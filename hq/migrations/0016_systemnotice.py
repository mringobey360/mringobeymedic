import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Adds SystemNotice (in-app status banners). New table only; no existing data is touched."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('hq', '0015_business_types_online_seller_real_estate'),
    ]

    operations = [
        migrations.CreateModel(
            name='SystemNotice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('IMPROVEMENT', 'Improvement / Maboresho'), ('MAINTENANCE', 'Planned maintenance / Matengenezo'), ('INCIDENT', 'Issue being fixed / Changamoto'), ('RESOLVED', 'Issue resolved / Imetatuliwa'), ('INFO', 'General notice / Taarifa')], default='INFO', max_length=12)),
                ('title', models.CharField(max_length=100)),
                ('message', models.TextField(help_text='Short and clear: what happened, what to expect, what you are doing about it.', max_length=500)),
                ('audience', models.CharField(choices=[('ALL', 'Everyone who is signed in'), ('MANAGERS', 'Shop owners and assistants only')], default='ALL', max_length=10)),
                ('link_url', models.CharField(blank=True, help_text='Optional page with more details (https://... or /path).', max_length=300)),
                ('link_label', models.CharField(blank=True, help_text='Button text. Default: Soma zaidi.', max_length=40)),
                ('show_on_login', models.BooleanField(default=False, help_text='Also show on the login page, so customers see it before they sign in.')),
                ('dismissible', models.BooleanField(default=True, help_text='Let customers close the banner (it comes back if you edit the notice).')),
                ('is_active', models.BooleanField(default=True, help_text='Master switch. Turn off to hide the notice immediately.')),
                ('starts_at', models.DateTimeField(default=django.utils.timezone.now, help_text='Shown from this time.')),
                ('ends_at', models.DateTimeField(blank=True, help_text='Hidden automatically after this time. Leave empty to keep it until you switch it off.', null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['is_active', 'starts_at', 'ends_at'], name='hq_notice_live_idx')],
            },
        ),
    ]
