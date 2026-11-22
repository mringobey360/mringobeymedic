# Referral programme: invite codes, referral tracking and renewal-discount settings.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('hq', '0011_free_trial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(model_name='paymentsettings', name='referral_enabled',
            field=models.BooleanField(default=True, help_text='Customers can invite others and earn a renewal discount for every invited customer who pays.')),
        migrations.AddField(model_name='paymentsettings', name='referral_percent_per_invite',
            field=models.PositiveSmallIntegerField(default=10, help_text='Renewal discount (%) earned for each invited customer who completes a paid subscription.')),
        migrations.AddField(model_name='paymentsettings', name='referral_max_percent',
            field=models.PositiveSmallIntegerField(default=50, help_text='Highest total discount (%) that can be applied to a single renewal.')),
        migrations.CreateModel(
            name='ReferralCode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(db_index=True, max_length=12, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='referral_code', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='Referral',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code_used', models.CharField(max_length=12)),
                ('status', models.CharField(choices=[('PENDING', 'Waiting for payment'), ('QUALIFIED', 'Reward available'), ('USED', 'Reward used'), ('VOID', 'Void')], db_index=True, default='PENDING', max_length=10)),
                ('reward_percent', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('qualified_at', models.DateTimeField(blank=True, null=True)),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('referrer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='referrals_made', to=settings.AUTH_USER_MODEL)),
                ('signup', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='referral', to='hq.signuprequest')),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
