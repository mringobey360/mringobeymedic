# Professional Preview (digital products, Gulio marketplace)

## What buyers see (product page)
- "Preview before you buy" panel: free sample, file facts (format, size, content, language, delivery) and a "What's inside" checklist.
- PDF sample opens in a viewer dialog (full-screen sheet on phones) with "Open in new tab" and a "Buy now" button.
  Browsers with no built-in PDF viewer (many Android phones) open the sample in a new tab instead.
- MP3 sample: inline audio player. MP4/WebM sample: inline video player.
- Product cards show a "Preview" badge when a free sample exists.

## What sellers do (Add/Edit product -> Digital download -> "Free preview")
- Free sample: PDF or MP3 up to 15 MB, MP4/WebM up to 80 MB. Must be smaller than the paid file.
- What's inside: one item per line (max 12, 110 characters each).
- Size of the full product ("120 pages") and Language.
- Movies keep using their trailer; the preview section is hidden for movies.

## Safety
- The sample is PUBLIC by design (route: /product/<slug>/preview/). The paid file stays private (DownloadGrant after payment).
- Extension + magic-byte checks, size limits, and the sample must be smaller than the paid file.
- The preview route sends X-Frame-Options: SAMEORIGIN (site default is DENY) so the PDF viewer can embed it.
- A replaced or removed sample is deleted from storage.

## Deploy
    python manage.py migrate          # marketplace 0021_product_preview
    python manage.py collectstatic    # static/marketplace/previews.css
    python manage.py test marketplace.tests_previews

Files: marketplace/{digital.py, models.py, views.py, preview_views.py, urls.py, tests_previews.py, migrations/0021_product_preview.py},
templates/marketplace/{_preview_panel.html, _preview_modal.html, detail.html, _product_card.html, seller_product_form.html, base.html},
static/marketplace/previews.css
