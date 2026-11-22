# Pesapal Online Payments — Setup Guide

This adds real-time Pesapal checkout (cards, M-Pesa, Tigo Pesa, Airtel Money)
to every part of SmartPOS that involves payment, alongside the
existing manual "Lipa Namba" flow — which is never removed, only used as a
fallback / alternative for customers who prefer it.

## What was added

| Where payment happens              | What's new                                    |
|-------------------------------------|------------------------------------------------|
| New pharmacy signup                 | "Pay online now" on the payment page           |
| Subscription renewal (locked owner) | "Pay online now" on the renewal page           |
| Additional branch (add-on)          | "Pay online now" on the branch payment page    |
| Premium Debt SMS package            | "Pay online now" per package                   |
| Premium Receipt SMS package         | "Pay online now" per package                   |

All five **activate automatically** the instant Pesapal confirms payment —
no admin needs to click "Approve" for online payments. The manual flow
(admin verifies a submitted reference) still works exactly as before for
anyone who pays by mobile money outside the app.

A single new app, `payments`, holds everything: the Pesapal API client, a
`PesapalTransaction` record for every attempt (so you can see the full
history from Django Admin), and the settings you configure below.

## 1. Deploy the code

This zip already contains everything. On Railway, just push/redeploy as
usual, then run migrations (Railway usually does this automatically via
your existing `release`/`start` command; otherwise run manually):

```
python manage.py migrate
```

This creates two new tables: `payments_pesapalsettings` and
`payments_pesapaltransaction`. Nothing else changes in your database.

## 2. Get your Pesapal credentials

Log in to your Pesapal Business Dashboard → **Account Settings →
Developer Settings → API settings**. Copy the **Consumer Key** and
**Consumer Secret** shown there (use the "show/copy" icons).

Sandbox (for testing, no real money) and Live use *different* credentials —
sign up separately at https://developer.pesapal.com for a sandbox account
if you want to test before going live.

## 3. Configure it — entirely from Django Admin

No `.env` variables, no code changes. Go to:

**Django Admin → Payments (Pesapal) → Pesapal Settings**

Fill in:
- **Environment** — start with `Sandbox`.
- **Consumer key / Consumer secret** — from step 2.
- **Site base URL** (under "Advanced") — your live domain, e.g.
  `https://yourapp.up.railway.app` (no trailing slash). This is what lets
  Pesapal call back to your server correctly behind Railway's proxy.
- Leave **Is enabled** OFF for now.

Save. Then use the admin action **"Test connection — request a token from
Pesapal"** from the Pesapal Settings list to confirm the credentials work.

### Go live

1. With `Sandbox` selected, turn **Is enabled** ON and try a real payment
   from one of the five payment pages above (use Pesapal's sandbox test
   card/numbers — see their developer docs).
2. Confirm the transaction shows as **Completed** under
   **Payments (Pesapal) → Pesapal Transactions**, and that the right thing
   activated (e.g. the branch became active, or the SMS package credits
   appeared).
3. Switch **Environment** to `Live`, paste your **Live** Consumer
   Key/Secret (from the same Developer Settings page, live mode), keep
   **Is enabled** ON, and save.
4. The saved IPN registration is per-environment — switching from Sandbox
   to Live clears nothing automatically, but the first Live payment will
   auto-register a fresh Live IPN the first time it's needed. You never
   need to touch Pesapal's own dashboard for this.

### Turning it off again

Untick **Is enabled** at any time. All five pages instantly fall back to
showing only the manual mobile-money flow — nothing else changes.

## 4. Tracking payments (for the owner)

**Django Admin → Payments (Pesapal) → Pesapal Transactions** lists every
attempt across the whole platform: purpose, amount, status, who paid,
Pesapal's confirmation code, and what it was for. Use the **"Re-check
status with Pesapal now"** action if a transaction looks stuck on
"Awaiting Payment" — this asks Pesapal directly rather than waiting for
their notification.

This is in addition to — not instead of — the existing HQ approval lists
(Branch Requests, SMS purchase lists, Subscriptions), which still show
everything including manually-paid customers.

## 5. How it works (for developers maintaining this later)

- `payments/pesapal_client.py` — raw Pesapal API 3.0 calls (token,
  register IPN, submit order, get status). The auth token is cached on the
  `PesapalSettings` row itself (not Django's cache framework), so it's
  correct across every Gunicorn worker without needing Redis.
- `payments/models.py` — `PesapalSettings` (singleton, edited in Admin) and
  `PesapalTransaction` (one row per attempt; `target` generically points at
  whatever it's paying for).
- `payments/services.py` — `submit_to_pesapal()` starts a payment;
  `refresh_status()` asks Pesapal for the truth and, the *first* time it
  sees `COMPLETED`, calls the matching activation function exactly once
  (guarded by a database row lock, so a webhook and a browser poll landing
  at the same moment can never double-activate something).
- `hq/services.py` / `sms_integration/services.py` — the actual
  "activate this" logic, shared between an admin manually confirming a
  payment and Pesapal confirming one automatically, so the outcome
  (notifications, SMS, receipts) is identical either way.
- Pesapal reaches your server two ways: their **IPN webhook**
  (`/payments/ipn/`, server-to-server, always answered) and the
  **browser return** (`/payments/return/`, where the customer's browser
  lands after paying). Either one is enough to complete the payment.

## Notes

- Amounts are always in **TZS**.
- A customer never loses their place: closing the Pesapal tab by mistake
  just leaves them on the "waiting for payment" page, which keeps checking
  automatically and also offers "Open payment page again" and "Use manual
  payment instead".
- Nothing about the *manual* Lipa Namba flow changed — it's still there,
  still admin-verified, exactly as before.


## V37 Professional Checkout Update

The payment engine now keeps the existing PesaPal API 3.0 integration but presents a branded SmartPOS checkout shell around PesaPal's secure hosted payment URL. PesaPal documents that the `redirect_url` returned by `SubmitOrderRequest` may be redirected to directly or loaded inside an iframe.

New customer-facing flow:

```text
Pay Online
    ↓
SmartPOS branded checkout
    ├── payment summary
    ├── customer/reference details
    ├── secure PesaPal checkout iframe
    └── fallback: Open secure checkout
    ↓
Customer selects available PesaPal method
    ↓
PesaPal processes payment
    ↓
Browser callback + server IPN
    ↓
GetTransactionStatus verification
    ↓
COMPLETED → idempotent service activation → receipt
FAILED / INVALID / REVERSED → retry flow
```

The callback/IPN are never treated as proof of payment by themselves. The application queries `GetTransactionStatus` and uses that response as the authoritative status before activating a subscription, branch add-on, or premium package.

The transaction ledger also records the provider status code, masked payment account, provider creation timestamp, last verification timestamp, and IPN count for support and reconciliation.

A customer-facing payment receipt is available at `/payments/receipt/<merchant-reference>/` after the transaction is created. It can be printed from the browser.

### Important branding boundary

The SmartPOS page is customizable, but the actual payment-method form supplied by PesaPal remains PesaPal-controlled. This is intentional: the application should not collect or store card numbers, mobile-money PINs, or other sensitive payment credentials.
