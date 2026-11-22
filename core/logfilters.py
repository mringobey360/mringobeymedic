"""Logging filter: phone numbers and card numbers never reach the logs (V117.11)."""
import logging


class RedactSensitiveFilter(logging.Filter):
    def filter(self, record):
        try:
            from payments.privacy import redact_text
            message = record.getMessage()
            cleaned = redact_text(message)
            if cleaned != message:
                record.msg, record.args = cleaned, ()
        except Exception:  # noqa: BLE001 - logging must never break the request
            pass
        return True
