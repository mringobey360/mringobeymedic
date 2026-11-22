# SmartPOS Seller Centre mobile navigation fix

## Changes
- Seller navigation tabs no longer shrink to fit a narrow screen.
- The tab row has a bounded width and horizontal touch scrolling for mobile devices.
- A subtle scrollbar makes additional navigation items discoverable.
- Links keep their existing URLs, active-state logic, labels, and SmartPOS teal/slate theme.
- Added visible keyboard focus styling and minimum mobile tap targets.

## Validation
- Template/CSS source was inspected and the targeted navigation CSS was updated.
- This is a frontend-only CSS change; no database migration is required.
- A live browser/device test and Railway deployment were not performed in this environment.
