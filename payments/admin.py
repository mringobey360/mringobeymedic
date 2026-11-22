from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html

from . import pesapal_client, abliner_client
from .models import PaymentEvent, PaymentGatewaySettings, PesapalSettings, PesapalTransaction
from .privacy import mask_phone



def secret_form(model, secret_fields):
    """Admin form that never shows a stored secret and keeps it when the box is left blank."""
    class Meta:
        fields = '__all__'
    attrs = {'Meta': type('Meta', (), {'model': model, 'fields': '__all__'})}

    def make_clean(name):
        def clean(self):
            value = (self.cleaned_data.get(name) or '').strip()
            return value or getattr(self.instance, name, '')
        return clean

    for name in secret_fields:
        attrs[name] = forms.CharField(
            required=False, strip=True,
            widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
            help_text='Stored encrypted and never displayed. Leave blank to keep the saved value; type a new one to replace it.',
        )
        attrs[f'clean_{name}'] = make_clean(name)
    return type(f'{model.__name__}SecretForm', (forms.ModelForm,), attrs)


@admin.register(PaymentGatewaySettings)
class PaymentGatewaySettingsAdmin(admin.ModelAdmin):
    form = secret_form(PaymentGatewaySettings, ['abliner_api_key', 'abliner_webhook_secret'])
    fieldsets = (
        ('Online Payment Control', {'fields': ('is_enabled', 'active_gateway'), 'description': 'Enable online payments here, then choose the provider for NEW payments. Existing transactions remain pinned to their original provider. If online payments are disabled, the Packages page falls back to manual Lipa Namba submission.'}),
        ('ABLiner', {'fields': ('abliner_enabled', 'abliner_api_key', 'abliner_webhook_secret', 'abliner_base_url'), 'description': 'For ABLiner checkout, set Abliner enabled = ON, enter the API key and webhook signing secret issued by ABLiner, and confirm the base URL supplied by ABLiner. Then set Active gateway = ABLiner and Is enabled = ON. Do not use invented credentials. Use the Test ABLiner connection action to validate access before taking real payments.'}),
        ('System', {'fields': ('updated_at',), 'classes': ('collapse',)}),
    )
    readonly_fields = ('updated_at',)
    actions = ['test_abliner_connection']

    def has_add_permission(self, request):
        return not PaymentGatewaySettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description='Test ABLiner connection')
    def test_abliner_connection(self, request, queryset):
        try:
            data = abliner_client.health_check()
            self.message_user(request, 'ABLiner connection is healthy. Balance endpoint responded successfully.', level=messages.SUCCESS)
        except abliner_client.ABLinerError as exc:
            self.message_user(request, f'ABLiner connection failed: {exc.technical}', level=messages.ERROR)


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'gateway', 'event_type', 'provider_status', 'payment', 'success')
    list_filter = ('gateway', 'event_type', 'success')
    search_fields = ('provider_event_id', 'payment__merchant_reference')
    readonly_fields = [f.name for f in PaymentEvent._meta.fields]
    has_add_permission = lambda self, request: False
    has_delete_permission = lambda self, request, obj=None: request.user.is_superuser

    def get_fields(self, request, obj=None):
        fields = [f.name for f in PaymentEvent._meta.fields]
        # Raw gateway payloads are diagnostics for the platform owner only.
        return fields if request.user.is_superuser else [f for f in fields if f != 'payload']

@admin.register(PesapalSettings)
class PesapalSettingsAdmin(admin.ModelAdmin):
    """
    Single row: platform owner enters Consumer Key / Consumer Secret here
    (from Pesapal Business Dashboard > Account Settings > Developer Settings
    > API settings) and switches Sandbox/Live. Nothing Pesapal-related is
    ever hardcoded in the codebase.
    """
    fieldsets = (
        ('Turn on / off', {
            'fields': ('is_enabled', 'environment'),
            'description': (
                'Start with <b>Sandbox</b> and "Turn on" OFF while you test the flow end-to-end. '
                'Switch to <b>Live</b> and turn it ON only once a Sandbox test payment has worked. '
                'While OFF (or unconfigured), customers only see the existing manual mobile-money '
                '(Lipa Namba) payment flow — nothing changes for them.'
            ),
        }),
        ('Pesapal API credentials', {
            'fields': ('consumer_key', 'consumer_secret'),
            'description': 'From your Pesapal Business Dashboard: Account Settings → Developer Settings → API settings.',
        }),
        ('Advanced (usually leave as-is)', {
            'fields': ('site_base_url', 'ipn_id_display'),
            'classes': ('collapse',),
        }),
    )
    form = secret_form(PesapalSettings, ['consumer_secret'])
    readonly_fields = ('ipn_id_display',)
    actions = ['test_connection', 're_register_ipn']

    def has_add_permission(self, request):
        return not PesapalSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def ipn_id_display(self, obj):
        if obj.ipn_id:
            return format_html('<code>{}</code> (registered for {})', obj.ipn_id, obj.ipn_url or 'unknown URL')
        return "Not registered yet — happens automatically on the first payment, or use the action below."
    ipn_id_display.short_description = "Registered IPN ID"

    @admin.action(description="Test connection — request a token from Pesapal")
    def test_connection(self, request, queryset):
        obj = PesapalSettings.load()
        try:
            pesapal_client.get_token(obj, force_refresh=True)
        except pesapal_client.PesapalError as exc:
            self.message_user(request, f"Connection failed: {exc.technical}", level=messages.ERROR)
        else:
            self.message_user(
                request,
                f"Connected successfully to Pesapal {obj.get_environment_display()}. "
                "Credentials are valid.",
                level=messages.SUCCESS,
            )

    @admin.action(description="Re-register IPN with Pesapal (clears the saved IPN ID)")
    def re_register_ipn(self, request, queryset):
        obj = PesapalSettings.load()
        obj.ipn_id = ''
        obj.ipn_url = ''
        obj.save(update_fields=['ipn_id', 'ipn_url'])
        self.message_user(
            request,
            "Saved IPN ID cleared. It will be re-registered automatically the next time a payment is started.",
            level=messages.SUCCESS,
        )


@admin.register(PesapalTransaction)
class PesapalTransactionAdmin(admin.ModelAdmin):
    """Read-mostly ledger of every online payment across the whole platform."""
    list_display = (
        'created_at', 'gateway', 'purpose', 'amount_display', 'status', 'owner',
        'payer_name', 'payer_phone_masked', 'payment_method', 'confirmation_code', 'target_link',
    )
    list_filter = ('gateway', 'purpose', 'status', 'currency')
    search_fields = (
        'merchant_reference', 'order_tracking_id', 'confirmation_code',
        'payer_name', 'payer_email', 'owner__username',
    )
    date_hierarchy = 'created_at'
    readonly_fields = [f.name for f in PesapalTransaction._meta.fields] + ['payer_phone_masked']

    def get_search_fields(self, request):
        # Searching by phone would let any staff member confirm a customer's number.
        fields = list(self.search_fields)
        if request.user.is_superuser:
            fields.append('payer_phone')
        return fields

    def get_fieldsets(self, request, obj=None):
        sets = []
        for title, opts in super().get_fieldsets(request, obj):
            fields = list(opts.get('fields', ()))
            fields = ['payer_phone_masked' if f == 'payer_phone' and not request.user.is_superuser else f for f in fields]
            if not request.user.is_superuser:
                fields = [f for f in fields if f != 'raw_response']
            opts = dict(opts, fields=tuple(fields))
            sets.append((title, opts))
        return sets

    def payer_phone_masked(self, obj):
        return mask_phone(obj.payer_phone) or '—'
    payer_phone_masked.short_description = 'Payer phone'
    fieldsets = (
        ('Payment identity', {'fields': ('merchant_reference', 'gateway', 'order_tracking_id', 'purpose', 'description', 'amount', 'currency')}),
        ('Payer', {'fields': ('owner', 'payer_name', 'payer_email', 'payer_phone')}),
        ('Status', {'fields': ('status', 'confirmation_code', 'payment_method', 'status_description', 'provider_status_code', 'payment_account', 'provider_created_at', 'last_checked_at', 'ipn_count', 'created_at', 'updated_at', 'completed_at', 'processed_at')}),
        ('Checkout', {'fields': ('redirect_url',)}),
        ('Support / diagnostics', {'fields': ('error_message', 'raw_response'), 'classes': ('collapse',)}),
    )
    actions = ['recheck_status']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def amount_display(self, obj):
        return f"{obj.currency} {obj.amount:,.0f}"
    amount_display.short_description = "Amount"

    def target_link(self, obj):
        return str(obj.target) if obj.target else "—"
    target_link.short_description = "For"

    @admin.action(description="Re-check status with Pesapal now (also retries a failed activation)")
    def recheck_status(self, request, queryset):
        from .services import refresh_status
        checked = activated = still_failing = 0
        for txn in queryset:
            if not txn.order_tracking_id:
                continue
            was_processed = txn.is_processed
            try:
                txn = refresh_status(txn)
            except Exception as exc:            # one bad row must not abort the whole batch
                self.message_user(request, f"{txn.merchant_reference}: {exc}", level=messages.ERROR)
                continue
            checked += 1
            if txn.is_processed and not was_processed:
                activated += 1
            elif txn.status == PesapalTransaction.Status.COMPLETED and not txn.is_processed:
                still_failing += 1
        self.message_user(
            request,
            f"Re-checked {checked} transaction(s) with Pesapal; {activated} activated now"
            + (f"; {still_failing} paid but STILL not activated (see each row's error message)." if still_failing else "."),
            level=messages.WARNING if still_failing else messages.SUCCESS,
        )
