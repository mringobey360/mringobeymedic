from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('daily_reports', '0002_intelligent_report_packages'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyreportsettings',
            name='include_salary_accrual',
            field=models.BooleanField(default=False, help_text='Deduct a daily share of monthly salaries (monthly salary / days in month) when calculating net profit.'),
        ),
    ]
