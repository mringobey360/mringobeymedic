# SmartPOS — Commercial SaaS Subscriptions & Billing V20.26

## Commercial plans

| Tier | Monthly | Yearly | Included branches | Included users |
|---|---:|---:|---:|---:|
| Starter | TZS 15,000 | TZS 150,000 | 1 | 3 |
| Business | TZS 30,000 | TZS 300,000 | 2 | 10 |
| Professional | TZS 50,000 | TZS 500,000 | 5 | 25 |
| Enterprise | TZS 100,000+ | TZS 1,000,000+ | Custom | Custom |

The platform owner can change these prices from HQ Payment Settings. Annual and monthly prices are stored independently.

## Billing rules

- A customer chooses a **tier** and **monthly/yearly billing period** during registration.
- The selected tier controls the included branch allowance and total active-user allowance.
- The owner counts as one user; active employees consume the remaining user allowance.
- Branches within the tier allowance are included.
- Branches above the tier allowance become monthly Branch Add-ons and remain locked until payment is confirmed.
- Existing legacy `monthly_price` / `yearly_price` fields remain as Starter compatibility aliases.
- Premium Debt SMS and Receipt SMS remain separate commercial add-ons.

## Payment lifecycle

1. Customer registers.
2. The system calculates the exact tier + billing + selected add-on total.
3. The customer receives payment instructions and can use the configured Pesapal online checkout when enabled.
4. Manual mobile-money payments can still be confirmed by HQ.
5. Pesapal callbacks/browser status checks use the same activation service as manual confirmation.
6. Successful payment activates the subscription exactly once.
7. The customer receives an in-app notification, login/payment SMS where configured, and a payment receipt.
8. Renewals can retain or change tier and can switch between monthly and yearly billing.

## Online payment safety

- Pesapal transactions are idempotently activated under a database row lock.
- Repeated clicks within the resumable-payment window reuse the pending transaction.
- The renewal transaction records both tier and billing period before payment completion.
- A paid transaction whose activation fails generates an HQ action-needed notification.

## Existing database upgrade

Back up the production database first, then run:

```bash
python manage.py migrate
python manage.py check
```

Do not delete existing migration files.

Migration `hq.0006_saas_tiers_pricing` adds the commercial tier/pricing fields and migrates existing customers to Starter-compatible pricing.
