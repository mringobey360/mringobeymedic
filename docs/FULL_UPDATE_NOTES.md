# SmartPOS — Full Professional Update

Included in this package:
- Android/Termux-safe chat attachment storage (prevents `OSError: [Errno 38] Function not implemented` caused by `fcntl.flock` on shared storage).
- Debt editing with server + client phone normalization/validation.
- Professional receipt PDF with watermark, receipt number and barcode.
- Product Code-128 barcode generation and printable labels; POS search accepts barcode input.
- SMS wallet: 500 initial SMS credits per activated customer, usage tracking and exhaustion protection.
- Optional customer-owned Africa's Talking username, Sender ID and API key, only when System Owner enables access.
- Automatic fallback to default system SMS credentials when custom credentials are not enabled/complete.
- Debt reminder SMS is a Premium feature for subscribed customers and includes the pharmacy name.
- Customer SMS Centre showing granted/used/remaining SMS.
- System Owner screen to enable/disable custom SMS credential access.
- Chat attachment preview before sending, plus document attachments.
- Professional PDF copyright/watermark treatment.

## Termux / fresh local database

Do NOT delete migrations in an existing production database.

For a fresh local SQLite database:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py check
python manage.py runserver 0.0.0.0:8000
```

If the project is on Android shared storage (`~/storage/downloads/...`), this package specifically avoids Django's file locking for chat attachments. For production Railway, use a persistent volume or object storage for uploaded files.

## Existing production database

Back up first. Run `python manage.py makemigrations` and `python manage.py migrate` using the same codebase/database. Do not remove old migration files.
