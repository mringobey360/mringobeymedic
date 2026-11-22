# Premium Debt SMS Subscription

Debt reminder SMS is a separate paid feature. It does not consume the standard customer SMS wallet.

Default packages seeded by `python manage.py seed_sms_packages`:
- TZS 5,000 → 500 debt reminder SMS / 30 days
- TZS 10,000 → 10,000 debt reminder SMS / 30 days

The packages are admin-editable. If the second package should be 1,000 SMS rather than 10,000, change its `sms_credits` in Django Admin before selling it.

## Customer flow
1. During account signup, customer may select a Premium Debt SMS package and pay it together with the main subscription.
2. If skipped, the customer can later open Premium Debt SMS, select a package, pay the configured Lipa Namba and submit the transaction reference.
3. System Owner sees the request in the Debt SMS Payments page.
4. On approval, the system automatically activates/extends the separate Debt SMS subscription and credits the SMS allowance.
5. Debt reminder sends reserve exactly one Premium Debt SMS credit. Failed provider sends release the reserved credit.
6. When the subscription expires or the quota reaches zero, the reminder action is blocked and the customer is directed to the Premium page.

The standard 500 SMS wallet remains separate and can continue to be used for normal system SMS.
