# Parking Car Deduplication & Filter

Bu loyiha Filebrowser orqali saqlanadigan parkovka kameralaridan olingan rasmlarni YOLO modellari yordamida saralash (deduplikatsiya) uchun mo'ljallangan.

## Imkoniyatlari

- **YOLO orqali aniqlash:** `yolov8n.pt` orqali kadrlarda mashinalar bor-yo'qligi skanerlanadi.
- **Raqamni aniqlash:** `plate_yolo.pt` yordamida mashinaning old qismi va davlat raqami (plaka) bor kadrlar saralab olinadi. 
- **O'tishlarni (passage) ajratish:** Bitta mashinaning ketma-ket o'tgan bir nechta kadrlari o'rniga eng yaxshi bitta kadr (old qismi yaqqol ko'ringan kadr) qoldiriladi.
- **Masofadan boshqarish:** `sites.json` faylidan sayt URL, username va parollarni o'qib, Filebrowser API orqali masofadagi serverga bog'lanadi.

## Fayllar vazifasi

- `filter.py`: Berilgan sayt (parkovka) uchun rasmlarni yuklab, YOLO orqali mashinalarni topish va faqat old tomoni ko'ringan bitta kadrni qoldirish logikasini bajaradi. Yig'ilgan natijalar `output/` papkasiga saqlanadi.
- `upload.py`: Saralangan (deduplikatsiya qilingan) sifatli kadrlarni orqaga, Filebrowser serveridagi maxsus papkaga yuklab beradi.
- `sites.json`: Parkovka ob'ektlarining ro'yxati va ularga kirish huquqlari (URL, username, password).
- `*.pt` (yolov8n, plate_yolo): Og'irlik (weights) fayllari (Git-ga kiritilmagan).

## Ishlatilishi

1. Kerakli Python kutubxonalarini (YOLO, va h.k.) o'rnating.
2. `filter.py` orqali kerakli ob'ekt uchun rasmlarni saralang:
   ```bash
   python filter.py fayzobod
   ```
3. Tanlangan va ajratib olingan rasmlarni serverga qayta yuklang:
   ```bash
   python upload.py <host> <parol> <lokal_papka> <masofaviy_papka>
   ```

*Eslatma: `*.pt` modellar fayllari va yig'ilgan `output/` jildi hajmi katta bo'lganligi sababli, `.gitignore` orqali GitHub'ga yuklanishi cheklangan.*
