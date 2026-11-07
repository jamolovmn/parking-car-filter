#!/usr/bin/env python3
"""
Bitta SAYT (parkovka) uchun: sites.json dan host+parol o'qiydi → filebrowser'dagi
`dataset/` ichidagi HAMMA kamerani topib, har biridan max MAX_SELECT ta MASHINA (standart 200, old tomoni
ko'ringan) kadrini ajratadi.

Ishlatish:  python filter.py <site>        (masalan: python filter.py fayzobod)

Mantiq (har kamera uchun):
  - thumbnail (kichik) bilan tez skan → YOLO mashina bor-yo'qligi
  - ketma-ket mashinali kadrlar = bitta "o'tish" (passage)
  - har o'tishdan FAQAT old tomoni ko'ringan 1 kadr (kadr tepasidagi = old) saqlanadi
  - MAX_SELECT ta mashina to'plangach o'sha kameradan to'xtaydi
"""
import sys, os, io, json, time, queue, threading, urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor

BASE       = os.path.dirname(os.path.abspath(__file__))
SITES_FILE = os.path.join(BASE, "sites.json")
OUTBASE    = os.path.join(BASE, "output")
DATASET    = "dataset"          # filebrowser ichidagi asosiy papka

CONF      = 0.40
MIN_AREA  = 0.05    # "mashina bor" deb hisoblash uchun eng kichik maydon
GAP_FRAMES = 4      # shuncha ketma-ket bo'sh kadr = mashina o'tdi
# Raqam (plaka) detektori — har mashinaning kadrlarini tekshiradi (kamera burchagidan mustaqil)
PLATE_MODEL = "plate_yolo.pt"
# 0.45 sinab ko'rildi (raqam ko'rinmagan rasmlarni to'sish uchun), lekin
# natija juda kam bo'ldi — 67 114 rasmdan 2 ta. Egasining talabi bilan
# ESKI qiymatga qaytarildi. Qattiqroq kerak bo'lsa:
#     PLATE_TH=0.45 python filter.py <sayt>
PLATE_TH    = float(os.environ.get("PLATE_TH", "0.10"))
MAX_PASSAGE_CHECK = 40   # bir mashinada eng ko'pi shuncha kadr raqamga tekshiriladi
IMGSZ     = 256
# COCO sinflari: 2=car, 3=moto, 5=bus, 7=truck.
# FAQAT avtomobil sinab ko'rildi ({2}), lekin o'tish aniqlash ham shunga
# bog'lanib, mashinalar qo'ldan keta boshladi (kechasi YOLO ba'zan "truck"
# deb tasniflaydi). Eski holat qaytarildi — hamma transport.
# Faqat avtomobil kerak bo'lsa: VEHICLE_CLASSES="2" python filter.py <sayt>
VEHICLE   = {int(x) for x in os.environ.get("VEHICLE_CLASSES", "2,3,5,7").split(",") if x.strip()}
# 2026-11-07 O'LCHANDI: 16 parallel so'rov parking serverini bo'g'ib qo'yardi.
# Xato: HTTP 500 (server thumbnail yasay olmayapti).
# O'LCHOV: o'sha 200 ta fayl 4 parallel bilan 200/200 ochildi, 8-16 parallel
# bilan 500 beradi. Ya'ni ayb faylda ham, tokenda ham, kanalda ham emas —
# parking serveri (mini-PC) parallel yukka dosh berolmaydi va uzoq
# ishlagandan keyin yomonlashadi (~20 ming rasmdan keyin boshlanadi).
DL_WORKERS = int(os.environ.get("DL_WORKERS", "4"))
PREFETCH  = 16
# 2026-11-04: egasining talabi bilan 50 -> 200 (har kameradan max mashina).
# 2026-11-07 (egasining talabi): bitta mashina KETMA-KET bir necha marta
# saqlanib qolardi — kechasi mashina ba'zi kadrlarda aniqlanmaydi va bitta
# o'tish ikkiga bo'linadi. Endi mashina saqlangach, qisqa "sovish" vaqti
# kutiladi; shu vaqt ichidagi O'SHA TURDAGI mashina bir xil deb tashlanadi.
# Tur boshqa bo'lsa (masalan avtobus keldi) — darhol o'tadi, kutilmaydi.
COOLDOWN_SEC = float(os.environ.get("COOLDOWN_SEC", "20"))
# Server bo'g'ilganda qancha dam berish va necha marta urinish
PAUSE_SEC  = float(os.environ.get("PAUSE_SEC", "30"))
MAX_STALLS = int(os.environ.get("MAX_STALLS", "10"))
# 2026-11-07: parking serveri uzluksiz yukda HTTP 500 bera boshlaydi
# (qisqa sinovlarda 40/40 OK, lekin skan ishlaganda yiqiladi). Shuning
# uchun so'rov tezligi cheklanadi va har "bo'g'ilish"da YANA pasayadi.
MAX_RPS = float(os.environ.get("MAX_RPS", "8"))
VEHICLE_NAMES = {2: "car", 3: "moto", 5: "bus", 7: "truck"}

MAX_SELECT = int(os.environ.get("MAX_SELECT", "200"))
# Disk himoyasi: yig'ilgan rasmlar shuncha GB dan oshsa — yig'ish PAUZA
# (zip yuklab, eskilarni o'chirasiz → joy bo'shaydi → o'zi davom etadi).
MAX_OUTPUT_GB = int(os.environ.get("MAX_OUTPUT_GB", "50"))


def log(msg, fp=None):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if fp:
        fp.write(line + "\n"); fp.flush()


def _output_gb():
    """Yig'ilgan rasmlarning umumiy hajmi (GB)."""
    t = 0
    for root, _, files in os.walk(OUTBASE):
        for f in files:
            if f.lower().endswith((".png", ".jpg", ".jpeg")):
                try:
                    t += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    return t / (1024 ** 3)


def disk_guard(logf):
    """Rasmlar MAX_OUTPUT_GB dan oshsa — joy bo'shaguncha kutadi (pauza)."""
    warned = False
    while _output_gb() >= MAX_OUTPUT_GB:
        if not warned:
            log(f"⏸ DISK PAUZA: rasmlar {MAX_OUTPUT_GB} GB dan oshdi. "
                f"Zip yuklab, eski rasmlarni o'chiring — keyin o'zi davom etadi.", logf)
            warned = True
        time.sleep(60)


def load_site(name):
    with open(SITES_FILE) as f:
        sites = json.load(f)
    key = name.lower()
    if key not in sites:
        raise SystemExit(f"sayt topilmadi: {name} (sites.json)")
    s = sites[key]
    return s["host"].rstrip("/"), s.get("user", "admin"), s["password"]


def _clean_host(host):
    """Brauzerdan ko'chirilgan '/login?redirect=/files' qismini tashlaydi —
    aks holda API yo'li buziladi va 404 keladi (2026-11-04)."""
    import urllib.parse as _up
    h = (host or "").strip().rstrip("/")
    if not h.startswith("http"):
        h = "https://" + h
    u = _up.urlsplit(h)
    return f"{u.scheme}://{u.netloc}"


# 2026-11-07: token/sessiya muddati tugasa HAMMA so'rov xato bera boshlaydi
# va skan "tarmoq uzildi" deb to'xtardi. O'LCHANDI: ikkala mashinada ham
# ~21-22 ming rasmdan keyin uzildi, o'sha kadrlar esa YANGI token bilan
# darhol ochildi (30/30). Shuning uchun token global saqlanadi va
# xatolar ko'payganda QAYTA LOGIN qilinadi.
TOKEN = {"v": None}
CREDS = {"host": None, "user": None, "pwd": None}


def refresh_token(logf=None):
    """Qayta login qilib yangi token oladi. Muvaffaqiyatli bo'lsa True."""
    try:
        TOKEN["v"] = login(CREDS["host"], CREDS["user"], CREDS["pwd"])
        if logf:
            log("    token yangilandi", logf)
        return True
    except Exception as e:
        if logf:
            log(f"    token yangilanmadi: {type(e).__name__}", logf)
        return False


def login(host, user, pwd, retries=2):
    host = _clean_host(host)
    data = json.dumps({"username": user, "password": pwd}).encode()
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(host + "/api/login", data=data,
                                         headers={"Content-Type": "application/json"})
            return urllib.request.urlopen(req, timeout=10).read().decode()  # tez aniqlash
        except Exception as e:
            last = e
            time.sleep(2)
    raise last     # server uzilgan — sayt keyin avto qayta urinadi


LAST_ERR = {"v": None}      # oxirgi xato sababi (log uchun) — 2026-11-07

# ── So'rov tezligini cheklovchi (hamma so'rovlar shu yerdan o'tadi) ──
_rl = {"rps": None, "next": 0.0, "lock": threading.Lock()}


def rate_wait():
    """Sekundiga MAX_RPS dan ko'p so'rov yubormaslik uchun kutadi."""
    rps = _rl["rps"] or MAX_RPS
    if rps <= 0:
        return
    gap = 1.0 / rps
    with _rl["lock"]:
        now = time.monotonic()
        wait = _rl["next"] - now
        _rl["next"] = max(now, _rl["next"]) + gap
    if wait > 0:
        time.sleep(wait)


def slow_down(logf=None):
    """Server bo'g'ilganda tezlikni ikki barobar pasaytiradi (eng kami 1/s)."""
    cur = _rl["rps"] or MAX_RPS
    _rl["rps"] = max(1.0, cur / 2)
    if logf:
        log(f"    tezlik pasaytirildi: {cur:.1f} -> {_rl['rps']:.1f} so'rov/s", logf)


def _get(url, token, retries=3):
    for i in range(retries):
        try:
            rate_wait()
            req = urllib.request.Request(url, headers={"X-Auth": token})
            return urllib.request.urlopen(req, timeout=20).read()  # tez aniqlash
        except Exception as e:
            code = getattr(e, "code", "")
            kind = ("thumb" if "/preview/thumb/" in url else
                    "big" if "/preview/big/" in url else
                    "raw" if "/api/raw/" in url else "boshqa")
            LAST_ERR["v"] = f"{kind}:{type(e).__name__}{code}"
            # 2026-11-07 MUHIM FARQ:
            #   server JAVOB berib xato qaytardi (HTTP 4xx/5xx) = SHU FAYL
            #     buzuq/o'qilmaydi -> b"" qaytaramiz, skan uni tashlab ketadi
            #   umuman javob yo'q (timeout/ulanmadi) = TARMOQ muammosi
            #     -> None qaytaramiz, skan dam olib qayta uradi
            # Avval ikkisi ham None edi va buzuq fayllar bloki (o'lchandi:
            # minor/camera_enter_1 da 22048-22600 oralig'idagi walpd_* fayllar
            # HTTP 500 beradi) "tarmoq uzildi" deb skanni to'xtatardi.
            # 401/403 = token eskirgan -> None (yangilash mantig'i ishlasin)
            if isinstance(e, urllib.error.HTTPError) and 500 <= e.code < 600:
                return b""
            if i == retries - 1:
                return None
            time.sleep(1)


def list_dir(host, token, path, timeout=180, retries=4):
    """Papka ro'yxati. KATTA papkada javob 15 MB bo'ladi va 20 soniyaga
    ulgurmaydi — 2026-11-07 da aynan shu sabab camera_exit_1 (41 099 rasm)
    va camera_exit_2 (50 429 rasm) "0 ta rasm" deb o'tkazib yuborilgan.
    Shuning uchun bu yerda timeout uzun va urinishlar ko'p."""
    url = host + "/api/resources/" + urllib.parse.quote(path)
    for i in range(retries):
        try:
            rate_wait()
            req = urllib.request.Request(url, headers={"X-Auth": token})
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            if raw:
                return json.loads(raw)
        except Exception as e:
            LAST_ERR["v"] = f"ro'yxat:{type(e).__name__}{getattr(e,'code','')}"
            time.sleep(3 * (i + 1))
    return {"items": []}


def list_images(host, token, folder):
    d = list_dir(host, token, folder)
    imgs = []
    for it in d.get("items", []):
        n = it.get("name", "")
        if not it.get("isDir") and n.lower().endswith((".png", ".jpg", ".jpeg")):
            num = "".join(c for c in n if c.isdigit())
            imgs.append((int(num) if num else 0, n))
    imgs.sort()
    return [n for _, n in imgs]


def thumb(host, token, folder, name):
    return _get(host + "/api/preview/thumb/" + urllib.parse.quote(folder + "/" + name), token)


def bigprev(host, token, folder, name):
    """O'rta o'lcham (~0.5MB) — raqam (plaka) qidirish uchun (thumbnail juda kichik)."""
    return _get(host + "/api/preview/big/" + urllib.parse.quote(folder + "/" + name), token)


def full(host, token, folder, name):
    return _get(host + "/api/raw/" + urllib.parse.quote(folder + "/" + name), token)


def frame_ts(name):
    """Fayl nomidan vaqtni oladi: 'wavd_1793986287637735.png' -> 1793986287.637
    (16 xonali mikrosoniya). Topilmasa None."""
    import re
    m = re.search(r"(\d{13,19})", name)
    if not m:
        return None
    d = m.group(1)
    return int(d) / (10 ** (len(d) - 10))


def scan_camera(host, token, folder, seldir, model, plate_model, Image, logf):
    """Bitta kamera papkasini skan qiladi. Tanlangan mashinalar sonini qaytaradi."""
    os.makedirs(seldir, exist_ok=True)
    all_names = list_images(host, token, folder)
    total = len(all_names)

    # 2026-11-07 (egasining talabi): tarmoq uzilsa kamera BOSHIDAN
    # boshlanardi — 21 500 rasmlik ish yo'qolgan edi. Endi joy faylga
    # yozib boriladi va uzilgandan keyin O'SHA JOYDAN davom etadi.
    # Joy faqat o'tish YOPIQ paytda yoziladi, aks holda yarim mashina
    # ikkiga bo'linib qolardi.
    pfile = os.path.join(os.path.dirname(seldir), "progress.txt")
    start = 0
    try:
        with open(pfile, encoding="utf-8") as f:
            start = max(0, min(int(f.read().strip()), total))
    except (OSError, ValueError):
        start = 0
    if total == 0:
        # Ro'yxat olinmadi (yoki papka haqiqatan bo'sh). TUGADI deb
        # belgilamaymiz — aks holda minglab rasm jimgina o'tkazib yuboriladi.
        log(f"  {folder}: ro'yxat BO'SH keldi ({LAST_ERR['v']}) — "
            f"kamera tugadi deb belgilanmaydi, keyin qayta urinamiz", logf)
        return False

    names = all_names[start:]
    if start:
        log(f"  {folder}: {total} ta rasm — {start}-dan DAVOM etamiz "
            f"(qolgani {len(names)})", logf)
    else:
        log(f"  {folder}: {total} ta rasm", logf)

    q = queue.Queue(maxsize=PREFETCH)
    stop_event = threading.Event()

    def producer():
        with ThreadPoolExecutor(max_workers=DL_WORKERS) as ex:
            i = 0
            while i < len(names) and not stop_event.is_set():
                window = names[i:i + DL_WORKERS]
                for r in ex.map(lambda nm: (nm, thumb(host, TOKEN["v"], folder, nm)), window):
                    q.put(r)
                i += DL_WORKERS
        q.put(None)
    threading.Thread(target=producer, daemon=True).start()

    active = False; pf = []; gap = 0; n_cars = 0; n_done = 0; fails = 0; n_pass = 0
    t0 = time.time()
    stalls = [0]            # server bo'g'ilib qolgan holatlar soni
    n_bad = 0               # o'qib bo'lmagan (buzuq) fayllar
    last_ts = None          # oxirgi SAQLANGAN mashinaning kadr vaqti
    last_kind = None        # oxirgi saqlangan mashinaning turi (car/bus/...)
    n_dup = 0               # takror deb tashlanganlar

    def close_passage():
        # Mashinaning kadrlarini RAQAM-DETEKTOR bilan tekshiramiz:
        #   - har kadrda raqam (plaka) bormi?
        #   - eng yaxshi (ishonchi eng yuqori) raqamli kadrni saqlaymiz
        #   - hech qaysi kadrda raqam yo'q bo'lsa — bu mashina TASHLANADI
        # Bu kamera burchagidan MUSTAQIL (uzoq/yon kadrlarda raqam yo'q → o'zi tushib qoladi).
        nonlocal n_cars, pf, active, n_pass, last_ts, last_kind, n_dup
        if pf:
            n_pass += 1                          # bitta mashina o'tishi topildi
            disk_guard(logf)                    # disk to'lsa — pauza
            names = [p[0] for p in pf]
            if len(names) > MAX_PASSAGE_CHECK:          # juda uzun bo'lsa — teng tanlab olamiz
                step = len(names) / MAX_PASSAGE_CHECK
                names = [names[int(i * step)] for i in range(MAX_PASSAGE_CHECK)]
            # big-previewlarni PARALLEL yuklaymiz (raqam thumbnailda juda kichik)
            def _dl(nm):
                data = bigprev(host, TOKEN["v"], folder, nm)
                try:
                    return nm, (Image.open(io.BytesIO(data)).convert("RGB") if data else None)
                except Exception:
                    return nm, None
            with ThreadPoolExecutor(max_workers=6) as ex:
                imgs = list(ex.map(_dl, names))
            # raqam inference KETMA-KET (model thread-safe bo'lishi uchun)
            best = (0.0, None)
            for nm, im in imgs:
                if im is None:
                    continue
                pr = plate_model.predict(im, conf=0.05, verbose=False)[0]   # past = ko'proq seziladi
                c = max([float(b.conf) for b in pr.boxes], default=0.0)
                if c > best[0]:
                    best = (c, nm)
            # RAQAM bor bo'lsa (≥ PLATE_TH) → raqam eng aniq ko'ringan kadrni saqlaymiz.
            # Yo'q bo'lsa (uzoq/yon/raqamsiz) → bu mashina TASHLANADI.
            if best[1] is not None and best[0] >= PLATE_TH:
                # Mashina TURI (eng katta mashina qutisining sinfi) — tanlangan
                # kadr bo'yicha aniqlanadi. Takrorni ajratish uchun kerak.
                kind = None
                bim = dict(imgs).get(best[1])
                if bim is not None:
                    vr = model.predict(bim, imgsz=IMGSZ, conf=CONF, verbose=False)[0]
                    bigbox = 0.0
                    for bb in vr.boxes:
                        cl = int(bb.cls)
                        if cl in VEHICLE:
                            x1, y1, x2, y2 = bb.xyxy[0].tolist()
                            ar = (x2 - x1) * (y2 - y1)
                            if ar > bigbox:
                                bigbox, kind = ar, VEHICLE_NAMES.get(cl, str(cl))

                # TAKROR TEKSHIRUVI: oxirgi saqlangandan beri COOLDOWN_SEC
                # o'tmagan bo'lsa VA turi o'sha bo'lsa -> bu o'sha mashina.
                ts = frame_ts(best[1])
                dup = False
                if (COOLDOWN_SEC > 0 and last_ts is not None and ts is not None
                        and (ts - last_ts) < COOLDOWN_SEC
                        and (kind == last_kind or kind is None or last_kind is None)):
                    dup = True

                if dup:
                    n_dup += 1
                    log(f"    takror tashlandi ({kind or '?'}, "
                        f"{ts - last_ts:.1f}s oldingisidan keyin)", logf)
                else:
                    data = full(host, TOKEN["v"], folder, best[1])
                    if data:
                        with open(os.path.join(seldir, best[1]), "wb") as fo:
                            fo.write(data)
                        n_cars += 1
                        last_ts, last_kind = ts, kind
                        # turini alohida faylga yozamiz (rasm nomi o'zgarmaydi)
                        try:
                            with open(os.path.join(seldir, "turlar.csv"), "a",
                                      encoding="utf-8") as tf:
                                tf.write(f"{best[1]},{kind or '?'},{best[0]:.3f}\n")
                        except OSError:
                            pass
        pf = []; active = False

    while True:
        if MAX_SELECT and n_cars >= MAX_SELECT:
            stop_event.set()
            log(f"  {folder}: ⏹ {MAX_SELECT} ta to'plandi — to'xtadik", logf)
            break
        item = q.get()
        if item is None:
            break
        name, raw = item
        n_done += 1
        if raw == b"":          # fayl buzuq (server HTTP xato berdi) — tashlaymiz
            n_bad += 1
            fails = 0
            if n_bad % 500 == 0:
                log(f"    {n_bad} ta fayl o'qilmadi (server xatosi) — tashlab ketilmoqda", logf)
            continue
        if raw is None:
            fails += 1
            # 30 ta ketma-ket xato = token eskirgan BO'LISHI MUMKIN.
            # Avval tokenni yangilab ko'ramiz — bu eng ko'p uchragan sabab.
            # Yangilash yordam bermasa, xatolar 100 ga yetadi va to'xtaymiz.
            # Server bo'g'ilgan bo'lishi mumkin — DAM BERAMIZ va davom etamiz.
            # Avval 100 xatoda butunlay to'xtardi; endi bir necha marta
            # "sovib" qayta urinadi (joy progress.txt da saqlanadi).
            if fails in (30, 60, 90):
                log(f"    {fails} ta xato ({LAST_ERR['v']}) — "
                    f"{PAUSE_SEC:.0f}s dam beramiz", logf)
                refresh_token(logf)
                time.sleep(PAUSE_SEC)
                continue
            if fails >= 100 and stalls[0] < MAX_STALLS:
                stalls[0] += 1
                try:
                    with open(pfile, "w", encoding="utf-8") as f:
                        f.write(str(max(0, start + n_done - 150)))
                except OSError:
                    pass
                slow_down(logf)
                log(f"    server javob bermayapti ({stalls[0]}/{MAX_STALLS}) — "
                    f"{PAUSE_SEC*4:.0f}s kutamiz", logf)
                time.sleep(PAUSE_SEC * 4)
                refresh_token(logf)
                fails = 0
                continue
            if fails >= 100:        # dam berish ham yordam bermadi
                stop_event.set()
                # Joyni darhol saqlaymiz — qayta ishga tushganda SHU YERDAN
                # davom etadi (avval kamera boshidan boshlanardi).
                try:
                    with open(pfile, "w", encoding="utf-8") as f:
                        f.write(str(max(0, start + n_done - 150)))
                except OSError:
                    pass
                log(f"  {folder}: tarmoq uzildi — to'xtatildi. "
                    f"Joy saqlandi: {max(0, start + n_done - 150)} "
                    f"(qayta ishga tushirsangiz SHU YERDAN davom etadi)", logf)
                return False
            continue
        fails = 0
        try:
            img = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception:
            continue
        r = model.predict(img, imgsz=IMGSZ, conf=CONF, verbose=False)[0]
        area = 0.0
        W, H = img.size
        for b in r.boxes:
            if int(b.cls) in VEHICLE:
                x1, y1, x2, y2 = b.xyxy[0].tolist()
                a = ((x2 - x1) * (y2 - y1)) / (W * H)
                if a > area:
                    area = a
        if area >= MIN_AREA:
            gap = 0; active = True
            pf.append((name, area))
        elif active:
            gap += 1
            if gap >= GAP_FRAMES:
                close_passage()
        if n_done % 500 == 0:
            sp = n_done / (time.time() - t0)
            log(f"  {folder}: {start + n_done}/{total} ({sp:.0f}/s) | "
                f"o'tish: {n_pass} | mashina: {n_cars} | takror: {n_dup}"
                f"{f' | buzuq: {n_bad}' if n_bad else ''}", logf)
            if not active:          # o'tish ochiq bo'lsa joyni yozmaymiz
                try:
                    with open(pfile, "w", encoding="utf-8") as f:
                        f.write(str(start + n_done))
                except OSError:
                    pass
    if active:
        close_passage()
    log(f"  {folder}: ✓ {n_cars} ta mashina (to'liq skan qilindi)", logf)
    try:
        os.remove(pfile)        # tugadi — joy belgisi kerak emas
    except OSError:
        pass
    return True     # kamera to'liq skan qilindi


def main():
    if len(sys.argv) < 2:
        print("Ishlatish: python filter.py <site>"); sys.exit(1)
    site = sys.argv[1].strip().lower()
    host, user, pwd = load_site(site)
    host = _clean_host(host)           # havola brauzerdan ko'chirilgan bo'lsa ham ishlaydi
    outdir = os.path.join(OUTBASE, site)
    os.makedirs(outdir, exist_ok=True)
    running = os.path.join(outdir, ".running")
    open(running, "w").close()
    logf = open(os.path.join(outdir, "scan.log"), "a", encoding="utf-8")
    try:
        log(f"=== SAYT: {site} ({host}) ===", logf)
        from PIL import Image
        # AVVAL login — server o'chiq bo'lsa, og'ir modellarni yuklamasdan tez chiqamiz (CPU tejaladi)
        CREDS.update(host=host, user=user, pwd=pwd)
        token = login(host, user, pwd)
        TOKEN["v"] = token
        from ultralytics import YOLO
        model = YOLO("yolov8n.pt"); model.fuse()           # mashina aniqlash
        plate_model = YOLO(PLATE_MODEL)                     # raqam (plaka) aniqlash
        cams = sorted(it["name"] for it in list_dir(host, token, DATASET).get("items", []) if it.get("isDir"))
        log(f"kameralar: {cams}", logf)
        # Hamma kamera ro'yxatini darrov yozamiz (UI ularni darrov ko'rsatadi)
        with open(os.path.join(outdir, "cameras.json"), "w") as f:
            json.dump(cams, f)
        current = os.path.join(outdir, "current.txt")
        for cam in cams:
            seldir = os.path.join(outdir, cam, "selected")
            cam_done = os.path.join(outdir, cam, ".done")
            # To'liq tugagan kamerani QAYTA skan qilmaymiz (joriy ishga tegmaymiz).
            if os.path.exists(cam_done):
                log(f"  {cam}: allaqachon tugagan — o'tkazildi", logf)
                continue
            # Allaqachon ≥ MAX_SELECT yig'ilgan bo'lsa — tugagan deb belgilab o'tkazamiz.
            have = len([f for f in os.listdir(seldir)
                        if f.lower().endswith((".png", ".jpg", ".jpeg"))]) if os.path.isdir(seldir) else 0
            if MAX_SELECT and have >= MAX_SELECT:
                open(cam_done, "w").close()
                log(f"  {cam}: allaqachon {have} ta bor — tugagan deb belgilandi", logf)
                continue
            os.makedirs(seldir, exist_ok=True)
            with open(current, "w") as f:
                f.write(cam)        # hozir qaysi kamera skan qilinmoqda
            ok = scan_camera(host, token, f"{DATASET}/{cam}", seldir, model, plate_model, Image, logf)
            if ok:
                open(cam_done, "w").close()      # bu kamera to'liq tugadi
            else:
                # server uzilgan — sayt tugamadi, keyin AVTO qayta urinadi (o'sha kameradan davom)
                log(f"  {site}: server uzilgan — keyin avto qayta urinadi", logf)
                break
        else:
            # break bo'lmadi = hamma kamera to'liq tugadi
            try:
                os.remove(current)
            except OSError:
                pass
            open(os.path.join(outdir, ".done"), "w").close()   # sayt to'liq tugadi
            log(f"=== {site}: HAMMASI TUGADI ===", logf)
    finally:
        try:
            os.remove(running)
        except OSError:
            pass
        logf.close()


if __name__ == "__main__":
    main()
