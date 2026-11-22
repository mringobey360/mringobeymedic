# Image handling (V75)

Goal: photos are never cropped, files are small, and weak connections degrade gracefully.

## Uploads (server)
`core/image_processing.py` + `core/image_signals.py` run automatically on every new ImageField upload:
- EXIF rotation applied, metadata (incl. GPS) removed, progressive JPEG (renders in passes on slow links).
- Product / listing photos: scaled down to 1600 / 1920 px longest side, never upscaled, never cropped.
- Profile photos: whole photo centred on a 640 px square with a soft blurred backdrop (nothing cut off).
- Logos keep transparency (PNG) and proportions. Animated or unreadable files are stored untouched.
- If optimisation fails for any reason the original upload is stored; uploads are never blocked.

Existing photos: `python manage.py optimize_images` (dry run), then `... --apply`.
Options: `--model accounts.User`, `--limit 50`, `--min-saving 0.2`.

## Display (browser)
- `static/css/sp-images.css`: profile photos and logos are `object-fit: contain` everywhere; shimmer while loading.
- `static/marketplace/marketplace.css`: every marketplace picture is shown whole (no inline cover can override it).
- `static/js/sp-images.js` (all layouts): retries with back-off, offline-aware, neutral placeholder instead of a
  broken icon, automatic reload when the connection returns, calm mode on Save-Data/2G.
- `static/marketplace/mk-images.js` keeps the richer marketplace card states (Slow connection / Retry).
- `static/sw.js`: uploaded `/media/` images are cache-first (names are unique), up to 300 kept on the device.

## Notes
- Circular avatars still clip the four corners of the square canvas; the profile page shows the full photo.
- Images served from S3/R2 on another domain are not cached by the service worker (browser cache still applies).
