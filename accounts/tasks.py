from celery import shared_task


@shared_task(bind=True, max_retries=1, default_retry_delay=60, acks_late=True)
def send_attendance_sms(self, kind, object_id):
    """Background delivery of an owner attendance alert (see accounts/attendance_sms.py)."""
    from .attendance_sms import process_attendance_sms

    status = process_attendance_sms(kind, object_id)
    if status == 'failed' and self.request.retries < self.max_retries:
        raise self.retry()
    return {'status': status}
