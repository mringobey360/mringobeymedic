from django.conf import settings
from django.core.validators import RegexValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('pharmacies', '0009_branch_package_selection'),
    ]

    operations = [
        migrations.CreateModel(
            name='ReceiptSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('primary_color', models.CharField(default='#2F8F3B', help_text='Main receipt colour: branch name, title, lines and panels.', max_length=7, validators=[RegexValidator('^#[0-9A-Fa-f]{6}$', 'Enter a hex colour such as #2F8F3B.')])),
                ('font_family', models.CharField(choices=[('HELVETICA', 'Modern sans-serif'), ('TIMES', 'Classic serif'), ('COURIER', 'Typewriter')], default='HELVETICA', max_length=10)),
                ('paper_width_mm', models.PositiveSmallIntegerField(choices=[(80, '80 mm (standard)'), (58, '58 mm (compact)')], default=80)),
                ('header_layout', models.CharField(choices=[('LEFT', 'Logo on the left'), ('CENTER', 'Centered')], default='LEFT', max_length=6)),
                ('logo_size', models.CharField(choices=[('SMALL', 'Small'), ('MEDIUM', 'Medium'), ('LARGE', 'Large')], default='MEDIUM', max_length=6)),
                ('show_logo', models.BooleanField(default=True)),
                ('title_text', models.CharField(blank=True, default='OFFICIAL SALES RECEIPT', help_text='Heading shown above the sale details.', max_length=40)),
                ('return_policy', models.CharField(blank=True, help_text='Optional terms, e.g. returns / exchange policy.', max_length=255)),
                ('watermark_text', models.CharField(blank=True, help_text='Faint diagonal text. Blank = the branch name.', max_length=30)),
                ('show_address', models.BooleanField(default=True)),
                ('show_contacts', models.BooleanField(default=True)),
                ('show_registration', models.BooleanField(default=True)),
                ('show_customer', models.BooleanField(default=True)),
                ('show_served_by', models.BooleanField(default=True)),
                ('show_batch_details', models.BooleanField(default=True)),
                ('show_barcode', models.BooleanField(default=True)),
                ('show_watermark', models.BooleanField(default=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('pharmacy', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='receipt_settings', to='pharmacies.pharmacy')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Receipt settings',
                'verbose_name_plural': 'Receipt settings',
            },
        ),
    ]
