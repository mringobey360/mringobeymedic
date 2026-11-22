from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('sms_integration', '0010_owner_scoped_custom_sms_packages'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SMSProspect',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phone_number', models.CharField(help_text='Normalised +255... number (one row per number).', max_length=20, unique=True)),
                ('name', models.CharField(blank=True, max_length=150)),
                ('business_name', models.CharField(blank=True, max_length=150)),
                ('business_type', models.CharField(choices=[('RETAIL', 'Duka la rejareja'), ('WHOLESALE', 'Duka la jumla'), ('HARDWARE', 'Vifaa vya ujenzi'), ('PHARMACY', 'Famasia / Duka la dawa'), ('COSMETICS', 'Vipodozi'), ('FOOD', 'Chakula / Mgahawa'), ('OTHER', 'Nyingine')], default='OTHER', max_length=12)),
                ('region', models.CharField(blank=True, max_length=60)),
                ('notes', models.CharField(blank=True, max_length=300)),
                ('status', models.CharField(choices=[('NEW', 'Mpya'), ('CONTACTED', 'Ametumiwa SMS'), ('JOINED', 'Amejiunga'), ('OPTED_OUT', 'Ameomba kuondolewa')], db_index=True, default='NEW', max_length=10)),
                ('sms_count', models.PositiveIntegerField(default=0, help_text='Invitations successfully sent to this number.')),
                ('last_sms_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sms_prospects_added', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddField(
            model_name='smsannouncementrecipient',
            name='prospect',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='announcement_rows', to='sms_integration.smsprospect'),
        ),
        migrations.AlterField(
            model_name='smsannouncement',
            name='audience',
            field=models.CharField(choices=[('ACTIVE', 'Customers with an active subscription'), ('ALL', 'All active customer accounts'), ('EXPIRING', 'Subscriptions expiring within 7 days'), ('EXPIRED', 'Customers with an expired subscription'), ('SELECTED', 'Selected customers only'), ('ASSISTANTS', 'Assistant managers only'), ('PROSPECTS', 'Prospects (registered business phone numbers)')], default='ACTIVE', max_length=10),
        ),
    ]
