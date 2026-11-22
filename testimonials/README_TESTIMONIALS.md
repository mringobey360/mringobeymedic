# Client testimonials (V54.104)

Flow: HQ enables popups -> HQ selects customers -> customer sees popup (once) -> review is PENDING ->
HQ approves -> it appears in the landing page carousel.

* HQ screen: sidebar -> "Client Testimonials" (`/hq/testimonials/`), superadmin only (server-side check).
* Popups are OFF by default. Turn them on in that screen.
* Public landing shows APPROVED reviews only; the section is hidden while there are none.
* Deploy: `python manage.py migrate` (adds 4 new tables only; no existing data is touched).
* Tests: `python manage.py test testimonials`
