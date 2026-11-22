from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('hq', '0018_systemnotice_notify_in_app')]

    operations = [
        migrations.AddField(
            model_name='signuprequest', name='google_sub',
            field=models.CharField(blank=True, default='', help_text="Verified Google account id when the owner registered with 'Continue with Google'. Linked to the owner when the account is opened.", max_length=64),
        ),
    ]
