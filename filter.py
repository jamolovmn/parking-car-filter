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
import sys, os, io, json, time, queue, threading, itertools, urllib.request, urllib.parse
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
# 2026-11-07 O'LCHANDI: 0.10 juda past edi — raqam KO'RINMAYDIGAN kadrlar
# o'tib ketardi. Ko'z bilan tekshirildi: 0.403 -> rasmda raqam YO'Q (mashina
# yon tomoni), 0.758 -> raqam toza o'qiladi ("01 K 477 TC"). Shuning uchun
# chegara 0.50. Natija: raqami ko'rinmagan mashina SAQLANMAYDI.
# Juda kam mashina chiqsa -> PLATE_TH=0.35 qilib ko'ring.
PLATE_TH    = float(os.environ.get("PLATE_TH", "0.50"))
# 2026-11-07: 40 -> 10. AVVAL 40 ga qaytargan edim ("raqam ko'rinmaydi"
# deb), lekin O'LCHOV buni RAD ETDI: 40 kadr ham, 10 kadr ham AYNAN bir
# xil kadrni tanladi (5/5), ishonchlar ham bir xil. Ya'ni 40 kadr sifat
# bermay, tezlikni 19 -> 7 rasm/s ga tushirardi. Sifat muammosi kadr
# sonida emas, PLATE_TH chegarasida edi (pastga qarang).
MAX_PASSAGE_CHECK = int(os.environ.get("MAX_PASSAGE_CHECK", "10"))
IMGSZ     = 256
VEHICLE   = {2, 3, 5, 7}
DL_WORKERS = 16
# 2026-11-07: 16 -> 256. Kichik bufer kanalni bo'sh qoldirardi
# (256 x 27 KB = ~7 MB xotira, arzon).
PREFETCH  = 256
# 2026-11-04: egasining talabi bilan 50 -> 200 (har kameradan max mashina).
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


def _get(url, token, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"X-Auth": token})
            return urllib.request.urlopen(req, timeout=20).read()  # tez aniqlash
        except Exception:
            if i == retries - 1:
                return None
            time.sleep(1)


def list_dir(host, token, path):
    raw = _get(host + "/api/resources/" + urllib.parse.quote(path), token)
    return json.loads(raw) if raw else {"items": []}


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


def scan_camera(host, token, folder, seldir, model, plate_model, Image, logf):
    """Bitta kamera papkasini skan qiladi. Tanlangan mashinalar sonini qaytaradi."""
    os.makedirs(seldir, exist_ok=True)
    names = list_images(host, token, folder)
    total = len(names)
    log(f"  {folder}: {total} ta rasm", logf)

    q = queue.Queue(maxsize=PREFETCH)
    stop_event = threading.Event()

    def producer():
        # 2026-11-07 O'LCHANGAN TUZATISH: avval rasmlar 16 talik "deraza" bilan
        # olinardi va har derazada ENG SEKINI kutilardi — o'sha paytda kanal
        # ham, CPU ham bo'sh turardi. Bitta sayt uchun toza yuklash ~4,5 soat
        # bo'lgani holda skan ~11 soat ketgan: ~6,5 soat shu kutishga ketgan.
        #
        # Endi uzluksiz: har yuklovchi bo'shashi bilan keyingi rasmni oladi.
        # MUHIM: mashina o'tishini aniqlash KETMA-KETLIKKA bog'liq (4 ta
        # ketma-ket bo'sh kadr = mashina o'tdi), shuning uchun natijalar
        # tartibsiz kelsa ham, bu yerda TARTIB TIKLANADI (reorder bufer).
        idx = itertools.count()
        names_n = len(names)
        ready = {}                      # indeks -> (nom, ma'lumot)
        lock = threading.Lock()
        nxt = 0                         # navbatdagi (kutilayotgan) indeks

        def worker():
            nonlocal nxt
            while not stop_event.is_set():
                i = next(idx)
                if i >= names_n:
                    return
                nm = names[i]
                try:
                    data = thumb(host, token, folder, nm)
                except Exception:
                    data = None
                # Qulf FAQAT lug'at uchun: q.put() navbat to'lganda bloklaydi,
                # shuning uchun uni qulfdan TASHQARIDA bajaramiz — aks holda
                # bitta kutayotgan yuklovchi qolgan 15 tasini to'xtatib qo'yadi.
                out = []
                with lock:
                    ready[i] = (nm, data)
                    while nxt in ready:
                        out.append(ready.pop(nxt))
                        nxt += 1
                for item in out:
                    q.put(item)                     # (nom, ma'lumot) — eski shakl

        ths = [threading.Thread(target=worker, daemon=True)
               for _ in range(DL_WORKERS)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        q.put(None)
    threading.Thread(target=producer, daemon=True).start()

    active = False; pf = []; gap = 0; n_cars = 0; n_done = 0; fails = 0; n_pass = 0
    t0 = time.time()

    def close_passage():
        # Mashinaning kadrlarini RAQAM-DETEKTOR bilan tekshiramiz:
        #   - har kadrda raqam (plaka) bormi?
        #   - eng yaxshi (ishonchi eng yuqori) raqamli kadrni saqlaymiz
        #   - hech qaysi kadrda raqam yo'q bo'lsa — bu mashina TASHLANADI
        # Bu kamera burchagidan MUSTAQIL (uzoq/yon kadrlarda raqam yo'q → o'zi tushib qoladi).
        nonlocal n_cars, pf, active, n_pass
        if pf:
            n_pass += 1                          # bitta mashina o'tishi topildi
            disk_guard(logf)                    # disk to'lsa — pauza
            names = [p[0] for p in pf]
            if len(names) > MAX_PASSAGE_CHECK:          # juda uzun bo'lsa — teng tanlab olamiz
                step = len(names) / MAX_PASSAGE_CHECK
                names = [names[int(i * step)] for i in range(MAX_PASSAGE_CHECK)]
            # big-previewlarni PARALLEL yuklaymiz (raqam thumbnailda juda kichik)
            def _dl(nm):
                data = bigprev(host, token, folder, nm)
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
                data = full(host, token, folder, best[1])
                if data:
                    with open(os.path.join(seldir, best[1]), "wb") as fo:
                        fo.write(data)
                    n_cars += 1
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
        if raw is None:
            fails += 1
            if fails >= 100:        # server uzilgan — kamerani tashlab, keyin avto qayta urinadi
                stop_event.set()
                log(f"  {folder}: tarmoq uzildi — to'xtatildi (server qaytsa avto davom etadi)", logf)
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
            # n_pass = topilgan o'tishlar soni. MUHIM: raqami ko'rinmagan
            # o'tish ham TASHLANADI, lekin katta rasmlar u uchun ham yuklanadi.
            # Shuning uchun haqiqiy yuk n_cars emas, n_pass bo'yicha o'lchanadi.
            log(f"  {folder}: {n_done}/{total} ({sp:.0f}/s) | "
                f"o'tish: {n_pass} | mashina: {n_cars}", logf)
    if active:
        close_passage()
    log(f"  {folder}: ✓ {n_cars} ta mashina (to'liq skan qilindi)", logf)
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
        token = login(host, user, pwd)
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
