# SmartPOS — Bottom navigation (mobile) + professional sidebar (desktop)

## Behaviour
- **Phones and tablets (< 1024px):** the floating bottom bar is the only navigation. The top hamburger button was removed.
  "More" opens the full menu as a rounded bottom sheet (the same sidebar markup, so every link, permission rule and branch switcher still works).
  Close it with the X in the sheet, the dimmed backdrop, the Escape key, or by tapping a bar item.
- **Desktop (>= 1024px):** the sidebar stays, restyled: icon tiles, rounded pill links, teal active glow, refined branch card, sub-menu guides.
  The collapse/expand rail still works.
- **Colours:** only the SmartPOS palette (ink #0b1220 + brand teal #14b8a6 / #0f766e / #2dd4bf). Inter font kept for consistency.

## Motion and usability
- A single pill glides behind the active bottom-bar icon; tap feedback on every item; More turns into an X.
- Menu sheet items fade in one after another; sub-menus drop in; sidebar icons scale on hover; pages fade in. All disabled under "reduce motion".
- Active tab sets `aria-current="page"`. When the current page lives under More, the More button is tinted so you always know where you are.

## Bar items per role
| Role | Items |
|---|---|
| Owner / manager / employee / assistant | Home, Sales, Sell (raised), Stock, More (assistant items still follow permissions) |
| System owner (superuser) | HQ, Customers, Market, Plans, More |
| Seller centre | Home, Products, Orders, Shop, More (Plans instead of Products/Orders until a store exists) |
| Branch display | Home, More |

## Files changed
- `templates/base.html` — hamburger removed; flatter bar (no glow), sliding glider; new `sp-sidebar-pro` and `sp-bottom-nav-css` style blocks; bottom bar now rendered for every role; main.js cache key v39.
- `static/js/main.js` — glider placement, aria-current, More tint; Escape closes the sheet; backdrop cleaned on resize to desktop.
- `static/sw.js` — cache name bumped so installed PWAs pick up the new shell.

No models, migrations, URLs, views or permissions changed. `tailwind.css` does not need rebuilding (no new Tailwind classes).
Run the usual test suite and check on staging (phone + desktop) before production.

## Fix: negative profit from discounts (V70)
Cause: VIP discounts were floored at batch cost, but the product's own manager-set discount was not. Batches of one product can have
different costs (Revlon: 11,000 and 12,000), so a 4,000 discount on a 15,000 price sold a 12,000-cost unit at 11,000 (profit -1,000).
Fix: `_cost_floor_price()` in `sales/views.py` (used by online and offline sale paths) and the same rule in `templates/sales/pos.html`
so the cashier's total matches the server. Tests added in `sales/tests_vip_discount.py`. Old sales are not changed.
