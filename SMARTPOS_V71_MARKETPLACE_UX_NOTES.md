# SmartPOS V71 - Marketplace UX

1. Image loading (static/marketplace/mk-images.js + CSS): skeleton shimmer -> fade-in; failed images retry
   automatically (2x, cache-busted); then a clean "Image unavailable" tile with a Retry button.
   Covers product cards, trending, cart rows, product gallery + thumbnails, search suggestions, shop logos.
   If the script fails to load, plain images are shown (mk-js class is removed).
2. No flash on click: tap-highlight removed, hover-lift only on mouse devices, links no longer blank out
   while navigating (slight dim + top progress bar), buttons keep their width while the spinner shows,
   cross-document View Transitions (header stays fixed) on supported browsers.
3. Track Order popup (all marketplace pages): form -> loading skeleton -> result (status, timeline, items,
   downloads, retry payment) or friendly not-found / connection / validation messages. No page reload.
   Full page /marketplace/track/ still works for direct links. views.track_order returns only the result
   block for AJAX calls (marketplace/_track_result.html is shared by popup and full page).
4. Cart: stale/unpublished products are now removed from the session cart when the cart opens, so the badge,
   item count and checkout always agree.
5. track.html had a <script> inside {% block title %}; fixed.

Not run here: Django tests (Django is not installed in this build environment). Test on staging.

## Ratings & reviews (verified buyers)
- New model `ProductReview` (+ `Product.rating_avg` / `rating_count`, kept in sync by signals) - migration `0015_product_reviews`.
  Run `python manage.py migrate` on deploy.
- Buyers rate with Ticket ID + checkout phone (same proof as Track Order). Only PAID, non-cancelled orders that
  contain the product can rate; one review per order+product (rating again edits it); 12 attempts/hour/IP.
- Product page: average + stars, 5-to-1 breakdown bars, review list with "Show more", "Rate this product" popup
  (tap-friendly star input, character counter, validation, no page reload). Reviewers shown as "Asha M." with a
  "Verified purchase" badge. Cards and trending show stars + count once a product has reviews.
- Track Order result has a "Rate" link per item (opens the popup with the Ticket ID prefilled).
- Moderation: Django admin > Product reviews > Hide/Show (ratings recalculate automatically).
- Tests: marketplace/tests_reviews.py (not run here - Django is not installed in the build environment).

## Landing page
- Removed the "Early customer offer" / Setup Offer section, its nav link, and the FAQ reference to it.

## V72 - product page / images / rating sheet / small phones
- Images (static/marketplace/mk-images.js + CSS): photos are never hidden any more. The container shimmers behind the photo and
  the browser paints it as bytes arrive. Slow = keep waiting on the same request (hint "Slow connection..." after 7s); only a real
  network error retries (3x, back-off, cache-buster); late loads replace the "Image unavailable" tile. Re-checks on tab focus,
  bfcache restore and when back online. (V71 hid images until JS confirmed the load and restarted the download after 20s,
  so on slow mobile data the product photo never appeared.)
- Product page: main photo preloaded + fetchpriority=high, fixed width/height (no layout jump), thumbnails swap only after the new
  photo is fully loaded (old photo stays visible, small spinner), gallery styles moved from inline to CSS.
- Tapping a product card shows a spinner on the photo straight away.
- Rating form: compact bottom sheet on phones (drag handle, wide star row, Ticket ID + phone side by side, 2-row review box
  with counter inside, sticky Submit button, 16px inputs so iOS does not zoom, stays above the keyboard).
- Small phones: "Add to cart" never wraps/clips (nowrap, fluid font; icon hidden <=400px; button and WhatsApp stack <=345px).
  Card price shows on its own line with the name below, so discounts no longer push the name out.
- Cache-busters bumped: marketplace.css ?v=mp-v72, mk-images.js ?v=72.
