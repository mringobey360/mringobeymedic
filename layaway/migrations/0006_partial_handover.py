# Generated for professional partial Layaway handover support.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import django.utils.timezone

class Migration(migrations.Migration):
    dependencies = [
        ('layaway', '0005_layawayplan_reservation_released'),
        ('sales', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AlterField(
            model_name='layawayplan', name='status',
            field=models.CharField(choices=[('ACTIVE','Active'),('PAID_PENDING_HANDOVER','Paid — pending handover'),('PARTIALLY_HANDED_OVER','Partially handed over'),('COMPLETED','Completed'),('NEEDS_RESOLUTION','Needs resolution'),('CANCELLED','Cancelled')], max_length=24, db_index=True, default='ACTIVE'),
        ),
        migrations.CreateModel(
            name='LayawayHandover',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField()),
                ('handed_over_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('note', models.CharField(blank=True, max_length=255)),
                ('handed_over_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='layaway_handovers', to=settings.AUTH_USER_MODEL)),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='handovers', to='layaway.layawayplan')),
                ('sale', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='layaway_handover', to='sales.sale')),
            ],
            options={'ordering': ['handed_over_at', 'pk']},
        ),
        migrations.AddConstraint(
            model_name='layawayhandover',
            constraint=models.CheckConstraint(condition=models.Q(('quantity__gt', 0)), name='layaway_handover_qty_positive'),
        ),
    ]
