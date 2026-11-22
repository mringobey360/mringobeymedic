# SmartPOS V30.18 — Announcement Selection & Live Preview Hotfix

## Fixed

### 1. Selected customers panel
- Selecting **Selected customers only** now reliably reveals the searchable customer selector.
- Customer checkbox selection updates the recipient count immediately.
- Select shown and Clear actions remain available.
- Search/filter does not remove checkbox state.
- Server-side `customer_ids` validation remains authoritative before sending.

### 2. Live title preview
- Announcement title now appears immediately in the SMS preview while typing.
- The preview uses the same compact structure as the customer-facing SMS:
  1. Announcement title
  2. Personalized message
  3. SmartPOS Team
- `{name}` and `{link}` are previewed safely.

### 3. Preview JavaScript resilience
- Removed the hard dependency on the optional `segment-count` element.
- Optional preview elements are now null-safe so one missing UI metric cannot stop the entire compose page.
- Recipient, segment, character and total-SMS metrics continue to refresh independently.

### 4. Professional UX
- The preview explicitly states that it updates while typing.
- No blank line is inserted before the administrator signature.
- Existing server-side title/footer formatting remains the authoritative final SMS format.

## Validation
- JavaScript syntax check passed with Node.js.
- Existing Django model/migration structure is unchanged by this hotfix.
