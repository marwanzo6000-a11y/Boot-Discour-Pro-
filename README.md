# Discord Management Bot — Single File

نسخة موحدة من بوت إدارة Discord. كل أنظمة البوت موجودة داخل `main.py`.

## الأنظمة
- Ban / Kick / Timeout / Unban
- Clear / Lock / Unlock / Slowmode
- Warnings مع SQLite
- Welcome / Leave / Autorole
- Logs
- Tickets بأزرار Persistent
- Anti-Link / Anti-Spam / Anti-Mention
- Server / User info

## التشغيل
```bash
pip install -r requirements.txt
cp .env.example .env
# ضع توكن البوت داخل .env
python main.py
```

فعّل في Discord Developer Portal: **Server Members Intent** و **Message Content Intent**.

لا ترفع `.env` إلى GitHub.
