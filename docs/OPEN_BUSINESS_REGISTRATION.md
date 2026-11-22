# Open registration for any business type

`/jiunge/` now accepts **any** business (pharmacy, shop/duka, wholesale, supermarket, restaurant,
hardware, electronics, clothing, salon, agro-vet, other) — not only pharmacies.

## What changed
- `SignupRequest.business_type` and `Pharmacy.business_type` (default `PHARMACY`).
  **Every existing registration and branch is automatically a Pharmacy** — nothing is changed or lost.
- Step 1 of the wizard starts with a business-type picker. Pharmacy Council FIN is shown and
  **required only for Pharmacy**; other types never see it. Licence number/expiry are optional for everyone.
- `pharmacy_council_fin` is now blank-able in the database (migration `hq/0008`).
- The server (`hq/forms.py`) enforces the same rule: FIN required only when type = Pharmacy.
- Fixed: the form said the permit number was optional but the server rejected an empty one.
- HQ list/detail show the business type; the activation service copies it to the created branch.

## Deploy
    python manage.py migrate        # hq.0008, pharmacies.0013 (additive, safe)

Files: hq/models.py, hq/forms.py, hq/services.py, hq/views.py, pharmacies/models.py,
hq/migrations/0008_*, pharmacies/migrations/0013_*, templates/hq/signup.html,
templates/hq/signup_list.html, templates/hq/signup_detail.html

## V3.16.1 – visual fixes
- Checkout / receipt / sign-in header: the wide SmartPOS wordmark was being cropped into a square (looked like "lar").
  They now use the square icon `static/img/smartpos-icon.svg` on a white tile.
- Registration page: form column is wider (fixed 21rem side panel + flexible form) and plan cards are 2 per row
  with prices on one line, so nothing wraps into narrow "newspaper" columns.
