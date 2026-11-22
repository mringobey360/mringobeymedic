# SmartPOS Layaway V3 — handover, reservation labels, safer payments

## Flow
1. **Open plan** (deposit method is now chosen: Cash / Mobile / Bank / Other). A deposit equal to the total goes straight to *Paid, pending handover*.
2. **Instalments** are recorded on the plan. Each form carries a one-time token, so a double tap or retry never records a payment twice.
3. **Fully paid** → status *Paid, pending handover*; customer gets the "ready for collection" SMS.
4. **Hand over product** (any cashier/manager of the branch) creates the official Sale in one transaction:
   - stock is reduced FEFO, with `StockMovement` rows and the agreed layaway price on each `SaleItem` (a price below list shows as a discount);
   - payment method of the Sale follows what the customer really paid with: all cash → `CASH`, none cash → `MOBILE_MONEY`, mixed → `SPLIT` (cash share goes to the cash ledger, the rest to mobile-money reports);
   - receipt PDF is available from the plan (*Print receipt*), the customer is added to the customer registry like a normal POS sale;
   - plan becomes `COMPLETED`, linked to the Sale, with who/when in the audit trail.
5. If stock is short, or the cash day is closed, the handover is refused with a clear message and **nothing is changed**. A manager can restock and retry, or flag the plan.

## Reservation (soft, never locked)
POS search shows an amber label *"Reserved for layaway: 2 of 5 (1 layaway customer)"* and a warning toast when the item is added to a cart. Anyone can still sell it. Plan detail shows stock vs reserved units and warns when reserved units exceed stock. Open plans (Active, Paid pending handover, Needs resolution) count as reserved.

## Permissions
- Record payments, hand over, flag for resolution: any staff of the branch.
- Cancel or re-open a plan: shop owner / manager / assistant only. A written reason is always required.

## Cash drawer (V3.1)
- **Cash received on a plan** (deposit or instalment with method Cash) enters the drawer **the day it is received**, shown as "Layaway" (separate line in *How this is calculated* and in the drill-down), so the till figure matches the money really in the drawer.
- The **handover Sale is not counted in the drawer again** (`cash_sales_qs` skips sales linked to a plan). Handover is therefore never blocked by a closed cash day.
- **Cancelling** a plan with money paid asks how much to refund and how. A **cash refund reduces the drawer** that day; mobile/bank refunds do not. Any amount not refunded is recorded in the audit trail as kept by the shop.
- Mobile / bank / other payments never touch the drawer.
- Layaway cash is folded into the existing "credit payments received in cash" / "cash refunds" totals, so every drawer screen, closing snapshot and range report stays in agreement.
- Not changed: sales/profit reports still recognise the sale (revenue, profit, stock) on the **handover day**, and the mobile-money report shows layaway mobile money on the handover day.

## Not included
- Offline POS catalogue does not carry the reservation label.

## Deploy
1. Back up the database. 2. Deploy. 3. `python manage.py migrate` (adds `0003_handover_sale_link`, `0004_layaway_refund`). 4. `python manage.py test layaway`. 5. Try one plan end to end on a test branch with a demo product.
