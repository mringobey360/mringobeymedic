from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('expenses', '0002_cashday_automation')]
    operations = [
        migrations.AddField(model_name='cashday', name='closed_automatically', field=models.BooleanField(default=False, db_index=True)),
        migrations.AddField(model_name='cashday', name='auto_close_reason', field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name='cashday', name='automation_checked_at', field=models.DateTimeField(blank=True, null=True)),
    ]
