# Facebook Video Downloader | محمّل فيديوهات فيسبوك

Download all videos from any Facebook profile or page using Playwright + yt-dlp.

تحميل جميع فيديوهات أي بروفايل أو صفحة على فيسبوك باستخدام Playwright و yt-dlp.

---

## Requirements | المتطلبات

- Python 3.10+
- Internet connection | اتصال بالإنترنت

---

## Installation | التثبيت

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

---

## Usage | طريقة الاستخدام

### 1. Export cookies | تصدير الكوكيز

The program needs your Facebook login cookies to access videos.

البرنامج يحتاج كوكيز فيسبوك الخاصة بك للوصول للفيديوهات.

**Steps | الخطوات:**
1. Install the browser extension **"Get cookies.txt LOCALLY"** on Chrome
   ثبّت امتداد **"Get cookies.txt LOCALLY"** على Chrome
2. Open [facebook.com](https://facebook.com) and make sure you are logged in
   افتح [facebook.com](https://facebook.com) وتأكد أنك مسجّل دخول
3. Click the extension icon → **Export** → save the file as `cookies.txt` in the project folder
   اضغط على أيقونة الامتداد ← **Export** ← احفظ الملف باسم `cookies.txt` في مجلد المشروع

---

### 2. Run the program | تشغيل البرنامج

**Profile | بروفايل شخصي:**
```bash
python downloader.py "https://www.facebook.com/profile.php?id=100080241908021" --cookies cookies.txt
```

**Page | صفحة:**
```bash
python downloader.py "https://www.facebook.com/zadlibraryy" --cookies cookies.txt
```

**Custom output folder | تحديد مجلد الحفظ:**
```bash
python downloader.py "https://www.facebook.com/zadlibraryy" --cookies cookies.txt -o videos/zad
```

**More scrolls for large pages | زيادة السكرول لصفحات كبيرة:**
```bash
python downloader.py "https://www.facebook.com/zadlibraryy" --cookies cookies.txt --scrolls 80
```

---

## Options | الخيارات

| Option | Description | الوصف |
|--------|-------------|-------|
| `url` | Facebook profile or page URL | رابط البروفايل أو الصفحة |
| `--cookies` | Path to cookies.txt file | مسار ملف الكوكيز |
| `-o` | Output folder (default: `downloads/`) | مجلد الحفظ (افتراضي: `downloads/`) |
| `--scrolls` | Number of scroll attempts (default: 30) | عدد مرات السكرول (افتراضي: 30) |

---

## How it works | كيف يعمل

1. Opens the profile/page Videos tab using a headless browser with your cookies
   يفتح تبويب الفيديوهات باستخدام متصفح خفي مع كوكيزك
2. Scrolls down automatically to load all videos
   يسكرول تلقائياً لتحميل كل الفيديوهات
3. Collects all video URLs
   يجمع روابط جميع الفيديوهات
4. Downloads each video with yt-dlp in the best available quality
   يحمّل كل فيديو بأعلى جودة متاحة

---

## Notes | ملاحظات

- Videos are saved as MP4 files | الفيديوهات تُحفظ بصيغة MP4
- The `cookies.txt` file expires after some time — re-export it if you see login errors
  ملف `cookies.txt` ينتهي مفعوله بعد فترة — أعد تصديره إذا ظهرت أخطاء تسجيل دخول
- Use this tool responsibly and respect Facebook's Terms of Service
  استخدم الأداة بشكل مسؤول واحترم شروط خدمة فيسبوك
