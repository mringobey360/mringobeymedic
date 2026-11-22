import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import django.utils.timezone
class Migration(migrations.Migration):
    initial = True
    dependencies = [('pharmacies','__first__'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='Feedback', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('rating', models.PositiveSmallIntegerField(choices=[(1,'Very poor'),(2,'Poor'),(3,'Average'),(4,'Good'),(5,'Excellent')])),
        ('category', models.CharField(choices=[('BUG','Bug or error'),('IDEA','Feature idea'),('USABILITY','Ease of use'),('SPEED','System speed'),('PRAISE','Compliment')], default='USABILITY', max_length=16)),
        ('comment', models.CharField(blank=True,max_length=500)), ('page_url',models.CharField(blank=True,max_length=500)),
        ('page_section',models.CharField(blank=True,max_length=80)), ('app_version',models.CharField(default='V50.48',max_length=32)),
        ('user_role',models.CharField(blank=True,max_length=40)), ('user_agent',models.CharField(blank=True,max_length=500)),
        ('status',models.CharField(choices=[('NEW','New'),('REVIEWING','Under review'),('PLANNED','Planned'),('RESOLVED','Resolved'),('DECLINED','Not planned')],default='NEW',max_length=16)),
        ('created_at',models.DateTimeField(db_index=True,default=django.utils.timezone.now)),('updated_at',models.DateTimeField(auto_now=True)),
        ('branch',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='smartpos_feedback',to='pharmacies.pharmacy')),
        ('user',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='smartpos_feedback',to=settings.AUTH_USER_MODEL)),
    ])]
