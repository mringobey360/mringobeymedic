import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('layaway', '0003_handover_sale_link'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='LayawayRefund',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('method', models.CharField(choices=[('CASH', 'Cash'), ('MOBILE', 'Mobile money'), ('BANK', 'Bank transfer'), ('OTHER', 'Other')], default='CASH', max_length=10)),
                ('note', models.CharField(blank=True, max_length=255)),
                ('refunded_at', models.DateTimeField(auto_now_add=True)),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='refunds', to='layaway.layawayplan')),
                ('refunded_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='layaway_refunds', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-refunded_at']},
        ),
    ]
