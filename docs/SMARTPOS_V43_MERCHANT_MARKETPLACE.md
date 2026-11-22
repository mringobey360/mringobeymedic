# SmartPOS V43 — Merchant-owned Marketplace foundation

## Included
- `SellerStore` links a public online store to a real `pharmacies.Pharmacy` business record.
- Products can be linked to a store using `Product.seller_store`; related-product lists prefer the linked store.
- Public, shareable store URL: `/marketplace/shop/<slug>/`.
- Seller Centre and store setup are scoped to active pharmacies managed by the signed-in Shop Owner (or platform superuser).
- MerchantPaymentProfile stores provider credentials using the project's encrypted text field; blank password inputs retain saved values.
- Provider connection is intentionally disabled by default. Saving credentials does not mark them verified or enable payment collection.

## Apply
1. Back up the database.
2. Run `python manage.py migrate`.
3. Sign in as a Shop Owner with managed active branches and open `/marketplace/seller/`.
4. Create/publish the store, then link products to it using HQ Marketplace product management.
5. Enter provider credentials only after the merchant has its own approved account.

## Direct-to-merchant payment status
This release lays down the merchant ownership and credential storage foundation, but **does not yet route a live checkout to a seller-specific ABLiner/Pesapal account**. The existing payment dispatcher uses platform-wide settings. Do not enable direct payments until a seller-aware payment adapter, provider-specific callback/IPN verification, transaction-to-store binding, idempotency, and live/sandbox tests are implemented. For Pesapal, confirm each seller can obtain and use their own API credentials for this marketplace model with Pesapal. ABLiner API keys are account-scoped and their official docs require signed webhooks and idempotency for deposits.

## Security
- Secrets are never rendered back to the form.
- Seller pages are limited to managed branches.
- Platform owner should review store publishing and provider credentials before live enablement.
