# SmartPOS Marketplace V60 — Expansion baseline and release checklist

This package is a full project based on the uploaded V59 source, not a patch. It preserves the existing POS, stock, branches, subscriptions, payment tracking, store-scoped categories, order workflow, product gallery and property listings.

## Existing marketplace capabilities verified in source

- Public product catalogue, search suggestions, category filtering, trending products and product detail pages.
- Seller storefronts, seller-specific categories, seller product management, stock, sale price and crossed-out comparison price.
- Multiple product photos (`ProductImage`) with an upload cap enforced by marketplace views; optimized images.
- Cart, checkout, order tracking, payment attempts and payment webhook endpoint.
- Seller order workflow and status history.
- Online service requests/subscriptions for marketplace services.
- Real-estate/property listings with multiple photos, location fields, inquiry flow, WhatsApp/call links, QR/flyer, reporting suspicious listings, admin review and time-limited featured listings.
- Seller store `verified` flag for admin-managed verification.

## Important release boundary

This release does **not** claim that all proposed future features are complete. In particular, do not advertise these as live until separately implemented and tested:

1. Product variants (size/colour) with per-variant stock and variant-aware cart/order lines.
2. Verified-purchase product reviews and ratings with moderation.
3. Self-service phone OTP registration for new online sellers/property advertisers.
4. Paid listing boosts with payment-provider verification and automatic expiry/renewal.
5. End-to-end subscription enforcement and customer/seller SMS templates for every marketplace event.
6. A dedicated marketplace analytics dashboard with views, conversion and revenue attribution.

These are intentionally called out so that a model field or admin flag is not mistaken for a fully connected customer workflow.

## Safe deployment checklist

Run these commands in the project's normal virtual environment and against a staging database first:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py migrate
python manage.py test marketplace
```

Then run the full project test suite:

```bash
python manage.py test
```

Do not apply migrations to production until the staging checks pass and a database backup has been taken. Verify payment callbacks using the provider's sandbox, not screenshots or a client-side success redirect. Keep provider secrets in environment variables/encrypted fields and never commit live credentials.

## Release notes

- Source baseline: `SmartPOS_V59_Online_Seller_Listings.zip` supplied in this conversation.
- Version label: V60 Marketplace Expansion Baseline.
- Packaging validation: Python syntax compilation only. Django checks and database tests require the project's dependencies and configured environment; they must be run by the deployer before production release.
