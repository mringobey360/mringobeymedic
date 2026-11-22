from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('marketplace', '0018_sellerstore_social_verified')]
    operations = [
        migrations.AddField(model_name='product', name='is_movie', field=models.BooleanField(db_index=True, default=False, help_text='A film sold as a digital download. Shown with a trailer and a movie card.')),
        migrations.AddField(model_name='product', name='trailer_file', field=models.FileField(blank=True, help_text='Short public trailer (MP4/WebM). The full movie stays private until the buyer pays.', max_length=255, upload_to='marketplace/trailers/')),
        migrations.AddField(model_name='product', name='movie_year', field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='product', name='movie_minutes', field=models.PositiveSmallIntegerField(blank=True, help_text='Running time in minutes.', null=True)),
        migrations.AddField(model_name='product', name='movie_genres', field=models.CharField(blank=True, help_text='Comma separated, e.g. Action, Drama.', max_length=120)),
        migrations.AddField(model_name='product', name='movie_quality', field=models.CharField(blank=True, max_length=4)),
    ]
