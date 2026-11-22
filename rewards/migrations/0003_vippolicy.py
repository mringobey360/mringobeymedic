from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [
        ('rewards', '0002_rewarddecision_lifecycle'),
        ('pharmacies', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='VIPPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=True)),
                ('qualifying_visits', models.PositiveSmallIntegerField(default=4)),
                ('window_days', models.PositiveSmallIntegerField(default=90)),
                ('discount_percent', models.DecimalField(decimal_places=2, default=5, max_digits=5)),
                ('minimum_spend', models.DecimalField(decimal_places=2, default=0, help_text='0 disables the minimum-spend requirement.', max_digits=14)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('pharmacy', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='vip_policy', to='pharmacies.pharmacy')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='updated_vip_policies', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'VIP policy', 'verbose_name_plural': 'VIP policies'},
        ),
    ]
