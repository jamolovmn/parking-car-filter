# parking-car-filter

Parking kameralarining arxividan **mashinalarni ajratib oladi** va har mashinadan
**raqami eng yaxshi ko'ringan bitta kadrni** to'liq o'lchamda saqlaydi.

Kameralar har soniyada kadr yozadi — bitta saytda 100-160 ming rasm to'planadi.
Ularning ko'pi bo'sh yo'l yoki bir mashinaning o'nlab takror kadri. Bu dastur
shundan faqat kerakligini qoldiradi: har o'tgan mashinadan **1 ta rasm**.

Raqamni **O'QIMAYDI** (OCR yo'q) — faqat raqam ko'rinayotgan kadrni tanlaydi.

---

## Qanday ishlaydi

```
  kichik rasm (thumb, 7-27 KB)
        │
        ▼
  YOLOv8n ──► kadrda mashina bormi? (256px, CONF=0.40, maydon ≥ 5%)
        │
        ▼
  4 ta ketma-ket bo'sh kadr  =  "mashina o'tdi"  (GAP_FRAMES)
        │
        ▼
  o'sha mashinaning 40 kadri KATTA holda yuklanadi (MAX_PASSAGE_CHECK)
        │
        ▼
  plate_yolo ──► har kadrda raqam qidiradi, eng ishonchlisini tanlaydi
        │
        ▼
  ishonch ≥ PLATE_TH (0.10) bo'lsa → o'sha kadr TO'LIQ o'lchamda saqlanadi
  bo'lmasa → bu mashina tashlanadi (uzoq/yon kadr, raqam ko'rinmagan)
```

Natija: `output/<sayt>/<kamera>/selected/*.png`

---

## O'rnatish

Kerak: **Python 3.9+**. GPU shart emas — CPU yetadi.

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install ultralytics pillow
```

Modellar repoda: `yolov8n.pt` (mashina) va `plate_yolo.pt` (raqam).
Qo'shimcha yuklab olish kerak emas.

> `.gitignore` da `*.pt` bor edi — modellar repoga **ataylab** qo'shildi,
> chunki ularsiz dastur ishlamaydi.

---

## Sozlash

`sites.json` — qaysi parkingdan yig'ish:

```json
{
  "minor": {
    "host": "https://275.app.ioedge.host",
    "user": "admin",
    "password": "PAROL"
  }
}
```

**`host` faqat manzil bo'lsin.** Brauzerdan ko'chirilgan
`https://275.app.ioedge.host/login?redirect=/files` **ishlamaydi** —
API yo'li buziladi va `404` keladi.

---

## Ishga tushirish

```bash
python filter.py minor
```

Natija:

```
output/minor/camera_enter_1/selected/*.png     ← tanlangan mashinalar
output/minor/scan.log                           ← jarayon logi
```

Log shunday ko'rinadi:

```
[15:18:37]   dataset/camera_enter_1: 5000/67114 (19/s) | mashina: 11
```

**Uzilsa** — xuddi shu buyruqni qayta bering. Tugagan kameralarni
(`.done` belgisi) o'tkazib yuboradi. Tugamagan kamerani **boshidan**
boshlaydi — joyni kamera darajasida eslaydi, har rasm darajasida emas.

### Tezlikni oldindan bilish

```bash
python tezlik-test.py
```

Hech narsa o'rnatmasdan, 200 ta rasmni yuklab tezligingizni o'lchaydi va
sayt uchun necha soat ketishini aytadi.

---

## Sozlamalar

Fayl boshida yoki muhit o'zgaruvchisi orqali:

| nom | standart | ma'nosi |
|---|---|---|
| `MAX_SELECT` | 200 | har kameradan maksimal mashina |
| `MAX_PASSAGE_CHECK` | 40 | har mashinada raqamga tekshiriladigan kadr |
| `PLATE_TH` | 0.10 | shundan past = "raqam yo'q", mashina tashlanadi |
| `CONF` | 0.40 | mashina aniqlash ishonchi |
| `MIN_AREA` | 0.05 | kadrdagi eng kichik mashina maydoni |
| `GAP_FRAMES` | 4 | shuncha bo'sh kadr = mashina o'tdi |
| `DL_WORKERS` | 16 | parallel yuklovchilar |
| `MAX_OUTPUT_GB` | 50 | disk to'lsa pauza qiladi |

```bash
# Linux/Mac
MAX_SELECT=300 python filter.py minor
# Windows
set MAX_SELECT=300 && python filter.py minor
```

---

## O'lchangan ko'rsatkichlar

2026-11-07 da olingan (VPS, 0.9 yadro cheklovi bilan):

| | 241 (beruniy) | 275 (minor) |
|---|---|---|
| rasm soni | 100 921 | 158 642 |
| kichik rasm | 27 KB | 7 KB |
| katta rasm | 410 KB | 91 KB |
| yuklash tezligi | 18-20 rasm/s | 19-27 rasm/s |
| YOLO (CPU, 256px) | 26 rasm/s | — |
| **to'liq skan** | **~11 soat** | — |

**Cheklov — kanal, protsessor emas.** O'lchov: keshlangan rasmni qayta
so'raganda ham tezlik o'zgarmadi (18 rasm/s), ya'ni server thumbnail
yasashga ulgurmayotgani emas — kanal 4-5 Mbit/s da to'yingan.

Shuning uchun **kuchliroq protsessor yordam bermaydi**. Tezlashtirishning
yagona yo'li — kamera serveriga yaqinroq joydan (masalan o'sha shahardagi
kompyuterdan) ishlatish.

| kanal tezligi | 100 000 rasm |
|---|---|
| 4 Mbit/s | ~11 soat |
| 20 Mbit/s | ~2.5 soat |
| 50 Mbit/s | ~1 soat |

---

## Ma'lum kamchiliklar

- **Bitta mashina bir necha marta saqlanishi mumkin.** Kechasi qora mashina
  ba'zi kadrlarda aniqlanmaydi; 4 ta shunday kadr "mashina o'tdi" deb
  hisoblanadi va bitta o'tish ikkiga bo'linadi.
- **Uzilgan kamera boshidan boshlanadi** — joy faqat kamera darajasida
  eslanadi.
- `PLATE_TH=0.10` past: raqam ko'rinmagan kadrlar ham o'tib ketishi mumkin
  (o'lchangan: 0.40 ishonchli kadrda ham raqam bo'lmasligi mumkin, 0.75 da
  toza o'qiladi). Chegarani ko'tarish mashina sonini kamaytiradi.
