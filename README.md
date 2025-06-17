# 🕸️ Liquipedia Scraper API

API بسيطة بلغة Python + Flask تقوم بجلب البطولات الخاصة بالألعاب (مثل Dota 2) من موقع [Liquipedia](https://liquipedia.net).

---

## 📦 المميزات

- ✅ جلب البطولات المقسّمة إلى:
  - Ongoing (جارية)
  - Upcoming (قادمة)
  - Completed (منتهية)
- ✅ تخزين البيانات مؤقتًا (Cache) لتقليل عدد الطلبات.
- ✅ خيار "force" لتحديث البيانات وتجاهل الكاش.
- ✅ كود بسيط وقابل للتعديل.

---

## 🔧 كيفية التشغيل محليًا

```bash
git clone https://github.com/mahmoodhamdi/scraper_api.git
cd scraper_api
pip install -r requirements.txt
python app.py
````

---

## 🧪 اختبار الـ API

### 📍 Endpoint

```post
POST /api/tournaments
```

### 📤 Request Body (JSON)

| Key   | Type    | Required | Description                            |
| ----- | ------- | -------- | -------------------------------------- |
| game  | string  | ✅        | اختصار اسم اللعبة (مثلاً: `dota2`)     |
| force | boolean | ❌        | تجاهل الكاش وجلب جديد (افتراضي: false) |

### 🧾 مثال

```json
{
  "game": "dota2",
  "force": true
}
```

### 📥 Response (مثال مبسط)

```json
{
  "Ongoing": [...],
  "Upcoming": [...],
  "Completed": [...]
}
```

---

## 🗃️ نظام الكاش

* يتم تخزين النتائج في مجلد `cache/` لمدة 10 دقائق.
* إذا تم إرسال `force: true`، يتم تجاهل الكاش وجلب بيانات جديدة من Liquipedia.

---

## 🚀 النشر على PythonAnywhere

تم تجهيز سكربت `deploy.sh` يعمل على:

1. رفع التعديلات إلى GitHub.
2. الدخول على سيرفر PythonAnywhere عبر SSH.
3. سحب التحديثات وإعادة تشغيل التطبيق تلقائيًا.

---

## 📂 هيكل المشروع

```structure
scraper_api/
├── app.py
├── scraper/
│   └── liquipedia_scraper.py
├── cache/
│   └── (ملفات الكاش)
├── deploy.sh
└── README.md
```

---

## ✨ المستقبل

* دعم المزيد من الألعاب.
* إضافة تفاصيل أكثر عن كل بطولة.
* تخزين في قاعدة بيانات بدلًا من ملفات.

---

## 👨‍💻 من المطور؟

* 👤 Mahmoud Hamdy
* 📧 [hmdy7486@gmail.com](mailto:hmdy7486@gmail.com)
* 💻 Powered by: Python, Flask, BeautifulSoup

---
