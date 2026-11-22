# SmartPOS Layaway Premium Messaging and Payments

## Customer experience
- Branch-scoped wallet: five one-time free trial SMS credits per branch.
- Essential: TZS 2,500 / 100 SMS credits.
- Business Plus: TZS 5,000 / 250 SMS credits.
- The listed products are prepaid credit top-ups, not auto-renewing subscriptions. Credits remain available until consumed, subject to the platform terms. No recurring charge is created.
- Shop owner/manager can choose **Pay securely online** when an enabled PesaPal or ABLiner gateway is configured. Confirmed gateway payment activates the package and grants credits automatically.
- Manual fallback: pay the configured official Lipa Namba, submit the transaction reference, and wait for platform-admin verification. Never grant credits just because a reference was submitted.
- Purchase requests and credits are branch-scoped; the payment must apply to the currently selected branch.
- Duplicate online callbacks and repeat approvals must not grant credits twice.

## Deployment
1. Back up production database and deploy to staging first.
2. Run `python manage.py migrate`.
3. Run `python manage.py seed_layaway_sms_packages`.
4. In Django Admin, verify package prices, the platform Lipa Namba/payee/support details, and enabled payment gateway configuration.
5. Test one online purchase in gateway sandbox and one manual-reference request. Verify successful online payment grants credits exactly once; manual requests grant credits only after admin verification.
6. Confirm Africa's Talking credentials and SMS sending are configured.

## Important
- A gateway must be enabled and configured for online checkout to appear. If neither supported gateway is usable, the page only offers the manual payment process.
- SMS credits are SmartPOS service credits and are separate from the Africa's Talking provider balance/charges. One SMS may consume multiple credits for longer or Unicode messages.
- This is a prepaid credit-pack model. A recurring monthly/yearly SMS subscription would require separate expiry, renewal, reminders, and auto-renewal/cancellation rules and is intentionally not represented as automatic billing here.


## Employee access policy (V13 update)

Employees can see Layaway in the sidebar and can view plans, create a customer plan, record payments up to the outstanding balance, hand over only payment-covered units when stock is available, and flag a plan for manager review. Employees cannot cancel or reopen plans, manage or purchase premium SMS packages, or use manager-only stock reservation overrides. These rules are enforced server-side for SMS package management and cancellation/reopening; test the role permissions in staging before production deployment.

## Customer SMS flow (V14)
- **Plan created:** confirmation, item/quantity, total, amount received, remaining balance, and due date if set. If already fully paid, the message asks the customer to confirm stock availability; it does not promise the product is ready.
- **Each payment:** receipt confirmation, cumulative paid amount and remaining balance. The final payment uses a separate fully-paid confirmation and explicitly says stock/collection must be confirmed.
- **Partial handover:** confirms cumulative quantity handed over and quantity still pending; says no additional payment was taken at handover.
- **Final handover:** confirms all units were handed over.
- **Reminders:** once three days before the due date, on the due date, and three days overdue then weekly while an outstanding balance remains. Cancelled/completed/fully-paid plans are excluded from reminders.
- **Delivery audit and duplicate protection:** each event has a unique event key and an admin-visible log with status, attempts and provider result. Failed/recent pending events are retried up to three attempts when the daily command runs. Provider acceptance is not a guarantee of delivery to the handset.
- Run `python manage.py send_layaway_reminders` daily using a Railway Cron Job or your existing scheduler. If this command is not scheduled, automated due-date reminders and automatic retry will not run. Transactional messages still fire when the plan/payment/handover action happens.

## Deployment for V14 SMS flow
1. Back up the production database and deploy to staging first.
2. Run `python manage.py migrate` to create the Layaway SMS event log.
3. Run `python manage.py seed_layaway_sms_packages` only if you need to seed/update the package catalogue.
4. Run `python manage.py send_layaway_reminders` once as a smoke test; it only sends for plans due by the reminder rules and retries recent failed messages.
5. Schedule `python manage.py send_layaway_reminders` daily (recommended around 09:00 Africa/Dar_es_Salaam) and verify SMS logs in Django Admin.
6. Test plan creation, part payment, final payment, partial handover, full handover, no-credit failure and duplicate submission using a test customer number before production.

## SMS template examples (Swahili)
- New plan: `HABARI ... Layaway LAY-... imesajiliwa. Bidhaa: ... Jumla TZS ... Umelipa TZS ... Salio TZS ...`
- Payment received: `Tumepokea TZS ... Layaway ... Jumla uliyolipa ... Salio ...`
- Fully paid: `Umekamilisha malipo ... Salio TZS 0. Tafadhali wasiliana na duka kuthibitisha upatikanaji wa bidhaa kabla ya kuichukua.`
- Handover: confirms either all units handed over or cumulative handed/remaining quantities for a partial handover.

Messages are kept concise, use the branch name as sender context, and include the branch support phone when configured. Actual character/segment count depends on the final message and SMS gateway encoding.
