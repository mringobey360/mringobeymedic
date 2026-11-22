# SmartPOS — Full Professional Update

## Design standard
- No emoji characters are used in the system UI.
- Use the existing inline SVG professional icon style for actions, status, navigation and attachments.
- Keep labels and icons consistent across modules.

## Local setup
For a fresh local SQLite database:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py seed_sms_packages
python manage.py check
python manage.py runserver 0.0.0.0:8000
```

Do not delete migration files in a production/data-bearing project.

## SMS architecture
- Standard SMS wallet: initial 500 SMS per customer owner.
- Premium Debt SMS is a separate paid subscription and separate quota.
- Default seeded Premium Debt SMS packages:
  - TZS 5,000 = 500 SMS / 30 days
  - TZS 10,000 = 10,000 SMS / 30 days
- Customers may select Premium Debt SMS during registration and pay it together with the main subscription, or activate it later from the Premium Debt SMS page.
- Combo activation happens automatically when the System Owner approves the signup payment.
- Later purchases activate automatically when the System Owner approves the in-system payment request.
- Actual mobile-money automatic payment verification requires a supported provider API/webhook integration; the current workflow intentionally requires admin verification of the submitted transaction reference.

## SMS credentials
- The System Owner can enable customer-owned SMS credentials.
- Customer fields: provider username, sender ID and API key.
- If custom access is disabled or credentials are incomplete, the system falls back to the default system SMS configuration.

## Chat attachments
- Attachments are previewed before send.
- Maximum attachment size is 10 MB.
- Android/Termux-safe storage avoids unsupported advisory file locking.
- Railway production should use persistent volume or object storage for uploaded media.


## V11 HOTFIX — Barcode Compatibility
- Restored `Product.effective_barcode`, which was referenced by barcode PDF, product list, and POS APIs but missing from the Product model.
- Added a stable fallback barcode for legacy products whose `barcode` field is blank.
- This fixes `AttributeError: 'Product' object has no attribute 'effective_barcode'` at `/inventory/<id>/barcode/`.
