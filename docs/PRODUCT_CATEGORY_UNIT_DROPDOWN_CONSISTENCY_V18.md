# SmartPOS V18 — Category / Unit Dropdown Consistency

## UI consistency
- Keeps Category and Unit as native SmartPOS form dropdowns, retaining the same `form-input` dimensions, typography, border, focus ring and full-width behaviour used elsewhere in the system.
- The “Add new category/unit” option remains inside the existing dropdown; no separate dropdown library or competing visual pattern is introduced.
- The inline quick-add panel uses the system's teal brand colors, form-input typography, standard control sizing, focus-visible states, responsive wrapping and restrained border/shadow treatment.
- Bumps the static script cache key from v1 to v2 on templates that load the quick-add script so browsers fetch the refreshed styling after deployment.

## Behaviour retained
- Branch-scoped category and custom-unit catalog endpoints remain unchanged.
- Existing categories, standard units and saved custom units continue to use the same dropdown and server-side validation.
- Quick-add selection continues to select the new item in the current form without discarding other entered product details.

## Verification
- Run Python syntax compilation and ZIP integrity checks during packaging.
- Django runtime checks, browser-level dropdown interaction tests and production deployment must be performed in the configured Django/Railway environment.
