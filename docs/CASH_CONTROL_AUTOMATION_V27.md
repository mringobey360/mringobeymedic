# Cash Control Automation V27

## What changed
- Existing `CashDay` remains the source of truth; no duplicate cash module was introduced.
- A new day automatically carries forward the previous reconciled `actual_closing_cash`.
- The first-ever cash day for a branch remains manual because there is no trusted prior closing balance.
- If the latest previous cash day is still open, automatic opening is blocked until it is reconciled.
- Each day records whether opening cash was `MANUAL`, `CARRY_FORWARD`, or `OVERRIDE`.
- Manager opening corrections require a reason and are stored in `CashOpeningAdjustment` audit history.
- Closed cash days cannot have their opening balance changed.
- Cash dashboard now shows opening source and automatic carry-forward confirmation.

## Daily flow
1. First day: manager enters opening cash once.
2. Close day: manager counts physical cash and records actual closing cash.
3. Next day: system creates the cash day automatically with the previous actual closing cash.
4. If a legitimate correction is needed, manager uses **Adjust Opening** and enters a reason.
5. At close, expected cash remains:
   `Opening + Cash Sales - Cash Refunds - Approved Cash Expenses`.

## Safety
Automatic carry-forward never uses an unclosed day, and it never guesses an opening balance.
