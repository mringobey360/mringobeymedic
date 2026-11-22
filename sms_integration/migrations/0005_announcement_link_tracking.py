import uuid

from django.db import migrations, models


def backfill_access_tokens(apps, schema_editor):
    """
    A plain AddField with a callable default only evaluates that callable ONCE and writes
    the same value into every existing row (this is how Postgres ADD COLUMN ... DEFAULT
    works) — so it cannot be used to add a unique, per-row UUID to a table that already has
    data. Give every existing SMSAnnouncementRecipient row its own distinct token here,
    row by row, before the next migration makes the column unique/non-null.
    """
    SMSAnnouncementRecipient = apps.get_model('sms_integration', 'SMSAnnouncementRecipient')
    for row in SMSAnnouncementRecipient.objects.all().only('id'):
        SMSAnnouncementRecipient.objects.filter(pk=row.pk).update(access_token=uuid.uuid4())


class Migration(migrations.Migration):

    dependencies = [
        ('sms_integration', '0004_sms_announcements'),
    ]

    operations = [
        migrations.AddField(
            model_name='smsannouncement',
            name='link_url',
            field=models.URLField(
                blank=True,
                help_text="Optional. Where {link} inside the message should lead (e.g. a payment page). Each "
                          "customer's SMS gets their own short, trackable link that redirects here, so you can "
                          "see who opened it.",
            ),
        ),
        migrations.AlterField(
            model_name='smsannouncement',
            name='message',
            field=models.TextField(
                help_text='SMS text as composed. {name} is replaced with the customer first name; {link} '
                          '(optional) with a trackable link to link_url.',
            ),
        ),

        # Step 1: add the column WITHOUT unique/db_index yet, nullable, no default applied
        # per-row. Safe on a populated table — every existing row just gets NULL for now.
        migrations.AddField(
            model_name='smsannouncementrecipient',
            name='access_token',
            field=models.UUIDField(
                null=True, editable=False,
                help_text="Unguessable identifier used for this customer's personal {link} redirect — never a "
                          "sequential ID.",
            ),
        ),
        # Step 2: give every existing row its OWN random UUID (one query per row, so the
        # values are actually distinct — not the single value a column default would give).
        migrations.RunPython(backfill_access_tokens, reverse_code=migrations.RunPython.noop),
        # Step 3: now that every row has a distinct value, it's safe to enforce NOT NULL +
        # UNIQUE and switch new rows to auto-generating their own token going forward.
        migrations.AlterField(
            model_name='smsannouncementrecipient',
            name='access_token',
            field=models.UUIDField(
                default=uuid.uuid4, editable=False, unique=True, db_index=True,
                help_text="Unguessable identifier used for this customer's personal {link} redirect — never a "
                          "sequential ID.",
            ),
        ),

        migrations.AddField(
            model_name='smsannouncementrecipient',
            name='opened_at',
            field=models.DateTimeField(
                null=True, blank=True,
                help_text='When this customer first followed their {link}.',
            ),
        ),
    ]
