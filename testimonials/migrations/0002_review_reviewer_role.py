from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('testimonials', '0001_initial')]

    operations = [
        migrations.AddField(
            model_name='testimonialreview',
            name='reviewer_role',
            field=models.CharField(blank=True, help_text='Role of the reviewer, set by the server.', max_length=40),
        ),
    ]
