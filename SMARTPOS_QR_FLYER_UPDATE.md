# SmartPOS — QR Code & Property Flyer Update

## Problem
The flyer showed a broken image ("QR code linking to this property") instead of the QR code.

**Root cause:** `listing_qr` served `segno.svg_inline()` output as a standalone `qr.svg` file.
`svg_inline()` omits the XML declaration and the `xmlns` namespace (it is meant for embedding inside HTML),
so browsers cannot render it through `<img src="…/qr.svg">`.

## Changes
| File | Change |
|---|---|
| `marketplace/listing_views.py` | QR embedded **inline** in the flyer (vector, print-safe). `qr.svg` now returns a complete SVG document (XML declaration + xmlns). New `listing_qr_png` view. Error-correction `H`→`M` for less dense, easier-to-scan codes. QR failure can never break the flyer. |
| `marketplace/urls.py` | New route `properties/<slug>/qr.png` (`?download=1` forces download). |
| `marketplace/templates/marketplace/listing_flyer.html` | Redesigned A4 flyer: cover-fit photo grid, gold accent header, contact + QR panel (46 mm), "Scan to view listing" caption, footer note, "Download QR (PNG)" button, PNG fallback if SVG generation fails, no page-break through the QR panel. |
| `marketplace/tests_listings.py` | Regression tests: SVG has xmlns/XML declaration, PNG signature, flyer embeds inline QR, drafts return 404. |

No migrations. No new dependencies (`segno==1.6.1` and `Pillow` already in `requirements.txt`).
PNG output needs Pillow only for nothing extra: segno writes PNG natively.

## Deploy
1. Replace the changed files (or deploy the full project).
2. `python manage.py test marketplace.tests_listings`
3. Restart the app. Open `/marketplace/properties/<slug>/flyer/` and scan the QR with a phone.

## Printing tips
- Chrome/Edge → Print → **Background graphics: ON**, Margins: Default, Scale: 100%.
- Print the QR at **≥ 3 cm**; keep the white border (quiet zone) around it.

## Update 2 — Professional one-page print + mobile-friendly flyer
- **Exactly one A4 page.** The photo grid flexes to fill the remaining height, so long titles/addresses/amenity lists never push content onto a 2nd page.
- **No browser header/footer on print** (date, page title, long https URL, "1/2"). Done with `@page { margin: 0 }` and inner padding, so Chrome/Edge/Firefox omit them automatically. The long URL is no longer printed on the flyer either (the QR carries it).
- **Cleaner professional design:** brand strip, gradient title card with gold accent, key facts (beds/baths/size) as solid chips, "Negotiable" badge, location pin icon, contact + QR panel, discreet footer note.
- **Mobile friendly:** large hero photo + 2 thumbnails, stacked contact panel with tap-to-call phone number, big centred QR, full-width touch-size buttons, native **Share** button (shown only on devices that support it), sticky toolbar.
- Toolbar and screen-only elements are hidden when printing.

## Update 3 — Properties page (`/marketplace/properties/`) redesign
| File | Change |
|---|---|
| `marketplace/templates/marketplace/listing_index.html` | New layout: hero with prominent search bar, For rent / For sale tabs, scrollable **property-type chips**, collapsible **More filters** on phones (always open on desktop), results count, **Sort** (auto-submits), **active-filter chips** with one-tap remove + "Clear all", numbered pager, polished empty state, "Post your property" call-to-action band. Accessible (labels, aria, focus rings). |
| `marketplace/templates/marketplace/_listing_card.html` | Photos now fill the card (`cover`), gradient overlay, **New / Featured / Reserved / Sold-Rented** badges, photo-count chip, "Negotiable" next to price, SVG icons (beds, baths, size, pin) instead of emoji, location clamped to 2 lines, verified seller badge. Closed adverts are greyed. |
| `marketplace/templates/marketplace/_ls_sprite.html` | New shared SVG icon sprite (included on index, detail and seller-store pages). |
| `marketplace/templates/marketplace/_ls_styles.html` | New `ls-index-v2` style block (calm: no hover jumps/animations, respects reduced-motion). Phone: 1 large card per row, 44px+ touch targets, no horizontal scrolling. |
| `marketplace/listing_views.py` | `listing_index` now supplies `quick_types`, `active_filters`, `clear_url`, `tab_qs` (tabs keep your filters), `page_window`. |
| `marketplace/models.py` | `Listing.is_new` (published in the last 7 days). **Property only — no migration.** |
| `marketplace/templates/marketplace/listing_detail.html`, `seller_store_public.html` | Include the icon sprite so cards there show icons too. |
| `marketplace/tests_listings.py` | 5 new tests for the index page. |

Deploy: no migrations, no new dependencies. Run `python manage.py test marketplace.tests_listings`, then hard-refresh (`Ctrl+Shift+R`).

## Update 4 — Compact "Post property" band, Show more, Grid/List (handles many listings)
- **Post-your-property band redesigned:** compact (about 2 lines on phones), professional home-plus icon, small button with plus icon (no longer full-width); the long sentence is shown on desktop only. The header "+ Post property" link became a slim pill (hidden on phones because the band already offers it).
- **Show more (progressive loading):** with JavaScript, "Show more properties" loads the next batch in place (JSON from `?partial=1`), keeps all filters, shows "Showing N of TOTAL", handles errors, and hides the numbered pager. Without JavaScript the numbered pager still works (SEO/accessibility fallback). Requests beyond the last page return an empty batch.
- **Grid / List toggle** (remembered on the device): compact list rows for scanning many properties quickly, 2 columns on desktop.
- Only the visible page of results is queried and rendered (12 per batch), so large catalogues stay fast; photos are lazy-loaded.
- Tests: `test_show_more_json_batches`, `test_show_more_keeps_filters`.
