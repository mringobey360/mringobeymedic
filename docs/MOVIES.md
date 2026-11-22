# Movies (v25)

Sellers: Seller Centre -> New listing -> **Movie**. Upload the full movie (private, paid), a poster (card cover),
an optional trailer (public MP4/WebM, max 80 MB), basic details (year, minutes, genres, quality)
and confirm distribution rights.

Buyers: Movies page (`/marketplace/movies/`), movie cards on the catalogue, trailer plays muted and automatically
(one at a time, off when Data Saver / reduced-motion is on), sound button, locked full movie until payment is verified.

Deploy: `python manage.py migrate` (migration `0019_movies`). Trailers use the default media storage (Railway volume or S3/R2).
Tests: `python manage.py test marketplace.tests_movies marketplace.tests_movies_core`

## Large movie uploads (direct to the bucket)

When a bucket is configured (`AWS_STORAGE_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_ENDPOINT_URL`),
the movie file no longer passes through the web server. The browser sends it to the bucket in 8 MB parts with
short-lived signed links (3 parts in parallel). Without a bucket the old form upload is used.

Seller experience: a live progress card with percent, MB sent, speed, time left, Pause/Resume, Cancel, automatic
retry of failed parts, automatic continue after the network returns, resume after a page reload (choose the same
file again), and plain-language error messages. The form cannot be saved until the movie shows "Uploaded".
The server verifies every part, the final size and the video header before accepting the file.

One-time setup after deploying:

1. `python manage.py migrate` (migration `0020_pendingupload`).
2. `python manage.py setup_bucket_cors --origin https://your-domain` (lets browsers PUT parts and read each part's ETag).
3. Optional daily cron: `python manage.py cleanup_pending_uploads` (removes abandoned half-uploads).

Tests: `python manage.py test marketplace.tests_directupload` (also runs without Django: `python -m unittest marketplace.tests_directupload`).
