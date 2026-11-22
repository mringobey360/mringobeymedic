# SMS Announcements (System Owner -> Customers) — v20.23

The platform owner (superadmin) can send announcements and system-update SMS to customers
from **HQ -> SMS Services -> SMS Announcements** (`/hq/sms-announcements/`).

## How it works
1. **Compose** (`New announcement`): campaign name (internal), message type, audience, message.
   * Audiences: active subscriptions, all active customer accounts, expiring within 7 days, expired, or hand-picked customers.
   * `{name}` in the message is replaced with each customer's first name.
   * Quick-start templates (system update, maintenance, subscription reminder, general notice) are provided in Swahili.
   * Live preview, character / SMS-segment counter and recipient total. A confirm dialog is always shown before sending.
   * Optional: also post an in-app (bell) notification to each customer.
2. **Sending** runs in the background so the page returns immediately. Identical messages go to the provider in batches of 100;
   personalised messages are sent one by one. The campaign page refreshes automatically while sending.
3. **Delivery report**: every customer has a row (Sent / Failed / Skipped) with the provider status or reason
   (no phone number, invalid number, duplicate number). Failed customers can be re-sent with **Retry undelivered**.
   If a server restart interrupts a send, the campaign becomes *Partially delivered* after ~5 minutes and can be resumed the same way.

## Cost & safety
* Uses the **platform** Africa's Talking credentials (SMS Settings in Django Admin). It never consumes a customer's
  SMS wallet, Premium Debt SMS or Premium Receipt SMS balance, and ignores customers' own custom credentials.
* Blocked automatically if SMS is switched off globally or the credentials are still `CHANGE-ME`.
* Message length is capped at 400 characters. Special characters (accents, emojis) switch SMS to Unicode (70 chars per segment) — a warning is shown.
* The same message from the same user within 2 minutes is rejected to prevent double-sends.
* Each SMS is also written to the SMS log (category *Platform Announcement*, no owner) for cost tracking.

## Deploy
`python manage.py migrate` (adds migration `sms_integration/0004_sms_announcements`; the Railway `start.sh` already runs it).
