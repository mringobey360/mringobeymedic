# SmartPOS V30.12 — Production Migration Fix

## Fixed issue

Railway was failing with:

`CommandError: Conflicting migrations detected; multiple leaf nodes in the migration graph: (0006_premium_daily_reports, 0006_saas_tiers_pricing in hq).`

This release adds:

`hq/migrations/0007_merge_0006_premium_daily_reports_0006_saas_tiers_pricing.py`

The merge migration does NOT delete or replace either 0006 migration. It preserves:

- Premium Daily Sales Intelligence enablement
- SaaS tier/pricing fields

## Local verification

Run from the project root:

```bash
python manage.py showmigrations hq
python manage.py makemigrations --check
python manage.py check
python manage.py migrate
python manage.py showmigrations hq
```

The migration graph should have one final leaf:

```text
0005_subscription_payment_receipt
  ├── 0006_premium_daily_reports
  └── 0006_saas_tiers_pricing
          \ /
      0007_merge_0006_...
```

## Railway

Commit and push the updated migration:

```bash
git add hq/migrations/0007_merge_0006_premium_daily_reports_0006_saas_tiers_pricing.py
git commit -m "Fix HQ migration graph conflict"
git push
```

Railway should then run:

```bash
python manage.py check --deploy
python manage.py migrate --noinput
```

Do NOT add `makemigrations --merge` to `start.sh`. Production should apply committed migrations, not generate new migrations at boot.

## Security warnings

The current project already defaults HSTS preload to true when `DEBUG=False`. Railway still reported:

- `security.W009`: the actual Railway `SECRET_KEY` is weak/too short.
- `security.W021`: the deployed environment/config reported HSTS preload as disabled.

Set these Railway variables:

```text
DEBUG=False
SECRET_KEY=<a long random secret, 50+ characters>
SECURE_HSTS_PRELOAD=True
SECURE_HSTS_SECONDS=31536000
```

Generate a strong secret locally with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Do not commit the real production secret into `.env`, Git, or this ZIP.

## Important

Do not delete either `0006_premium_daily_reports.py` or `0006_saas_tiers_pricing.py`.
Do not reset or drop the Railway PostgreSQL database.
