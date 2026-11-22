from django.db import migrations, models


def apply_new_programme(apps, schema_editor):
    """Invite 5 paying customers -> 15% off what you pay (3% per paying invite, capped at 15%)."""
    PaymentSettings = apps.get_model('hq', 'PaymentSettings')
    PaymentSettings.objects.update(referral_percent_per_invite=3, referral_max_percent=15)


class Migration(migrations.Migration):
    dependencies = [('hq', '0013_free_plan')]

    operations = [
        migrations.AlterField(
            model_name='paymentsettings', name='referral_percent_per_invite',
            field=models.PositiveSmallIntegerField(default=3, help_text='Renewal discount (%) earned for each invited customer who completes a paid subscription (3% x 5 invites = 15%).'),
        ),
        migrations.AlterField(
            model_name='paymentsettings', name='referral_max_percent',
            field=models.PositiveSmallIntegerField(default=15, help_text='Highest total discount (%) that can be applied to a single renewal (15% once 5 invited customers have paid).'),
        ),
        migrations.RunPython(apply_new_programme, migrations.RunPython.noop),
    ]
