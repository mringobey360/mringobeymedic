# SmartPOS barcode scanner browser compatibility fix

## What changed
- The product edit scanner still uses the browser's native `BarcodeDetector` when available.
- Browsers without `BarcodeDetector` now fall back to ZXing's browser scanner loaded from unpkg CDN.
- The fallback uses the same barcode auto-update API and retains the existing duplicate/permission checks on the server.
- Manual and USB/Bluetooth scanner fallback remains available.

## Notes
- Camera access requires HTTPS and user permission.
- The ZXing fallback needs access to `unpkg.com` when it is first loaded. If the CDN is blocked or unavailable, use a USB/Bluetooth scanner or manual fallback.
- `python -m py_compile inventory/views.py` passed. `python manage.py check` could not run in this environment because Django is not installed. No live phone/browser or Railway test was performed.
