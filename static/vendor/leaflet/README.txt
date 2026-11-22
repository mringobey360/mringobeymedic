Leaflet 1.9.4 local copy (optional but recommended).

Install (needs internet once, on your dev machine or the server):
    python manage.py vendor_leaflet
Check:
    python manage.py vendor_leaflet --check
On production afterwards:
    python manage.py collectstatic --noinput

leaflet.js / leaflet.css are verified against the official SHA-256 hashes before saving.
Manual alternative: download from https://leafletjs.com/download.html and copy
leaflet.js, leaflet.css and the images/ folder into this directory.
The branch page tries this local copy first, then unpkg, jsDelivr and cdnjs.
