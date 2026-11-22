# Generated manually for SmartPOS V29 Assistant delegation.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0007_merge_presence_and_meal_indexes'),
        ('pharmacies', '0007_professional_presence_settings'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                max_length=10,
                default='EMPLOYEE',
                choices=[
                    ('ADMIN', 'Shop Owner / Supervisor'),
                    ('ASSISTANT', 'Manager Assistant'),
                    ('EMPLOYEE', 'Shop Employee'),
                ],
            ),
        ),
        migrations.CreateModel(
            name='ManagerAssistant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('permissions', models.JSONField(blank=True, default=list)),
                ('active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deactivated_at', models.DateTimeField(blank=True, null=True)),
                ('assistant', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='assistant_profile',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('created_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='created_manager_assistants',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('manager', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='manager_assistants',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('pharmacy', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='manager_assistants',
                    to='pharmacies.pharmacy',
                )),
            ],
            options={'ordering': ['-active', '-created_at']},
        ),
    ]
