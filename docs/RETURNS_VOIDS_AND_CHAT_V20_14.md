# v20_14 — Returns & Voids, and chat Reply / Delete

## Run after deploying
```
python manage.py migrate
```
New migrations: `inventory 0012`, `sales 0007`, `chat 0002`.

## 1. Wrong sale? Returns & Voids
A sale is never deleted. Mistakes are corrected with a return request:

* **Sales History → Return / Void** on the sale. Pick the wrong item(s) and quantity, or tick *Void the entire sale*. A reason is required.
* **Employee**: sends a request (own sales only, within `SALE_RETURN_EMPLOYEE_WINDOW_HOURS`, default 24h). Owners are notified.
* **Owner / Supervisor**: approves or rejects on **Returns & Voids** (sidebar badge shows pending count), or performs the return directly.
* **On approval** (one database transaction, rows locked):
  * stock returns to the *same batch* it was sold from (`StockMovement: SALE_RETURN`), or is written off (`WRITE_OFF`, no stock change);
  * `SaleItem.quantity` becomes the NET quantity still sold, `returned_quantity` records what came back — so every revenue / profit / best-seller total is automatically correct;
  * a linked debt is reduced; if the customer had already paid more than the new total, the screen tells the owner how much to hand back;
  * sale status becomes `PARTIALLY_RETURNED` or `VOIDED` (excluded from transaction counts; receipt PDF is stamped).
* Audit trail: `SaleReturnRequest` + `SaleReturnLine` (who asked, who decided, when, why, value). Read-only in Django admin. Deleting sales in Django admin is now disabled.

## 2. Chat
* **Reply to a specific message**: hover → Reply (or tap a message, or swipe right on a phone). The quote is clickable and jumps to the original. The original sender is notified.
* **Delete my message for everyone**: hover → trash icon → confirm. Text, attachments (including the stored file), mentions and related notifications are erased permanently. An empty tombstone shows "This message was deleted".
* Edits and deletions now appear on other people's screens without reloading (feed returns `changes` + `server_time`).
* **Bug fixed**: the edit endpoint had a URL/view signature mismatch (`message_id`) that made *Edit message* fail with HTTP 500.
