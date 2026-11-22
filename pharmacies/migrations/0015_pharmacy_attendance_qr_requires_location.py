from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pharmacies', '0014_branchbillingevent'),
    ]

    operations = [
        migrations.AddField(
            model_name='pharmacy',
            name='attendance_qr_requires_location',
            field=models.BooleanField(default=True, help_text='Employees must be physically inside the workplace radius (device GPS) when they scan the branch QR to check in, claim a meal or check out. Outside = no scan, no meal.'),
        ),
    ]
