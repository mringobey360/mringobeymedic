"""
Diagnose why Pesapal refuses to create a payment order.

    python manage.py pesapal_diagnose                 # check settings, token, IPN
    python manage.py pesapal_diagnose --txn 56        # also show why transaction #56 failed
    python manage.py pesapal_diagnose --txn 56 --submit   # re-try that order and print Pesapal's raw answer

Nothing is charged: --submit only asks Pesapal to create a checkout link with a
fresh reference; the customer never sees it.
"""
import uuid

from django.core.management.base import BaseCommand
from django.urls import reverse

from payments import pesapal_client
from payments.models import PesapalSettings, PesapalTransaction


def _mask(value, keep=3):
    value = value or ''
    return value if len(value) <= keep * 2 else f"{value[:keep]}...{value[-keep:]}"


class Command(BaseCommand):
    help = "Check Pesapal settings, token, IPN registration and (optionally) a failed transaction."

    def add_arguments(self, parser):
        parser.add_argument('--txn', type=int, help="PesapalTransaction id to inspect (the 'Ref #' shown to the customer).")
        parser.add_argument('--submit', action='store_true', help="Re-submit that transaction's data to Pesapal and print the raw reply.")

    def handle(self, *args, **opts):
        out = self.stdout.write
        s = PesapalSettings.load()
        out("== Settings ==")
        out(f"enabled={s.is_enabled}  configured={s.is_configured}  environment={s.environment}")
        out(f"api base      : {s.base_api_url}")
        out(f"site base url : {s.site_base_url or '(empty - request host is used)'}")
        out(f"consumer key  : {_mask(s.consumer_key)}")
        out(f"ipn id / url  : {s.ipn_id or '-'} / {s.ipn_url or '-'}")

        out("\n== Token ==")
        try:
            pesapal_client.get_token(s, force_refresh=True)
            out("OK - credentials are accepted by Pesapal.")
        except pesapal_client.PesapalError as exc:
            out(f"FAILED: {exc.technical}")
            out("Fix Consumer Key/Secret or the Sandbox/Live setting first.")
            return

        base = (s.site_base_url or '').rstrip('/')
        if not base and s.ipn_url:
            # Site base URL is empty in settings (the live site then uses the request host);
            # reuse the host the IPN was registered with so this command builds valid URLs.
            from urllib.parse import urlparse
            parsed = urlparse(s.ipn_url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            out(f"(site base url empty - using {base} from the registered IPN)")
        if not base:
            out("Set 'Site base URL' in Pesapal Settings, then run this again.")
            return
        if base:
            out("\n== IPN ==")
            ipn_url = base + reverse('payments:ipn')
            try:
                ipn_id = pesapal_client.ensure_ipn_registered(s, ipn_url)
                out(f"OK - {ipn_url}  ->  {ipn_id}")
            except pesapal_client.PesapalError as exc:
                out(f"FAILED: {exc.technical}")
                return

        if not opts.get('txn'):
            return
        try:
            txn = PesapalTransaction.objects.get(pk=opts['txn'])
        except PesapalTransaction.DoesNotExist:
            out(f"\nTransaction #{opts['txn']} not found.")
            return

        out(f"\n== Transaction #{txn.pk} ==")
        out(f"purpose={txn.purpose} status={txn.status} amount={txn.amount} {txn.currency}")
        out(f"payer: {txn.payer_name!r}  email={txn.payer_email!r}  phone={txn.payer_phone!r}")
        out(f"description: {txn.description!r}")
        out(f"STORED ERROR (Pesapal's real reason): {txn.error_message or '(none stored)'}")

        if not opts['submit']:
            out("\nAdd --submit to retry this order and print Pesapal's raw reply.")
            return

        out("\n== Re-submitting to Pesapal ==")
        first, _, last = (txn.payer_name or '').strip().partition(' ')
        try:
            ipn_id = pesapal_client.ensure_ipn_registered(s, (base or '') + reverse('payments:ipn'))
            data = pesapal_client.submit_order(
                s, merchant_reference=str(uuid.uuid4()), amount=txn.amount, currency=txn.currency,
                description=txn.description, callback_url=(base or '') + reverse('payments:return'),
                ipn_id=ipn_id, email=txn.payer_email, phone_number=txn.payer_phone,
                first_name=first, last_name=last,
            )
            out(f"SUCCESS - Pesapal created the order: {data.get('order_tracking_id')}")
            out("The data is fine; the earlier failure was temporary or fixed by the cleaning changes.")
        except pesapal_client.PesapalError as exc:
            out(f"FAILED: {exc.technical}")
            out(f"Raw reply: {exc.raw}")
