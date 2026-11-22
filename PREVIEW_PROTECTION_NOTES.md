# Free-sample protection (v44)

What a visitor can reach WITHOUT paying:
- PDF samples are shown in the SmartPOS viewer (PDF.js on a canvas): no browser PDF toolbar, no Download / Print button,
  in the dialog AND in "Open in new tab" (also inside the installed PWA window).
- The PDF bytes come only from /product/<slug>/preview/file/<token>/ which needs (1) a signed token valid 10 minutes and
  (2) the custom header X-PV-Viewer sent by the viewer script. A link, new tab, <iframe> or service-worker navigation
  cannot send that header, so they get 403. (Does not rely on Sec-Fetch-* headers, which the service worker strips.)
- What is served is a server-built copy: first PREVIEW_PDF_MAX_PAGES pages (default 5), a fine SmartPOS Marketplace brand pattern (small rotated tiles, ~9% opacity),
  a small "FREE SAMPLE" pill (top right), hairline top ribbon + slim footer strip, no links/scripts/metadata, AES-256 encrypted, no print/copy/edit permissions.
  The seller's uploaded file is never served. A PDF that cannot be processed is not served at all.
- Audio/video samples must be under 40% of the paid file; stored sample names are random; endpoint is rate-limited
  (90 req/min/IP) and uses Cache-Control: private, no-store.

The paid file stays in private storage and is released only through a paid DownloadGrant token.

Deploy: pip install -r requirements.txt (adds pypdf), python manage.py migrate (0022), collectstatic.
Check it is live: page source of any product must contain previews.css?v=pv-4.
PDF.js is loaded from cdnjs (pinned 3.11.174). To self-host, copy pdf.min.js and pdf.worker.min.js into static/ and set
PREVIEW_PDFJS_BASE = '/static/vendor/pdfjs' in settings.py.
Optional: PREVIEW_PDF_MAX_PAGES = 3

Limits: anything a person can see on screen can be photographed or screen-recorded; that is why the sample itself is short.

Watermark policy (v45)
- PDF samples: subtle branded tile pattern + corner pill + slim footer (marketplace/preview_protect.py, _watermark_page).
  Change CACHE_VERSION there whenever the design changes so cached samples are rebuilt.
- PAID files are delivered exactly as the seller uploaded them: no watermark is ever added after payment.
- Video samples and trailers show a small "SmartPOS Marketplace" mark above the player controls (previews.css, .pv-vwrap / .mv-stage).
  It is a display overlay; the paid video file itself is not modified.
