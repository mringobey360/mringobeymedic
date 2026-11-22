# Announcement Title & Administrator Signature Standard

All new platform administrator announcements follow one customer-facing format:

```text
Announcement title
Message body
SmartPOS Team
```

There is exactly one newline between sections and no blank line before the signature.
The signature is GSM-7 compatible to avoid unnecessary Unicode SMS segmentation.

The title is shown in SMS and in-app notification titles. The message body supports `{name}` and `{link}`.

Existing delivered messages are not rewritten; the standard applies to new announcements and retries generated from the updated campaign renderer.
