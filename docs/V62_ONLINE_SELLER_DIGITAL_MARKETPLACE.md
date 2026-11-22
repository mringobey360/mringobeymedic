# SmartPOS V62 — Online Seller Digital Marketplace

Full project, built on the uploaded **V60** ZIP (the V61/V62 steps described earlier in the conversation were not inside that ZIP, so everything below is implemented here, in code you can read).

## What is new

1. **Digital downloads** (PDF books, EPUB, MP4/M4V video, MP3, ZIP, Word, PowerPoint, Excel; up to 200 MB).
   - Seller chooses *Digital download* in the product form: no stock, a file upload and a rights confirmation.
   - The file is checked by its **content signature**, not only its extension (a renamed `.exe` is refused).
   - Stored in **private storage** (`marketplace/storage.py`): S3/R2 private prefix with 5-minute signed URLs, or a local folder outside `MEDIA_ROOT`. It is never served from `MEDIA_URL`, never shown in the admin, and stored under a random name.
2. **Paid-only delivery.** After the payment gateway confirms payment, `orders.provision_digital()` issues a `DownloadGrant` (unguessable UUID token, 10 downloads by default). The download page (`/marketplace/download/<token>/`) re-checks on every download that the order is paid, not cancelled and the grant is not revoked or used up. GET never consumes a download (link previews/crawlers are harmless); only the POST button does, atomically.
3. **Order flow.** Digital-only orders complete automatically (nothing to ship); mixed orders keep the delivery workflow for physical items while downloads are available immediately. Cancelling an order revokes downloads and restores stock for physical items only.
4. **Checkout.** A digital-only cart needs no delivery location. A digital item is bought once per order. Download buttons appear on the payment confirmation page and in *Track order* (reference + phone).
5. **Stationery and any product.** Physical products keep stock/price/discount/photos. Sellers get category suggestions (Stationery, School supplies, Books, E-books & PDFs, Videos & movies, …) and still own their categories.
6. **"What are you listing?" chooser** (Seller Centre → *New listing*): Physical product · Digital download · Room/house/land (Property listings). The product form tells sellers to use Property listings for rooms and houses.
7. **Seller-only sidebar.** Inside the Seller Centre — and always for accounts of type *Online seller* or *Property* — the sidebar shows only: Seller overview, New listing, Products & digital downloads, Categories, Rooms & property listings, Orders & payments, Customer enquiries, Payment settings, Store profile, public store, plans, Browse Marketplace. POS sections, branch switcher and POS bottom bar are hidden. Shop owners who also run a POS get a *Back to shop workspace* link.
8. **`.jfif` photos** are accepted (they are JPEGs), verified with Pillow and stored as `.jpg`; the 8 MB limit is unchanged and the main photo and extra photos use the same validation.

## Files

New: `marketplace/storage.py`, `digital.py`, `digital_views.py`, `context.py`, `tests_digital.py`, `migrations/0012_digital_products.py`, templates `seller_new_listing.html`, `download.html`, `_download_links.html`, `_seller_sidebar.html`.
Changed: `marketplace/models.py, views.py, listing_views.py, orders.py, urls.py, admin.py`, `payments/services.py`, `pharmacy_system/settings.py` (context processor + `PRIVATE_MEDIA_ROOT`), `templates/base.html`, and the product/cart/detail/seller templates.

## Deploy checklist

```bash
python manage.py check
python manage.py makemigrations --check --dry-run   # must report no changes
python manage.py migrate --plan && python manage.py migrate
python manage.py test marketplace
```

- **Production storage:** set `AWS_STORAGE_BUCKET_NAME`, keys and endpoint (S3 or Cloudflare R2). Without a bucket, mount a persistent volume and set `PRIVATE_MEDIA_ROOT` to it (outside `MEDIA_ROOT`), otherwise uploaded files vanish on redeploy.
- **Large uploads:** files up to 200 MB go through the web process. Raise the reverse-proxy/gunicorn request timeout and body limit accordingly (for example `--timeout 300`).
- **Test before going live:** place a sandbox payment for a digital item, confirm the download works only after payment is confirmed, and that the link of an unpaid/cancelled order is refused.

## Migration note (hotfix)

`0012_digital_products` is idempotent: it adds a column or the `DownloadGrant` table only if it is missing from the real database. This fixes `column "digital_file" of relation "marketplace_product" already exists` on a database that already had part of this feature. If a column already exists with a *different* type, fix that column manually.

## Not part of V62 (still to build and test before advertising)

Services marketplace (mechanics, cleaners, etc.), multi-unit room availability/reservations, product variants with per-variant stock, buyer reviews, OTP seller sign-up, paid boosted listings, marketplace analytics. Digital sales do not yet have per-buyer e-mail delivery or seller-controlled "reset downloads".

## Verification status

Python syntax of the whole project compiles and Django template tags balance. **The Django test-suite and `migrate` were not run**: Django could not be installed in the build environment (no package-index access). `marketplace/tests_digital.py` (≈30 tests) and the migration have therefore not been executed — run the checklist above on staging first.
