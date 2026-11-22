# Google button – compact professional redesign

**Files changed**
- `templates/partials/google_button.html` – max width 280px (was 400px), centred; Google-style rectangular button with 6px radius; `large` size kept for tap-friendliness; placeholder now shows the real Google "G" logo while the script loads (no layout jump); dark-theme aware (`filled_black`); softer hover/focus ring; leaner error and "signing in" states; divider limited to 320px.
- `templates/hq/signup.html` – removed the heavy bordered card around the button; now a light centred block with a small caption.
- `templates/accounts/login.html` – centred wrapper so button and divider align.

**Not changed:** authentication logic, nonce, CSRF, endpoints, tests.
**Deploy:** replace files, `collectstatic` not required (inline template change only).

## v97.3 – Google profile picture
Cause: the Google token's `picture` claim was never read, so the system photo stayed empty.
- `accounts/google_auth.py` – `sync_profile_photo()`: downloads the Google picture (https, Google image hosts only, 3 MB cap, 4 s timeout, no redirects) and saves it as `profile_photo` ONLY when the user has none. Failures never block sign-in. Image is compressed by `core.image_signals`.
- `accounts/google_views.py` – called on Google login and when connecting Google from the profile page; the picture URL is kept in the signup session.
- `hq/views.py` – new owners registering with Google get the photo right after registration.
- Tests added in `accounts/tests/test_google_signin.py` (not run in the build sandbox: Django unavailable). Run: `python manage.py test accounts.tests.test_google_signin`.
- No migration required. Owners who already have their own photo keep it.

## v97.4 – Dashboard banner avatar
The round "M" tile on the Online Seller dashboard shows the SHOP LOGO (`store.logo`), not the owner's profile photo, so it fell back to the shop's first letter. It now falls back: shop logo -> owner's profile photo -> first letter.
Only the private seller dashboard uses the personal photo; the public shop page and marketplace keep showing the shop logo / first letter, so personal photos are never exposed publicly.
File: `marketplace/templates/marketplace/seller_dashboard.html`.

## v97.5 – Custom compact Google button
Google draws its own button inside an iframe (personalised "Continue as <name> / <email>"), which is why it looked long and unbalanced no matter the width.
Now `templates/partials/google_button.html` shows OUR button (260px max, 42px high, 8px radius, Google "G", fixed text: "Continue / Sign in / Sign up with Google") and renders Google's real button invisibly on top of it. The click, popup, token, nonce and server verification are unchanged and still Google's own.
Dark mode, hover, keyboard-focus ring and error/offline states are handled. Login logic untouched.
