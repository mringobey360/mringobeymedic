from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('expenses', '0023_mobile_money_account'),
    ]

    operations = [
        migrations.AddField(model_name='expense', name='auto_recorded',
                            field=models.BooleanField(db_index=True, default=False)),
        migrations.AddField(model_name='expense', name='cash_out_at',
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='expense', name='reviewed_at',
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='expense', name='reviewed_by',
                            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                    related_name='reviewed_expenses', to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='expense', name='reversed_at',
                            field=models.DateTimeField(blank=True, db_index=True, null=True)),
        migrations.AddField(model_name='expense', name='reversed_by',
                            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                    related_name='reversed_expenses', to=settings.AUTH_USER_MODEL)),
    ]
