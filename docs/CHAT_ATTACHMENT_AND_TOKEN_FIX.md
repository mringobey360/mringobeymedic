# Chat attachment and client-token reliability update

## Fixed
- Invalid/non-UUID client tokens no longer reject an otherwise valid message.
- Non-UUID tokens are converted deterministically to UUID5 values so retries remain idempotent.
- Missing tokens are generated server-side.
- The API returns the canonical client token so the browser can retain it for retries.
- Browser token generation has a crypto-safe fallback.
- Attachment changes are included in the pending-message signature so a new file with the same text cannot accidentally reuse an old message token.
- Android/Termux attachment storage avoids Django's file_move_safe/fcntl locking path that can raise OSError 38 (Function not implemented).

## User-visible result
- No more `Invalid client token.` 400 for normal chat sends.
- Attachment preview remains available before Send.
- Failed/uncertain sends can be retried without intentionally creating duplicate messages.
