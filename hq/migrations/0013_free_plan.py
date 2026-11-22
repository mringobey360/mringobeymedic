from django.db import migrations, models

FREE_PLAN_END = __import__('datetime').date(2099, 12, 31)


def convert_trials_to_free_plan(apps, schema_editor):
    """Existing 7-day trial accounts become Free Plan accounts: they never expire or lock."""
    Subscription = apps.get_model('hq', 'Subscription')
    Subscription.objects.filter(is_trial=True).update(end_date=FREE_PLAN_END, status='ACTIVE')


class Migration(migrations.Migration):
    dependencies = [('hq', '0012_referrals')]

    operations = [
        migrations.AlterField(
            model_name='signuprequest', name='is_trial',
            field=models.BooleanField(default=False, help_text='Free Plan registration: activated immediately, free for life with limited shops, users and monthly sales. No payment taken.'),
        ),
        migrations.AlterField(
            model_name='subscription', name='is_trial',
            field=models.BooleanField(db_index=True, default=False, help_text='Free Plan account (1 shop, 2 users, limited monthly sales). Never locks; cleared automatically when the customer upgrades.'),
        ),
        migrations.RunPython(convert_trials_to_free_plan, migrations.RunPython.noop),
    ]
