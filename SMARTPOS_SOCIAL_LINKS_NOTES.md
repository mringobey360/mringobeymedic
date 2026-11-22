# Seller social links (WhatsApp / Instagram / TikTok / Facebook / YouTube)

Online sellers can add their social media pages in **Seller Centre → My store → Shop profile → Social media**.
Icons (real brand logos, brand colours, hover fill) appear on:

- every product page (under the seller name)
- the public shop page header
- property/listing detail pages (compact size)

Only accounts the seller filled in are shown (order: WhatsApp, Instagram, TikTok, Facebook, YouTube).
Nothing is shown if all are empty. On the shop page the "WhatsApp" contact chip uses the seller's own
WhatsApp number when set, otherwise the branch phone as before.

## What the seller can type
Instagram/TikTok/YouTube: `@myshop`, `myshop`, `instagram.com/myshop`, or a full link (tracking parameters are stripped).
WhatsApp: a phone number (`0712 345 678`, `+255712345678`, `255712345678`) or a wa.me link. A leading 0 means Tanzania (+255);
numbers from other countries need `+` and the country code. Stored as `https://wa.me/<digits>`.
YouTube: channel only (`@name`, `/channel/...`, `/c/...`), not a single video.
The value is rebuilt as a clean canonical https link. Anything that is not a real profile link for
that platform (other websites, `javascript:`, post links, Facebook redirect/share-dialog links) is
rejected with a clear message, and the form keeps what the seller typed.

## Deploy
1. `python manage.py migrate`  (adds 6 columns to SellerStore: migrations `0016`, `0017`, and `0018_sellerstore_social_verified`). `start.sh` already runs this.
2. `python manage.py collectstatic`  (new file `static/marketplace/social.css`, already run by `start.sh`)
3. Run tests (19 pure-Python tests): `python manage.py test marketplace.tests_social`

## Files
- `marketplace/social.py` – validation, canonical links, icon paths (no Django imports)
- `marketplace/models.py` – `SellerStore.instagram_url / tiktok_url / facebook_url`, `social_links` property
- `marketplace/templatetags/social_tags.py`, `templates/marketplace/_social_links.html` – `{% social_links store %}`
- `marketplace/views.py` – `seller_store_setup` validates and saves
- `static/marketplace/social.css` – styles (linked from marketplace base + setup page)
- Templates edited: `seller_store_setup`, `seller_store_public`, `detail`, `listing_detail`, `base`

## Adding another platform later (e.g. Telegram, X)
Add an entry to `PLATFORMS` and `ICON_PATHS` in `social.py`, a model field + migration, and one
`.sx-<key>{--sx:#colour}` line in `social.css`. The form, preview and display loop over `PLATFORMS`.
Icon artwork: Simple Icons (CC0).

## Verified badge (green tick)
A green tick on an icon (and "Verified by SmartPOS" under the row) means the platform owner checked that
the account really belongs to the seller. Sellers see "Verified by SmartPOS" / "Not verified" next to each field.

**How to verify (platform owner / superuser only):** Django admin -> Seller stores -> open the store -> tick
"<Platform> verified" -> Save. Or select stores in the list and use the actions
"Verify ALL current social links" / "Remove social-link verification". The list shows "2/5 verified".

**Tamper-proof by design:** the exact link that was verified is stored (`SellerStore.social_verified`).
The badge shows only while the seller's current link is identical to it. If a seller changes the link, the badge
disappears automatically (also enforced in `SellerStore.save()`), so nobody can get verified and then swap the link.

Suggested check before ticking: ask the seller to put a short code you give them in the account bio or send it
from that account/number, or confirm by phone. The system verifies the *link format* automatically; ownership
is a human decision made in the admin.

## Mobile and desktop behaviour (tested in Chromium at 1280, 768, 390 and 320 px)
- Desktop (mouse): 38 px icons, brand-colour fill on hover, keyboard focus ring.
- Touch devices: 44 px tap targets (40 px compact), no "sticky" hover after a tap, press feedback instead.
- Icons wrap onto a new line on narrow screens; no horizontal scrolling at 320 px.
- Setup form: inputs are 44 px high and 16 px on touch/small screens (stops iOS zooming on focus);
  WhatsApp opens the numeric keypad; saved links are shown short (`@name`, `+255...`, `facebook.com/name`).
- Link opens in a new tab with `rel="noopener noreferrer nofollow"`.
