from datetime import timedelta

from django.db import migrations, models


def mark_existing_deposits(apps, schema_editor):
    """Older repayments recorded in the same moment as the credit sale were POS deposits ("paid now").
    Tag them DEPOSIT so reports can show credit sales as paid-at-sale + really-lent. Pre-V54.91 (LEGACY)
    rows and everything recorded later stay REPAYMENT, so no past figure or closed cash day changes."""
    DebtPayment = apps.get_model('sales', 'DebtPayment')
    window = timedelta(minutes=2)
    batch = []
    for pay in (DebtPayment.objects.exclude(payment_method='LEGACY').select_related('debt__sale').iterator()):
        sale = pay.debt.sale
        if sale.created_at and abs(pay.paid_at - sale.created_at) <= window:
            pay.kind = 'DEPOSIT'
            batch.append(pay)
        if len(batch) >= 500:
            DebtPayment.objects.bulk_update(batch, ['kind'])
            batch = []
    if batch:
        DebtPayment.objects.bulk_update(batch, ['kind'])


class Migration(migrations.Migration):

    dependencies = [('sales', '0013_split_payments')]

    operations = [
        migrations.AddField(
            model_name='debtpayment', name='kind',
            field=models.CharField(choices=[('DEPOSIT', 'Paid at sale (deposit)'), ('REPAYMENT', 'Repayment')],
                                   db_index=True, default='REPAYMENT', max_length=10)),
        migrations.AddField(
            model_name='debtpayment', name='note',
            field=models.CharField(blank=True, max_length=200,
                                   help_text='Optional reference (mobile money code, who brought it...).')),
        migrations.RunPython(mark_existing_deposits, migrations.RunPython.noop),
    ]
