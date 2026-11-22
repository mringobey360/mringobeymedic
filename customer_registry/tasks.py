from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import CustomerCampaign, CustomerCampaignRecipient
from sms_integration.utils import send_customer_marketing_sms


@shared_task(bind=True, max_retries=3, default_retry_delay=30, acks_late=True)
def queue_customer_campaign(self, campaign_id):
    """Durably fan out a campaign into one task per recipient."""
    campaign = CustomerCampaign.objects.filter(pk=campaign_id).first()
    if not campaign:
        return
    CustomerCampaign.objects.filter(pk=campaign_id, status=CustomerCampaign.Status.QUEUED).update(status=CustomerCampaign.Status.SENDING)
    recipient_ids = list(campaign.recipients.filter(status=CustomerCampaignRecipient.Status.PENDING).values_list('id', flat=True))
    for recipient_id in recipient_ids:
        send_customer_campaign_recipient.delay(recipient_id)


@shared_task(bind=True, max_retries=3, default_retry_delay=45, acks_late=True)
def send_customer_campaign_recipient(self, recipient_id):
    recipient = CustomerCampaignRecipient.objects.select_related('campaign__created_by').filter(pk=recipient_id).first()
    if not recipient or recipient.status == CustomerCampaignRecipient.Status.SENT:
        return
    # A row lock prevents duplicate sends when a task is delivered twice.
    with transaction.atomic():
        row = CustomerCampaignRecipient.objects.select_for_update(of=('self',)).select_related('campaign__created_by').get(pk=recipient_id)
        if row.status == CustomerCampaignRecipient.Status.SENT:
            return
        row.status = CustomerCampaignRecipient.Status.SENDING
        row.attempt_count += 1
        row.last_attempt_at = timezone.now()
        row.error = ''
        row.save(update_fields=['status', 'attempt_count', 'last_attempt_at', 'error'])
        recipient = row

    ok = send_customer_marketing_sms(recipient.phone, recipient.message, owner=recipient.campaign.created_by, segments=recipient.segments, credits_already_reserved=True)
    now = timezone.now()
    if ok:
        CustomerCampaignRecipient.objects.filter(pk=recipient_id).update(status=CustomerCampaignRecipient.Status.SENT, sent_at=now, error='', credits_reserved=0)
    else:
        CustomerCampaignRecipient.objects.filter(pk=recipient_id).update(status=CustomerCampaignRecipient.Status.FAILED, error='SMS provider rejected delivery or delivery was unavailable. Reserved marketing credits were returned.', credits_reserved=0)
        from sms_integration.utils import release_marketing_sms_credits
        release_marketing_sms_credits(recipient.campaign.created_by, recipient.segments)

    _finalize_campaign(recipient.campaign_id)


def _finalize_campaign(campaign_id):
    campaign = CustomerCampaign.objects.get(pk=campaign_id)
    pending = campaign.recipients.filter(status__in=[CustomerCampaignRecipient.Status.PENDING, CustomerCampaignRecipient.Status.SENDING]).exists()
    sent = campaign.recipients.filter(status=CustomerCampaignRecipient.Status.SENT).count()
    failed = campaign.recipients.filter(status=CustomerCampaignRecipient.Status.FAILED).count()
    if not pending:
        status = CustomerCampaign.Status.COMPLETED if sent and not failed else (CustomerCampaign.Status.PARTIAL if sent else CustomerCampaign.Status.FAILED)
        CustomerCampaign.objects.filter(pk=campaign_id).update(status=status, sent_count=sent, failed_count=failed, completed_at=timezone.now())
    else:
        CustomerCampaign.objects.filter(pk=campaign_id).update(sent_count=sent, failed_count=failed)
