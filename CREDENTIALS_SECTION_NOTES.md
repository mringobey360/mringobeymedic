# Credentials / Trust section (landing page)

Adds a compact, professional "Credentials" section (3 slim document cards + one verify link, full-size viewer on click) to the public landing page showing the
Mutech Apex Innovations BRELA and TRA documents.

## Files changed / added
- templates/public/landing.html  (complete file; compact #credentials section, document viewer + JS, nav/footer links, JSON-LD)
- static/img/credentials/brela-certificate-of-registration.png
- static/img/credentials/brela-register-extract.png
- static/img/credentials/tra-tin-certificate.png

## Deploy
1. Replace the files above (or extract this zip over the project).
2. Run `python manage.py collectstatic --noinput` on production.
3. No migrations, no new settings, no new Python code.

## Editing later
- Numbers/dates/labels are plain HTML inside `<section id="credentials">`.
- To replace a document, overwrite the PNG in static/img/credentials/ with the same name
  (higher-resolution scans recommended).
- Public data shown: registration no., TIN, proprietor name, place of business, and the
  register extract (which includes email, phone and P.O. Box). Blur those on the image if you do not want them public.

## Mobile header
- `<style id="smartpos-mobile-login">` keeps the "Log in" button visible on phones (Marketplace becomes icon-only; verified 412/390/360/320px, no overflow).
