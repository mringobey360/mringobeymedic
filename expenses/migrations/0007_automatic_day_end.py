from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('expenses', '0006_cashdayreopenaudit')]
    operations = [
        migrations.AlterField(model_name='cashday', name='status', field=models.CharField(choices=[('OPEN','Open'),('DAY_ENDED','Day ended — awaiting physical close'),('CLOSED','Closed')], default='OPEN', max_length=20)),
        migrations.AddField(model_name='cashday', name='day_ended_at', field=models.DateTimeField(blank=True, db_index=True, null=True)),
        migrations.AddField(model_name='cashday', name='day_ended_automatically', field=models.BooleanField(db_index=True, default=False)),
    ]
