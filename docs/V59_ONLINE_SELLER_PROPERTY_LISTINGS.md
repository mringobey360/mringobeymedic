# V59 — Online Seller & Property Listings (Stage 1)

## What was added
* Business types `ONLINE_SELLER` and `REAL_ESTATE` (signup form picks them up automatically). They skip POS sample products and land on the Seller Centre after login.
* `marketplace.Listing`, `ListingImage` (up to 15 photos, auto-resized), `Inquiry` (lead) — migration `marketplace/0010_listings`.
* Public: `/marketplace/properties/` (+ `/rent/`, `/sale/`), detail page with gallery, key facts, amenities, map link, WhatsApp / call / enquiry form, share link with Open Graph preview, safety warning.
* Seller: My listings, new/edit form (location, GPS capture button, size, ownership document, amenities, photos), status actions (publish, reserve, sold/rented, archive, delete), enquiries inbox.
* Admin: Listings -> actions "Approve & publish", "Send back", "Feature for 7 days".
* Seller public shop page now also works for listing-only sellers and shows their listings.

## Rules (settings, all optional)
* `MARKETPLACE_FREE_LISTING_LIMIT` = 3 live listings without Online Selling subscription.
* `MARKETPLACE_PAID_LISTING_LIMIT` = 100 with an active Online Selling subscription.
* `MARKETPLACE_LISTING_AUTO_APPROVE` = False (new/edited listings wait for admin approval).

## Deploy
    python manage.py migrate
    python manage.py test marketplace.tests_listings hq.test_free_trial

## Added after Stage 1
* Products: up to 6 extra photos (gallery on the product page) and an "old price" with automatic -% badge. Migration `marketplace/0011`.
* Lead SMS: the listing owner gets an SMS for each enquiry, taken from the seller's paid Order SMS allowance.
* Verified seller badge (admin: Seller stores -> "Mark as Verified seller").
* Report this advert (admin: Listing reports -> hide + mark handled).
* Printable flyer with QR code per listing (`/marketplace/properties/<slug>/flyer/`, Print -> Save as PDF).

## Not built yet
OTP phone signup, separate listing-seller price plans and fees, paid Boost (admin can feature listings manually), product variants (colour/size) which need a cart/checkout change.
