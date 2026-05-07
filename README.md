# Git Live Browser

مرورگر زنده برای Codespaces + VS Code Desktop. این پروژه برخلاف نسخه GitHub.io/Actions، تصویر مرورگر را با CDP Screencast و WebSocket از داخل Codespace به صفحه محلی شما می‌فرستد.

## اجرا

1. این repo را در GitHub بسازید و فایل‌ها را push کنید.
2. با VS Code Desktop به Codespace وصل شوید.
3. ترمینال Codespace را باز کنید و اجرا کنید:

```bash
bash scripts/start-live.sh
```

4. پورت `8787` را در VS Code Desktop روی حالت Private forward کنید.
5. در مرورگر سیستم خودتان باز کنید:

```text
http://127.0.0.1:8787
```

## قابلیت‌ها

- تصویر زنده از Chromium با CDP Screencast + WebSocket binary
- کلیک مستقیم روی تصویر
- اسکرول، برگشت، جلو، رفرش
- تایپ متن و ارسال Enter
- دانلود واقعی فایل‌ها در پوشه `downloads/`
- آپلود فایل‌های دانلودشده به GitHub Releases، در صورت تنظیم `GH_TOKEN` و `REPO`

## متغیرهای اختیاری برای Release Upload

```bash
export GH_TOKEN=ghp_...
export REPO=username/repo
```

Release tag پیش‌فرض:

```text
browser-downloads
```

## نکته امنیتی

از ورود به حساب‌های خیلی حساس مثل بانک، پرداخت، حساب اصلی Google و سرویس‌های مالی داخل مرورگر اتومات‌شده خودداری کنید.


## نسخه CDP Screencast

این نسخه به‌جای loop گرفتن `page.screenshot()`، از Chrome DevTools Protocol `Page.startScreencast` استفاده می‌کند. فریم‌ها به‌صورت binary روی WebSocket فرستاده می‌شوند و اگر کلاینت عقب بیفتد، فریم‌های قدیمی drop می‌شوند تا تصویر زنده نماند پشت صف.

اگر روی بعضی محیط‌ها CDP Screencast در دسترس نبود، برنامه خودکار به screenshot fallback برمی‌گردد.
