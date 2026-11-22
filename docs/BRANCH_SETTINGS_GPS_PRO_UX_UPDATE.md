# Branch Settings + Workplace GPS — Professional Production Update

## 1. Ordinary Branch Settings

`/<branch>/edit/` is intentionally limited to low-risk business configuration:

- Branch name
- Physical location/address
- Office / customer-service phone
- Email
- Logo
- Receipt header
- Receipt footer

Saving this form does **not** modify GPS, attendance policy, billing/subscription state, regulatory identifiers, or receipt/sale counters.

## 2. Workplace Location

`/<branch>/workplace-location/` is the dedicated operational page for:

- Branch latitude/longitude
- Geofence radius (30m–2000m)
- Presence check interval (5–60 minutes)
- Optional lunch attendance rescan
- Lunch rescan start/end
- Outside-geofence grace period (0–60 minutes)

### GPS capture policy

1. Manager opens Workplace Location while physically at the branch.
2. Browser requests location permission.
3. Device GPS is requested with high accuracy and a fresh reading.
4. Readings with accuracy worse than 250m are rejected.
5. Manager reviews coordinates and accuracy.
6. Manager explicitly confirms before saving.
7. Device capture stores:
   - accuracy
   - capture timestamp
   - capturing user
   - source = DEVICE
8. Employee attendance remains server-authoritative and uses the branch coordinates as its geofence anchor.

The application does not continuously track employees. Location is checked only during attendance/presence actions.

## 3. Plan / Billing Safety

Branch Settings does not write subscription tier or billing period directly.

The current plan is displayed as read-only information. The Shop Owner can use **View Plans & Upgrade** to open the existing Packages Center and its established payment/verification flow.

Manager Assistants can manage operational branch settings but do not receive a billing mutation form from Branch Settings.

## 4. No New Database Migration

This update uses existing `Pharmacy` fields. No migration is required.

## 5. Recommended Production Test

For each branch:

1. Open Branch Settings and change the phone number.
2. Confirm the GPS values remain unchanged.
3. Open Workplace Location.
4. Capture GPS from a phone inside the branch.
5. Confirm accuracy is shown.
6. Confirm and save.
7. Verify the last-captured timestamp/user/accuracy.
8. Set the geofence radius.
9. Test employee Verify & Start Workday.
10. Test a presence verification.
11. If GPS is unavailable, use the existing manager-review fallback rather than silently approving attendance.
12. Open Packages & Services as the Shop Owner and confirm the existing upgrade/payment flow still works.


## V42 Branch Capacity & Payment Safety
- Included subscription branch slots are always activated without an add-on payment.
- The Add Branch form no longer defaults to Business/Monthly billing.
- When included branch capacity is full, the owner sees the dedicated branch add-on price or a link to upgrade the core subscription.
- Additional branch billing uses PaymentSettings.branch_addon_price (TZS/month) rather than the full core Business/Professional plan price.
- Only one pending additional-branch request is allowed at a time.
