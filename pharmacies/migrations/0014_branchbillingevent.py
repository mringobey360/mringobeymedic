from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('pharmacies', '0013_pharmacy_business_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='BranchBillingEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(help_text='e.g. due-10, due-3, due-1, due-0, grace, grace-last, readonly, locked, renewed, activated', max_length=30)),
                ('stage', models.CharField(blank=True, max_length=12)),
                ('due_date', models.DateField(blank=True, null=True)),
                ('detail', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('pharmacy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='billing_events', to='pharmacies.pharmacy')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
                'indexes': [models.Index(fields=['pharmacy', '-created_at'], name='pharm_billing_ev_idx')],
            },
        ),
    ]
