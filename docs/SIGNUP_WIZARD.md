# Signup wizard (public registration form)

`/jiunge/` is now a 5-step wizard: **Business → Licence (optional) → Owner → Plan & add-ons → Review**.

- Same URL, same `SignupForm`, same `signup_view` flow and same payment flow. No database change, no migration.
- Each step is validated before moving on (FIN = 7 digits, optional permit number/expiry date, phone format + availability via the existing `/jiunge/api/check-phone/`).
- The order summary (left on desktop, "Total to pay" on mobile) updates live as plan and add-ons change. Prices come from Payment Settings and the active SMS packages.
- The Review step shows everything with an **Edit** link per section and a required "details are correct" confirmation.
- If the server rejects the form (e.g. FIN already registered), the page reloads with the values kept and jumps to the first step that has an error.
- The draft is kept in the browser tab (sessionStorage) so a refresh does not lose progress; it is cleared on submit.
- Without JavaScript all steps are shown on one page and the form still submits normally (the server validates everything either way).
- The "Best value" badge and "save TZS ..." text for the Yearly plan only appear when yearly is really cheaper than 12 × monthly.

Files: `templates/hq/signup.html`, `templates/hq/partials/signup_field.html`, small changes in `hq/views.py` (plan notes) and `hq/forms.py` (mobile keyboard/autofill hints).

## Payment page (`/jiunge/malipo/<id>/`)
Redesigned to match the wizard (same header, progress steps, cards and colours). No view or database change.

- Progress: Register ✓ → **Payment** → Verification → Login by SMS.
- One compact pay box: amount, Lipa Namba and payee name, each with a **Copy** button; 3 short steps; the admin's extra instructions text (Payment Settings) if set.
- Order summary and a "Need help?" card (tap-to-call support + "Check registration status" link) beside it on desktop, below it on mobile.
- Status-aware: if the registration is already active it shows "Your account is active" + Sign in; if rejected it shows a support message; the pay box only appears while payment is pending.
- If the payment number is still the placeholder (`CHANGE-ME`), the page tells the visitor to call support instead of showing it.
