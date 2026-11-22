# SmartPOS — Production Release

## V46 — Marketplace seller store setup hotfix

- Fixed the production `NameError: name 'slugify' is not defined` when submitting `/marketplace/seller/store/setup/` by importing `slugify` from `django.utils.text`.
- Added a regression test to ensure the slug utility is available from the Marketplace views module.
- No database schema changes or migrations are required for this fix.


A multi-branch business management platform for Tanzania, built with Django 5.1, PostgreSQL, S3-compatible object storage, responsive POS, inventory/batch control, reports, SMS, debt management, customer registration, branch management and internal chat.

## Production architecture

- **Application:** Django 5.1 + Gunicorn
- **Hosting:** Railway
- **Database:** Railway PostgreSQL via `DATABASE_URL`
- **Uploaded media:** S3-compatible bucket (AWS S3, Cloudflare R2 or another compatible provider)
- **Static files:** WhiteNoise
- **Timezone:** `Africa/Dar_es_Salaam`
- **Production server:** Gunicorn with configurable workers/threads
- **Deployment:** Dockerfile + Railway

The application no longer treats Railway's ephemeral filesystem as a production database or media store. PostgreSQL is the production database and the configured object-storage bucket is the production media store.

## 1. Project structure

```text
PharmacyManager_TZ_Production_2026/
├── accounts/                 # users, roles, login, OTP, employees
├── pharmacies/               # branches and pharmacy branding
├── inventory/                # products, batches, packaging, WAC, FEFO
├── sales/                    # POS, sales, debts, receipts
├── dashboard/                # dashboards, analytics, reports and PDF exports
├── hq/                       # registrations, subscriptions and system-owner tools
├── sms_integration/          # SMS wallet, Premium Debt SMS and AT integration
├── notifications/            # in-app notifications
├── chat/                     # branch, manager and customer-group chat
├── core/                     # shared utilities, backups and demo seeding
├── templates/                # application templates
├── static/                   # application static assets and system logo
├── docs/                     # feature and release documentation
├── pharmacy_system/          # Django project settings / URLs / WSGI
├── Dockerfile                # reproducible Railway image
├── railway.json              # Railway service configuration
├── start.sh                  # production entrypoint
├── requirements.txt          # pinned Python dependencies
├── .env.example              # local environment reference
└── manage.py
```

## 2. Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py makemigrations
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

On Android/Termux, the local default storage backend is Android-safe and avoids the `fcntl/flock` issue that can occur on shared storage.

## 3B. One-command complete demo environment

Use exactly (development/demo only):

```bash
python manage.py seed_demo_data
```

Demo seeding is blocked when `DEBUG=False` unless `--force-demo` is explicitly supplied. Do not use demo seeding on the real production tenant.


This is intentionally a single orchestration command. It prepares:

- Demo Shop Owner / Supervisor
- Two independent branches
- Employees
- Categories and 2,000+ products per branch by default
- Opening stock and batches
- Packaging examples and purchase-cost logic
- Ordinary cash/mobile-money sales
- Debt sales and outstanding debts
- WAC-based cost/profit data
- HQ payment settings
- Monthly/yearly subscriptions
- Demo registration requests in multiple statuses
- Standard SMS wallets
- Premium Debt SMS packages and demo entitlement
- Customer SMS access records
- Demo notifications
- Branch chat
- All-branches manager chat
- System Owner & Customers chat
- Demo System Administrator

### Demo credentials

Shop Owner:

```text
Username: mringobey
Password: mringobey@123
```

Demo customer:

```text
Username: demo.customer
Password: DemoCustomer@123
```

Demo System Administrator:

```text
Username: demo.superadmin
Password: DemoAdmin@123
```

Employee temporary password:

```text
Employee@123
```

**These credentials are for local/demo testing only. Never use them in a real production environment.**

To reduce the demo dataset for a phone or quick smoke test:

```bash
python manage.py seed_demo_data --products-per-branch 100
```


## 3A. Import the real legacy database (production)

The supplied real legacy export is bundled at:

```text
core/fixtures/legacy/database.json
```

It is treated as **source-of-truth data**. The import does not regenerate product names, prices, stock, sales, employees, owner details, or branch details.

After deploying/migrating the application, run the following **once** on the production database:

```bash
python manage.py import_real_legacy_data
```

If the current database already contains the demo dataset and you want to replace it with the supplied real legacy database, take a database backup first, then run:

```bash
python manage.py import_real_legacy_data --replace-existing --yes-i-understand
```

The replacement mode clears current database rows and then loads the exact supplied `database.json`, so it should only be used when that legacy export is the intended production source of truth.

For the imported real owner `mringobey`, the command also configures the subscription/billing state without changing the legacy business data:

- Existing `mringobey` account is retained.
- Existing `Mringobey medics Paranga` and `Mringobey medics Kelema` branches are retained exactly and associated with the owner.
- A monthly subscription is marked **ACTIVE** for the current billing period; the next renewal is one month from the migration date.
- Paranga is treated as the included branch; Kelema is recorded as a paid branch add-on for the same current period.
- The owner receives **5,000 standard SMS credits** for the current period. No unprovided SMS payment amount is invented.
- A legacy signup record is created for HQ/Admin visibility because the supplied legacy database contains the real owner account but no `SignupRequest` record. Missing legacy permit/FIN fields remain blank rather than being fabricated.

The import command is idempotent for the billing setup and is intended for an explicit production migration, not automatic execution during every deployment.

## 4. Inventory model

The inventory engine is generic rather than tablet-specific.

Every product has a **base unit** used by stock and POS. A purchase can optionally use another unit and a conversion.

Example:

```text
Product: Paracetamol 500mg
Base unit: Tablet
Purchase unit: Box
Units per purchase unit: 100
Purchase quantity: 5
Purchase price per box: TSh 4,200
Selling price per tablet: TSh 100
```

The system calculates:

```text
5 boxes × 100 tablets = 500 tablets
TSh 4,200 ÷ 100 = TSh 42 cost/tablet
TSh 100 − TSh 42 = TSh 58 gross profit/tablet
```

The same model supports bottles, cartons, tubes, packs, pieces, pairs, sachets and other configured units. Purchase packaging is optional.

Current product reporting may use **Weighted Average Cost (WAC)**, while physical batch issuing follows **FEFO**. Each SaleItem now retains the exact purchase cost and selling price of the physical batch issued. If one POS sale spans batches with different prices, the sale is split into separate batch-aware SaleItems; the cashier sees the batch price breakdown and checkout is blocked if the live server price changed after the cart was priced.

## 5. Bulk registration

Bulk product import supports the normal product information plus optional purchase packaging:

```text
name
category
batch_number
unit
barcode
purchase_unit
units_per_purchase_unit
purchase_quantity
purchase_price
selling_price
reorder_level
expiry_date
```

Packaging fields are optional. A product bought directly as a base unit can use the same unit with conversion `1`.

## 6. Receipts

The receipt is designed for professional thermal printing with an **80mm roll width** and a dynamic height based on the amount of content.

It includes pharmacy-specific identity and contact information, pharmacy logo with system-logo fallback, FIN/permit information, receipt number, date/time, cashier, customer, payment method, item details, batch/expiry, totals, barcode, custom footer, system watermark and copyright.

The receipt height is dynamic, so a short sale does not produce a large blank page. The footer remains at the bottom of the generated receipt.

## 7. POS receipt flow

Completing a sale does not force navigation to the receipt page.

After a successful sale the POS offers a receipt choice. The user can:

- Print/View the receipt
- Continue selling
- Close the receipt choice and remain on POS

The completed cart is cleared only after the sale succeeds.

## 8. Railway deployment

### 8.1 Create services

Create a Railway project with:

1. A PostgreSQL service.
2. A web service connected to this repository.
3. An S3-compatible bucket for uploaded media.

Railway PostgreSQL supplies `DATABASE_URL` to the application service when the database is connected.

### 8.2 Required application variables

Set these on the Railway web service:

| Variable | Production value |
|---|---|
| `SECRET_KEY` | Long random secret |
| `DEBUG` | `False` |
| `TIME_ZONE` | `Africa/Dar_es_Salaam` |
| `RAILWAY_PUBLIC_DOMAIN` | Your Railway public domain, if not already exposed |
| `AWS_STORAGE_BUCKET_NAME` | Your bucket name |
| `AWS_ACCESS_KEY_ID` | Bucket access key |
| `AWS_SECRET_ACCESS_KEY` | Bucket secret key |
| `AWS_S3_ENDPOINT_URL` | S3/R2 endpoint when required |
| `AWS_S3_REGION_NAME` | `auto` for R2, or provider region |
| `AWS_S3_CUSTOM_DOMAIN` | Optional public CDN/custom media domain |
| `AWS_QUERYSTRING_AUTH` | `False` for public media URLs; use `True` when private signed URLs are required |

`DATABASE_URL` should come from the connected Railway PostgreSQL service. Do not paste database passwords into source files.

### 8.3 Cloudflare R2 / S3-compatible example

For an S3-compatible bucket, configure the equivalent of:

```text
AWS_STORAGE_BUCKET_NAME=your-bucket
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_S3_ENDPOINT_URL=https://<account-or-provider-endpoint>
AWS_S3_REGION_NAME=auto
AWS_S3_CUSTOM_DOMAIN=
AWS_QUERYSTRING_AUTH=False
```

Use the endpoint and credentials supplied by your storage provider. Do not commit them to Git.

### 8.4 Deploy

Railway will detect the included `Dockerfile` and build a reproducible application image. The image:

1. Installs the pinned dependencies.
2. Generates migrations for a fresh database image.
3. Collects static assets.
4. Starts `start.sh`.
5. Runs `python manage.py check --deploy`.
6. Runs `python manage.py migrate --noinput`.
7. Starts Gunicorn.

For a **new PostgreSQL database**, this gives a clean deployment path.

### 8.5 First production setup

After the deployment is healthy, create the real System Administrator:

```bash
python manage.py createsuperuser
```

Then log into Django Admin and configure:

- Africa's Talking settings
- Payment settings
- SMS controls
- Other platform-owner settings

Do not run `seed_demo_data` on a real production tenant unless you deliberately want demo records.

## 9. Important migration rule for production

Do not delete the production database or migration history.

For a fresh database, migrations can be generated during the Docker build as this project is currently distributed without committed migration files.

For an **existing production database that already contains business data**, schema changes must be handled deliberately. Generate and review normal Django migration files against the current codebase before deploying the schema change. Never solve a migration problem by deleting production migrations or the production database.

## 10. Backups

PostgreSQL is the production database, so use Railway/PostgreSQL backup facilities or an external PostgreSQL backup process as the authoritative database backup.

The application `backup_db` command creates a portable Django fixture plus media archive and now reads from the active storage backend, including an S3-compatible bucket. It is useful for application-level exports and recovery workflows, but it should not replace managed PostgreSQL backups.

```bash
python manage.py backup_db
```

Keep at least one backup outside the Railway project.

## 11. Security checklist before real customers

- Replace all demo credentials.
- Set a strong random `SECRET_KEY`.
- Keep `DEBUG=False`.
- Use Railway PostgreSQL, not production SQLite.
- Configure the media bucket with least-privilege credentials.
- Restrict bucket access according to your chosen public/private media policy.
- Configure `ALLOWED_HOSTS` / `RAILWAY_PUBLIC_DOMAIN` correctly.
- Configure `CSRF_TRUSTED_ORIGINS` if using a custom domain.
- Create a real System Administrator account.
- Configure Africa's Talking credentials only through the appropriate admin settings.
- Do not commit `.env` or credentials.
- Test registration, activation, POS, receipts, expiry, WAC, debt SMS, chat attachments and backups before accepting real data.

## 12. Scheduled operations

Useful management commands include:

```bash
python manage.py check_alerts
python manage.py send_daily_summary
python manage.py cleanup_notifications
python manage.py backup_db
```

Schedule them with Railway's supported scheduling/cron mechanism or another trusted scheduler. Do not run long-running recurring jobs inside the web request process.

## 13. Documentation

Additional implementation notes are under `docs/`:

- `INVENTORY_MULTI_UNIT_WAC.md`
- `RECEIPT_BRANDING_UPDATE.md`
- `RECEIPT_REPORT_PERIOD_UPDATE.md`
- `REGISTRATION_VALIDATION.md`
- `SMS_DEBT_SUBSCRIPTION.md`
- `INTELLIGENT_REPORT_SMS.md`
- `CHAT_ATTACHMENT_AND_TOKEN_FIX.md`
- `MIGRATIONS_WARNING.md`
- `FULL_UPDATE_NOTES.md`
- `SYSTEM_UPDATE_NOTES.md`

## 14. Production principle

The system is designed so that **business data belongs to the pharmacy customer, while application source code and intellectual property remain with the software owner**. Hosting, PostgreSQL, object storage, SMS provider and other third-party services remain separate operational costs.

## V20 professional subscription payment SMS update
- Registration SMS now includes the selected plan, exact payable amount, configured Lipa Namba, and private status URL in one concise GSM-friendly message.
- Verified subscription activation creates a secure, unguessable payment receipt link showing amount, plan, payment reference, start date, and subscription expiry date.
- Payment receipt SMS is a platform transactional SMS and is separate from customer sales Receipt SMS credits.
- Subscription payment receipt SMS delivery is audited as SENT/FAILED with attempt count.
- Pharmacy owners can open My Subscription and resend the payment receipt SMS only when the previous attempt did not succeed; resend never creates a new payment or receipt.
- SMS audit records now store character count and estimated billable SMS segments for cost monitoring.


## V20.26.01 Branch Switching Performance Fix
- Branch switch always redirects to the dashboard instead of returning to the initiating heavy page.
- Branch switch button now displays a dedicated `Switching branch...` state.
- Dashboard sales aggregates use index-friendly datetime ranges instead of applying `DATE()` to indexed timestamps.
- Added branch-scoped Product/ProductBatch indexes for dashboard stock and expiry queries.
- Existing sales, inventory, debt, branch and subscription data is preserved.

After deployment run:
`python manage.py migrate`
`python manage.py check`


## Commercial SaaS subscriptions & billing

SmartPOS now supports tiered commercial subscriptions with monthly/yearly billing, included branch and user allowances, paid branch add-ons, manual HQ confirmation, and optional Pesapal online checkout. See `docs/SAAS_PRICING_UPDATE_V20_26.md` for the commercial rules and deployment migration.

## Customer Marketing SMS (paid add-on)

Customer Registry Marketing SMS now uses a paid credit package and a durable Celery + Redis background queue. Run `python manage.py seed_customer_marketing_packages` after deployment to create the default editable packages. Run the web service with `bash start.sh` and a separate worker service with `bash worker.sh`. Configure `REDIS_URL` in production.

## V34.11 — Midnight Business-Day Control

- Tanzania-local midnight (`Africa/Dar_es_Salaam`) is the hard business-date boundary.
- Previous dates become `DAY_ENDED`; the active date is automatically prepared as `OPEN`.
- Midnight never fabricates a physical cash close.
- Provisional carry-forward remains supported and is reconciled after physical verification.
- POS performs a resilient rollover check before accepting cash sales.
- Offline sales preserve their original accounting `business_date`; delayed historical sales are protected from silent date shifting.
- Historical offline cash sales cannot post silently into a physically closed day.

## SmartPOS V50 — Device PIN Security

V50 hardens the existing device-PIN flow in three areas:

- **Trust this device is explicit:** creating a PIN no longer silently trusts the current browser. Password sign-in also removes an existing browser trust when the user leaves Trust this device unchecked.
- **Idle auto-lock:** trusted POS sessions lock after `DEVICE_PIN_IDLE_LOCK_MINUTES` (default `10`) without destroying the authenticated session. The user unlocks with the PIN or signs out to use the password.
- **Progressive lockout:** wrong PIN attempt 3 starts a 30-second cooldown; attempt 5 starts a 5-minute cooldown; the next wrong attempt permanently locks the PIN. PIN endpoints are additionally throttled per peer IP.

New environment settings:

```env
DEVICE_PIN_IDLE_LOCK_MINUTES=10
DEVICE_PIN_IP_RATE_LIMIT_ATTEMPTS=12
DEVICE_PIN_IP_RATE_LIMIT_WINDOW_SECONDS=60
```

See `DEVICE_PIN_LOGIN_V50.md` for the release notes and deployment guidance.


## Layaway and Premium Messaging update (V2)

See `docs/LAYAWAY_PREMIUM_MESSAGING_V2.md` for setup, package rules and production limitations. After deploying this version, run `python manage.py migrate` and `python manage.py seed_layaway_sms_packages`. Configure the existing Africa's Talking SMS credentials before testing notifications.


## Marketplace management in the SmartPOS owner console

- Superusers can open **Marketplace Management** from the System Owner Console sidebar or HQ Dashboard Quick Access. This is a SmartPOS page, not Django Admin.
- Product management supports image upload, preview, replacement, and clearing/removing the old stored image. Products already referenced by orders are archived instead of permanently deleted to preserve order history.
- **View all orders** opens a searchable, filterable paginated order management page. Orders are not hard-deleted; update/cancel their status for audit integrity. Verify provider payment records before marking an order as paid.
- Main routes: `/marketplace/manage/` and `/marketplace/manage/orders/`.

## V40 — Landing header consistency fix

- The Marketplace header action uses the shared inline SVG icon sprite (`i-shopping-bag`) to match SmartPOS icon styling.
- Fixed the responsive Marketplace header CSS placement so it is inside the document stylesheet (not after `</html>`), allowing the mobile link to appear at tablet/mobile widths.
- Kept the existing landing page sections, copy, dynamic previews, Free Plan, setup calculator, reviews, pricing and FAQ behavior intact.
- Responsive header uses SmartPOS design tokens, accessible focus treatment, compact icon-only presentation on very narrow screens, and reduced-motion support.



## V47 Marketplace service request workflow

- Sellers can request Online Selling or seller order-alert SMS from Seller Centre.
- Requests are recorded with status, requester, linked subscription, amount, reviewer and review notes.
- Platform superusers/staff receive in-app notifications linking to the request queue.
- Verified provider payment activates the linked subscription and marks the request active.
- Authorized platform staff can approve/activate for 30 days or reject with an audit note.
- Sellers can view request status in My service requests.
- Buyer payment receipts are not charged against the seller order-alert SMS quota.

Deploy with a database backup, run `python manage.py migrate`, `python manage.py check`, and `python manage.py test marketplace`. Admin review routes are restricted to active staff/superusers; Shop Owner accounts cannot view other sellers' requests.


## V48 — Online Selling seller workflow hardening

- Seller product create/edit and merchant payment settings now require an active Online Selling entitlement.
- Public catalogue and product detail pages exclude seller-store listings unless the store is published and has a currently active Online Selling subscription. Legacy Marketplace products without a seller-store link remain supported.
- Product form supports auto-generated shareable slugs, avoids duplicate slugs, and uses a responsive SmartPOS-green seller form layout.
- Existing request, admin review, and verified-payment activation flow is retained.

Before production deployment, run `python manage.py check` and `python manage.py test marketplace`; test request submission, admin notification, approval, verified sandbox payment, seller product creation, and public store visibility.

## V60 — Marketplace expansion baseline

See `docs/V60_MARKETPLACE_EXPANSION_RELEASE_NOTES.md` for the verified capabilities already present in this source, the features that still require implementation, and the staging/production validation checklist. The V60 package is a complete project archive based on V59; it is not a claim that the future variants, reviews, OTP seller signup, paid boosts and analytics workflows have all been implemented.
