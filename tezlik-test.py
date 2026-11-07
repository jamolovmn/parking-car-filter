"""TEZLIK TESTI — mashina filterlash kompyuteringizda necha soat olishini
AVVALDAN hisoblab beradi. Hech narsa o'rnatish kerak emas (faqat Python).

Ishlatish:
    python tezlik-test.py

Manzil/parolni shu papkadagi sites.json dan oladi. U yo'q bo'lsa so'raydi.
PAROL KODGA YOZILMAYDI.
"""
import json, os, sys, time, getpass, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

CAM = "dataset/camera_enter_1"


def credentials():
    """sites.json dan birinchi saytni oladi; bo'lmasa so'raydi."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sites.json")
    if os.path.isfile(p):
        try:
            s = json.load(open(p, encoding="utf-8"))
            name = sys.argv[1] if len(sys.argv) > 1 else sorted(s)[0]
            v = s[name]
            print(f"sites.json dan olindi: {name}")
            return v["host"].rstrip("/"), v.get("user", "admin"), v["password"]
        except Exception as e:
            print(f"sites.json o'qilmadi ({e}) — qo'lda so'rayman")
    host = input("manzil (masalan https://241.app.ioedge.host): ").strip().rstrip("/")
    user = input("login [admin]: ").strip() or "admin"
    pwd = getpass.getpass("parol (yozilayotgani ko'rinmaydi): ")
    return host, user, pwd


HOST, USER, PWD = credentials()


def login():
    d = json.dumps({"username": USER, "password": PWD}).encode()
    r = urllib.request.Request(HOST + "/api/login", data=d,
                               headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(r, timeout=20).read().decode()


def get(path, tok, to=60):
    r = urllib.request.Request(HOST + path, headers={"X-Auth": tok})
    return urllib.request.urlopen(r, timeout=to).read()


print("serverga ulanmoqda...")
try:
    tok = login()
except Exception as e:
    print(f"ULANMADI: {e}\n  - manzil to'g'rimi?\n  - login/parol to'g'rimi?")
    sys.exit(1)

items = json.loads(get("/api/resources/" + urllib.parse.quote(CAM), tok))["items"]
names = [i["name"] for i in items if not i.get("isDir")]
print(f"ulandi. {CAM} ichida {len(names)} ta rasm bor\n")


def one(nm):
    try:
        return len(get("/api/preview/thumb/" + urllib.parse.quote(CAM + "/" + nm), tok))
    except Exception:
        return 0


t0 = time.perf_counter()
with ThreadPoolExecutor(max_workers=16) as ex:
    r = list(ex.map(one, names[:200]))
el = time.perf_counter() - t0
sp, kbs = 200 / el, sum(r) / 1024 / el

print(f"TEZLIGINGIZ: {sp:.1f} rasm/s   ({kbs:.0f} KB/s = {kbs*8/1024:.1f} Mbit/s)")
print(f"VPS da     : 20.4 rasm/s   (470 KB/s = 3.7 Mbit/s)")
print(f"-> kompyuteringiz VPS dan {sp/20.4:.1f} barobar {'TEZ' if sp > 20.4 else 'SEKIN'}\n")

# O'lchangan koeffitsientlar (2026-11-07, 241-sayt):
TOTAL = 100921       # 3 kameradagi jami rasm
PASS_RATE = 0.006    # ~167 rasmda bitta mashina o'tishi
BIG_KB, BIG_N = 410, 10     # har o'tishga 10 ta katta rasm, ~410 KB
th = TOTAL / sp / 3600
bh = (TOTAL * PASS_RATE * BIG_N * BIG_KB) / kbs / 3600
print(f"100 921 rasmli sayt uchun BAHO:")
print(f"  kichik rasmlar : {th:4.1f} soat")
print(f"  katta rasmlar  : {bh:4.1f} soat")
print(f"  JAMI           : {th + bh:4.1f} soat")
print(f"\n(VPS da shu ish ~2.5 soat; eski kod bilan 11 soat edi)")
