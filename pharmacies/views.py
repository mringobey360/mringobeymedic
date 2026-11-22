import logging

from django.contrib import messages
from django.http import HttpResponse
from django.db import IntegrityError, transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.views.decorators.http import require_http_methods
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal

from accounts.decorators import admin_required
from django.contrib.auth.decorators import login_required
from hq.models import PaymentSettings, SignupRequest, Subscription
from accounts.models import User
from notifications.models import Notification
from notifications.utils import notify_many
from sms_integration.models import SMSLog
from sms_integration.utils import send_sms
from .forms import PharmacyForm, BranchSettingsForm, WorkplaceLocationForm, ReceiptDesignForm
from .models import BranchAddOn, Pharmacy, ReceiptSettings
from .receipt_utils import copy_design, render_sample_receipt_bytes
from core.receipt_pdf import DEFAULT_STYLE

logger = logging.getLogger(__name__)


@admin_required
def pharmacy_list_view(request):
    if request.user.is_assistant_role():
        profile = getattr(request.user, 'assistant_profile', None)
        if not profile or not profile.active:
            messages.error(request, 'Your Manager Assistant account is inactive.')
            return redirect('dashboard:home')
        pharmacies = profile.accessible_pharmacies().order_by('name').select_related('addon')
    else:
        pharmacies = request.user.business_owner().managed_pharmacies.all().order_by('name').select_related('addon')
    payment_settings = PaymentSettings.load()
    addon_price = payment_settings.branch_addon_price
    package_options = []
    package_limits = {
        SignupRequest.Tier.STARTER: '1 branch • up to 3 users',
        SignupRequest.Tier.BUSINESS: 'Up to 2 branches • up to 10 users',
        SignupRequest.Tier.PROFESSIONAL: 'Up to 5 branches • up to 25 users',
        SignupRequest.Tier.ENTERPRISE: 'Custom branches • custom users',
    }
    for tier, label in SignupRequest.Tier.choices:
        package_options.append({
            'value': tier, 'label': label,
            'monthly': payment_settings.price_for(Subscription.Plan.MONTHLY, tier),
            'yearly': payment_settings.price_for(Subscription.Plan.YEARLY, tier),
            'limits': package_limits.get(tier, ''),
        })
    owner_for_billing = request.user.business_owner()
    branch_count = owner_for_billing.managed_pharmacies.exclude(addon__status=BranchAddOn.Status.CANCELLED).count()
    try:
        subscription = owner_for_billing.subscription
        included_branches = subscription.included_branches
        current_tier = subscription.tier
        current_plan = subscription.plan
        subscription_end = subscription.end_date
    except Subscription.DoesNotExist:
        subscription = None
        included_branches = 1
        current_tier = SignupRequest.Tier.STARTER
        current_plan = Subscription.Plan.MONTHLY
        subscription_end = None

    remaining_branch_slots = max(included_branches - branch_count, 0)
    # Show/charge the price of the owner's package (e.g. Starter = 15,000), not the legacy flat add-on price.
    addon_price = payment_settings.price_for(Subscription.Plan.MONTHLY, current_tier)
    pending_addon = owner_for_billing.branch_addons.filter(status=BranchAddOn.Status.PENDING_PAYMENT).select_related('pharmacy').first()

    paginator = Paginator(pharmacies, 8)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    form = PharmacyForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and not form.is_valid():
        # Never refresh silently: say the branch was NOT created and why.
        problems = [f"{form.fields[name].label or name}: {errs[0]}" for name, errs in form.errors.items() if name in form.fields and errs]
        problems += [str(e) for e in form.non_field_errors()]
        messages.error(request, "Branch was not created. " + ("; ".join(problems) if problems else "Please check the form."))
    if request.method == 'POST' and form.is_valid():
        # A branch slot inside the owner's active subscription is NEVER a paid add-on.
        # Package/billing fields on the create form are informational only when a slot exists.
        # This prevents the old Business/Monthly default from accidentally creating a payment.
        is_additional_branch = branch_count >= included_branches
        if is_additional_branch and subscription is not None and subscription.is_trial:
            # Free trial = Starter features (1 branch). Extra paid branches need a real plan.
            messages.warning(request, "Your Free Plan includes 1 shop. Upgrade your plan to add more branches.")
            return redirect('hq:packages_center')
        if is_additional_branch and pending_addon is not None:
            messages.warning(
                request,
                f"You already have a pending additional branch ({pending_addon.pharmacy.name}). Complete or cancel that request before creating another paid branch."
            )
            return redirect('pharmacies:branch_addon_payment', pk=pending_addon.pharmacy_id)

        selected_tier = current_tier
        selected_plan = current_plan

        # Create the branch and billing record atomically. Included branches activate immediately.
        # Extra branches use the dedicated branch add-on price, NOT the full Business/Professional
        # subscription price shown in the core Plans Center.
        try:
            with transaction.atomic():
                pharmacy = form.save(commit=False)
                pharmacy.is_active = not is_additional_branch
                pharmacy.save()

                # Every new shop gets its OWN starter category records. Never
                # copy Category rows from another branch: categories are tenant-scoped.
                # Standard product units are provided by inventory.forms.unit_choices;
                # custom units are discovered only from this pharmacy's own products/batches.
                from inventory.models import Category
                starter_categories = {
                    'PHARMACY': [
                        'Medicines', 'Medical Supplies', 'Personal Care',
                        'Cosmetics', 'Baby Care', 'First Aid', 'Supplements', 'Other',
                    ],
                    'RETAIL': ['General Goods', 'Food & Beverages', 'Household', 'Personal Care', 'Other'],
                    'WHOLESALE': ['General Goods', 'Food & Beverages', 'Household', 'Personal Care', 'Other'],
                    'SUPERMARKET': ['Food & Beverages', 'Fresh Produce', 'Household', 'Personal Care', 'Other'],
                    'RESTAURANT': ['Food', 'Beverages', 'Ingredients', 'Packaging', 'Other'],
                    'HARDWARE': ['Building Materials', 'Tools', 'Electrical', 'Plumbing', 'Safety Equipment', 'Other'],
                    'ELECTRONICS': ['Mobile Phones', 'Accessories', 'Electronics', 'Chargers & Cables', 'Other'],
                    'FASHION': ['Clothing', 'Footwear', 'Bags', 'Accessories', 'Other'],
                    'BEAUTY': ['Cosmetics', 'Hair Care', 'Skin Care', 'Beauty Tools', 'Other'],
                    'AGROVET': ['Seeds', 'Fertilizers', 'Animal Health', 'Pesticides', 'Farm Tools', 'Other'],
                    'OTHER': ['General Goods', 'Supplies', 'Accessories', 'Other'],
                }
                for category_name in starter_categories.get(pharmacy.business_type, starter_categories['OTHER']):
                    Category.objects.get_or_create(pharmacy=pharmacy, name=category_name)

                request.user.business_owner().managed_pharmacies.add(pharmacy)
                if is_additional_branch:
                    monthly_reference_price = payment_settings.price_for(Subscription.Plan.MONTHLY, selected_tier)
                    package_price = payment_settings.price_for(selected_plan, selected_tier)
                    addon = BranchAddOn.objects.create(
                        pharmacy=pharmacy, owner=request.user.business_owner(),
                        package_tier=selected_tier, billing_plan=selected_plan,
                        billing_amount=package_price, monthly_price=monthly_reference_price,
                    )
        except IntegrityError:
            logger.warning("Branch creation hit an integrity error for user %s", request.user.pk, exc_info=True)
            messages.error(request, "A branch with that name already exists. Please choose a different name.")
            return render(request, 'pharmacies/pharmacy_list.html', {
                'pharmacies': pharmacies, 'page_obj': page_obj, 'form': form, 'addon_price': addon_price,
                'package_options': package_options, 'is_additional_branch': is_additional_branch, 'payment_settings': payment_settings,
            })

        if is_additional_branch:

            # Notify the platform owner immediately when an additional branch
            # is requested. This is deliberately sent before payment so HQ can
            # see the request; a second notification is sent when the owner
            # submits the mobile-money reference.
            superadmins = User.objects.filter(is_superuser=True, is_active=True)
            branch_message = (
                f"{request.user.get_full_name() or request.user.username} requested a new branch "
                f"'{pharmacy.name}' for {request.user.username}. Package: {addon.get_package_tier_display()} / {addon.get_billing_plan_display()}. "
                f"Amount: TZS {addon.billing_amount:,.0f}. Payment is pending."
            )
            notify_many(
                superadmins,
                'New branch request',
                branch_message,
                category=Notification.Category.SYSTEM,
                level=Notification.Level.INFO,
                link_url=reverse('hq:branch_addon_list'),
                pharmacy=pharmacy,
            )

            # Platform SMS is sent to the configured HQ notification number,
            # not against the customer's SMS wallet.
            try:
                settings_obj = PaymentSettings.load()
                if settings_obj.owner_notification_phone:
                    send_sms(
                        settings_obj.owner_notification_phone,
                        f"SmartPOS: New branch request from {request.user.username}: '{pharmacy.name}'. "
                        f"Package {addon.get_package_tier_display()} / {addon.get_billing_plan_display()}, TZS {addon.billing_amount:,.0f}. Payment pending. Check HQ Branch Requests.",
                        category=SMSLog.Category.OTHER,
                    )
            except Exception:
                pass

            notify_many(
                [request.user],
                'Branch request submitted',
                f"Your branch '{pharmacy.name}' was created and is waiting for payment. "
                f"Package: {addon.get_package_tier_display()} / {addon.get_billing_plan_display()}. Amount: TZS {addon.billing_amount:,.0f}. Complete payment to activate the branch.",
                category=Notification.Category.SYSTEM,
                level=Notification.Level.INFO,
                link_url=reverse('pharmacies:branch_addon_payment', kwargs={'pk': pharmacy.pk}),
                pharmacy=pharmacy,
            )

            messages.info(
                request,
                f"Branch '{pharmacy.name}' has been created. Complete the selected package payment below to activate it."
            )
            return redirect('pharmacies:branch_addon_payment', pk=pharmacy.pk)
        else:
            request.session['active_pharmacy_id'] = pharmacy.id
            messages.success(request, f"Pharmacy branch '{pharmacy.name}' created and set as your active branch.")
            return redirect('pharmacies:list')

    return render(request, 'pharmacies/pharmacy_list.html', {
        'pharmacies': pharmacies, 'page_obj': page_obj, 'form': form, 'addon_price': addon_price,
        'package_options': package_options, 'is_additional_branch': branch_count >= included_branches,
        'payment_settings': payment_settings, 'subscription': subscription,
        'current_tier': current_tier, 'current_plan': current_plan, 'subscription_end': subscription_end,
        'included_branches': included_branches, 'remaining_branch_slots': remaining_branch_slots,
        'pending_addon': pending_addon,
    })


@admin_required
@require_http_methods(["GET", "POST"])
def pharmacy_edit_view(request, pk):
    if request.user.is_assistant_role():
        profile = getattr(request.user, 'assistant_profile', None)
        if not profile or not profile.active:
            raise PermissionDenied('Your Manager Assistant account is inactive.')
        # Assistants keep the same operational branch scope; pending branches
        # are intentionally excluded from operational access by their normal
        # permissions, but may be edited here if they are in the assistant
        # manager's explicitly assigned branch set.
        pharmacy = get_object_or_404(profile.accessible_pharmacies(), pk=pk)
    else:
        # IMPORTANT: do not use accessible_management_pharmacies() here. That
        # queryset intentionally contains only ACTIVE branches for operational
        # modules. A newly-created additional branch can be PENDING_PAYMENT and
        # therefore is_active=False, but its owner must still be able to edit
        # branding/contact details and complete payment.
        owner = request.user.business_owner()
        pharmacy = get_object_or_404(owner.managed_pharmacies.all(), pk=pk)
    form = BranchSettingsForm(request.POST or None, request.FILES or None, instance=pharmacy)
    if request.method == 'POST' and form.is_valid():
        # Ordinary branch branding edits deliberately never touch GPS, attendance
        # policy, billing state, regulatory identifiers or internal counters.
        obj = form.save(commit=False)
        obj.save()
        messages.success(request, f"Pharmacy settings for '{pharmacy.name}' updated.")
        return redirect('pharmacies:list')
    
    try:
        billing_owner = request.user.business_owner()
        subscription = billing_owner.subscription
    except Exception:
        subscription = None
    return render(request, 'pharmacies/pharmacy_edit.html', {
        'form': form, 'pharmacy': pharmacy, 'addon': getattr(pharmacy, 'addon', None),
        'subscription': subscription,
    })


@admin_required
@require_http_methods(["GET", "POST"])
def workplace_location_view(request, pk):
    """Dedicated, safe branch GPS and attendance-control page.

    Device GPS is the preferred source. The browser submits latitude, longitude
    and accuracy only after an explicit capture/confirm action. Server-side
    validation remains authoritative and rejects poor-quality device readings.
    """
    if request.user.is_assistant_role():
        profile = getattr(request.user, 'assistant_profile', None)
        if not profile or not profile.active:
            raise PermissionDenied('Your Manager Assistant account is inactive.')
        # Assistants keep the same operational branch scope; pending branches
        # are intentionally excluded from operational access by their normal
        # permissions, but may be edited here if they are in the assistant
        # manager's explicitly assigned branch set.
        pharmacy = get_object_or_404(profile.accessible_pharmacies(), pk=pk)
    else:
        # IMPORTANT: do not use accessible_management_pharmacies() here. That
        # queryset intentionally contains only ACTIVE branches for operational
        # modules. A newly-created additional branch can be PENDING_PAYMENT and
        # therefore is_active=False, but its owner must still be able to edit
        # branding/contact details and complete payment.
        owner = request.user.business_owner()
        pharmacy = get_object_or_404(owner.managed_pharmacies.all(), pk=pk)

    form = WorkplaceLocationForm(request.POST or None, instance=pharmacy)
    if request.method == 'POST' and form.is_valid():
        old_lat, old_lon = pharmacy.latitude, pharmacy.longitude
        obj = form.save(commit=False)
        capture_accuracy = request.POST.get('location_capture_accuracy', '').strip()
        captured_device = request.POST.get('location_captured_device') == '1'
        capture_requested = request.POST.get('gps_capture_confirmed') == '1'

        changed = old_lat != obj.latitude or old_lon != obj.longitude
        if captured_device:
            try:
                accuracy = Decimal(capture_accuracy)
            except Exception:
                accuracy = None
            if accuracy is None or accuracy <= 0 or accuracy > Decimal('250'):
                messages.error(request, 'The GPS reading is not accurate enough. Stand inside the branch, enable precise location, and capture again.')
                return render(request, 'pharmacies/workplace_location.html', {
                    'form': form, 'pharmacy': pharmacy, 'gps_error': True,
                    'has_gps': pharmacy.latitude is not None and pharmacy.longitude is not None,
                })
            obj.location_accuracy_m = accuracy
            obj.location_captured_at = timezone.now()
            obj.location_captured_by = request.user
            obj.location_source = 'DEVICE'
        elif changed and capture_requested:
            # Manual coordinates are an explicit fallback, never an invisible edit.
            obj.location_accuracy_m = None
            obj.location_captured_at = timezone.now()
            obj.location_captured_by = request.user
            obj.location_source = 'MANUAL'

        obj.save()
        if changed:
            source = 'device GPS' if obj.location_source == 'DEVICE' else 'manual coordinates'
            logger.info('Branch workplace location updated: pharmacy=%s actor=%s source=%s accuracy=%s', pharmacy.pk, request.user.pk, source, obj.location_accuracy_m)
        messages.success(request, f"Workplace location and attendance settings for '{pharmacy.name}' were saved safely.")
        return redirect('pharmacies:workplace_location', pk=pharmacy.pk)

    return render(request, 'pharmacies/workplace_location.html', {
        'form': form, 'pharmacy': pharmacy,
        'has_gps': pharmacy.latitude is not None and pharmacy.longitude is not None,
    })


@login_required
@require_http_methods(["POST"])
def pharmacy_switch_view(request, pk):
    if request.user.is_assistant_role():
        profile = getattr(request.user, 'assistant_profile', None)
        if not profile or not profile.active:
            messages.error(request, "Your Manager Assistant account is inactive.")
            return redirect('dashboard:home')
        pharmacy = get_object_or_404(profile.accessible_pharmacies(), pk=pk)
    elif request.user.is_management_user():
        pharmacy = get_object_or_404(request.user.accessible_management_pharmacies(), pk=pk)
    else:
        messages.error(request, "Only management accounts can switch between branches.")
        return redirect('dashboard:home')
    addon = getattr(pharmacy, 'addon', None)
    if not pharmacy.is_active and addon is None:
        messages.error(request, f"Branch '{pharmacy.name}' is currently inactive. Please contact the platform administrator.")
        return redirect('pharmacies:list')
    if addon is not None and addon.status != BranchAddOn.Status.ACTIVE:
        messages.error(
            request,
            f"Branch '{pharmacy.name}' is not usable yet ({addon.get_status_display()}). "
            "Please complete payment first."
        )
        return redirect('pharmacies:branch_addon_payment', pk=pharmacy.pk)

    request.session['active_pharmacy_id'] = pharmacy.id
    # No "Switched to ..." toast: the active branch name in the sidebar already confirms the switch,
    # and the branch's own subscription banner (if any) is shown by the page itself.

    # A successful branch switch should always land on the active branch
    # dashboard. The branch-management page is a management tool, not the
    # destination for a context switch. This also gives Managers and Manager
    # Assistants the same predictable post-switch experience.
    return redirect('dashboard:home')


@admin_required
def branch_addon_payment_view(request, pk):
    """Payment instructions for an additional branch — same manual mobile-money
    flow, look, and copy as the main subscription's payment page (hq)."""
    pharmacy = get_object_or_404(request.user.business_owner().managed_pharmacies.all(), pk=pk)
    addon = getattr(pharmacy, 'addon', None)
    if addon is None:
        messages.info(request, f"'{pharmacy.name}' is included in your plan — no payment needed.")
        return redirect('pharmacies:list')

    payment_settings = PaymentSettings.load()
    addon.refresh_price(payment_settings)
    from payments.models import PaymentGatewaySettings
    from payments.services import resolve_gateway, pesapal_usable
    from payments import abliner_client
    gateway_settings = PaymentGatewaySettings.load()
    selected_gateway = resolve_gateway() if gateway_settings.is_enabled else None
    G = PaymentGatewaySettings.Gateway
    online_payment_ready = bool(
        gateway_settings.is_enabled and (
            (selected_gateway == G.ABLINER and abliner_client.is_usable())
            or (selected_gateway == G.PESAPAL and pesapal_usable())
        )
    )
    if request.method == 'POST':
        reference = request.POST.get('payment_reference', '').strip()
        if addon.status != BranchAddOn.Status.PENDING_PAYMENT:
            messages.info(request, 'This branch payment request has already been processed.')
            return redirect('pharmacies:list')
        if not reference:
            messages.error(request, 'Please enter the mobile-money transaction reference after payment.')
        else:
            addon.payment_reference = reference
            addon.save(update_fields=['payment_reference'])

            superadmins = User.objects.filter(is_superuser=True, is_active=True)
            payment_message = (
                f"{request.user.get_full_name() or request.user.username} submitted payment reference '{reference}' "
                f"for branch '{pharmacy.name}' ({addon.get_package_tier_display()} / {addon.get_billing_plan_display()}, TZS {addon.billing_amount:,.0f}). Please verify and activate."
            )
            notify_many(
                superadmins,
                'Branch payment submitted',
                payment_message,
                category=Notification.Category.SYSTEM,
                level=Notification.Level.INFO,
                link_url=reverse('hq:branch_addon_list'),
                pharmacy=pharmacy,
            )
            try:
                settings_obj = PaymentSettings.load()
                if settings_obj.owner_notification_phone:
                    send_sms(
                        settings_obj.owner_notification_phone,
                        f"SmartPOS: Payment reference {reference} submitted by {request.user.username} for branch '{pharmacy.name}'. "
                        'Please verify in HQ Branch Requests.',
                        category=SMSLog.Category.OTHER,
                    )
            except Exception:
                pass

            notify_many(
                [request.user],
                'Payment reference submitted',
                f"Your payment reference for '{pharmacy.name}' has been submitted successfully. "
                'The platform administrator will verify it and notify you when the branch is activated.',
                category=Notification.Category.SYSTEM,
                level=Notification.Level.SUCCESS,
                link_url=reverse('pharmacies:list'),
                pharmacy=pharmacy,
            )
            messages.success(request, 'Payment reference submitted. The platform administrator will verify and activate this branch.')
            return redirect('pharmacies:list')
    return render(request, 'pharmacies/branch_addon_payment.html', {
        'pharmacy': pharmacy, 'addon': addon, 'payment_settings': payment_settings,
        'online_payment_ready': online_payment_ready,
        'online_gateway_is_abliner': selected_gateway == G.ABLINER,
    })


@admin_required
@require_http_methods(["POST"])
def pharmacy_cancel_addon_view(request, pk):
    """Owner cancels a paid additional branch. Data is kept — only the
    branch stops being usable (see BranchAddOn.cancel)."""
    pharmacy = get_object_or_404(request.user.accessible_management_pharmacies(), pk=pk)
    addon = getattr(pharmacy, 'addon', None)
    if addon is None:
        messages.error(request, "This branch has no billing record to cancel.")
        return redirect('pharmacies:list')

    addon.cancel()
    if request.session.get('active_pharmacy_id') == pharmacy.id:
        request.session.pop('active_pharmacy_id', None)
    messages.success(request, f"Branch '{pharmacy.name}' has been cancelled. Its data (sales, stock, employees) has been kept.")
    return redirect('pharmacies:list')


# ---------------------------------------------------------------------------
# Receipt design (manager-controlled look of every printed / SMS receipt)
# ---------------------------------------------------------------------------
def _configurable_pharmacies(request):
    """Every branch the signed-in manager (or active assistant) may brand.

    Pending-payment branches are included on purpose, like branch settings, so a
    new branch can be branded before it is activated.
    """
    if request.user.is_assistant_role():
        profile = getattr(request.user, 'assistant_profile', None)
        if not profile or not profile.active:
            raise PermissionDenied('Your Manager Assistant account is inactive.')
        return profile.accessible_pharmacies()
    return request.user.business_owner().managed_pharmacies.all()


@admin_required
@require_http_methods(["GET", "POST"])
def receipt_design_view(request, pk=None):
    branches = _configurable_pharmacies(request)
    if pk is None:
        active = getattr(request, 'pharmacy', None)
        if active is None:
            messages.info(request, 'Create or select a branch first, then design its receipt.')
            return redirect('pharmacies:list')
        pharmacy = get_object_or_404(branches, pk=active.pk)
    else:
        pharmacy = get_object_or_404(branches, pk=pk)

    saved = getattr(pharmacy, 'receipt_settings', None)
    form = ReceiptDesignForm(
        request.POST or None, request.FILES or None,
        instance=saved or ReceiptSettings(pharmacy=pharmacy), pharmacy=pharmacy,
    )

    if request.method == 'POST' and form.is_valid():
        # PDF preview is deliberately non-persistent: it renders the exact
        # submitted form values and an uploaded logo from memory. This gives
        # managers a true "preview before save" workflow.
        if request.POST.get('action') == 'preview_pdf':
            preview_design = form.save(commit=False)
            preview_design.pharmacy = pharmacy
            preview_style = dict(DEFAULT_STYLE)
            for field_name in ReceiptSettings.STYLE_FIELDS:
                preview_style[field_name] = getattr(preview_design, field_name)
            uploaded_logo = form.cleaned_data.get('logo')
            remove_logo = form.cleaned_data.get('remove_logo')
            response = HttpResponse(
                render_sample_receipt_bytes(
                    pharmacy,
                    request.user,
                    style=preview_style,
                    logo_override=uploaded_logo,
                    use_branch_logo=not bool(remove_logo or uploaded_logo),
                ),
                content_type='application/pdf',
            )
            response['Content-Disposition'] = 'inline; filename="receipt-design-preview.pdf"'
            return response

        apply_all = request.POST.get('apply_all') == '1'
        copied = 0
        with transaction.atomic():
            design = form.save(commit=False)
            design.pharmacy = pharmacy
            design.updated_by = request.user
            design.save()
            form.apply_branch_fields()
            if apply_all:
                logo_name = pharmacy.logo.name if pharmacy.logo else None
                for other in branches.exclude(pk=pharmacy.pk):
                    copy_design(design, other, request.user)
                    other.receipt_header = pharmacy.receipt_header
                    other.receipt_footer = pharmacy.receipt_footer
                    other.logo = logo_name
                    other.save(update_fields=['receipt_header', 'receipt_footer', 'logo'])
                    copied += 1
        if apply_all and copied:
            messages.success(request, f"Receipt design saved for '{pharmacy.name}' and applied to {copied} other branch{'es' if copied != 1 else ''}.")
        else:
            messages.success(request, f"Receipt design saved for '{pharmacy.name}'. New receipts use it immediately.")
        return redirect('pharmacies:receipt_design_branch', pk=pharmacy.pk)

    if request.method == 'POST':
        messages.error(request, 'Please fix the highlighted fields and save again.')

    from .receipt_samples import sample_lines, sample_total
    _sample_lines = sample_lines(pharmacy.business_type)
    _sample_total = sample_total(_sample_lines)
    branch_list = list(branches.order_by('name'))
    custom_ids = set(ReceiptSettings.objects.filter(pharmacy__in=branch_list).values_list('pharmacy_id', flat=True))
    for b in branch_list:
        b.has_custom_receipt = b.pk in custom_ids
    return render(request, 'pharmacies/receipt_design.html', {
        'form': form, 'pharmacy': pharmacy, 'branch_list': branch_list,
        'has_saved_design': saved is not None,
        'sample_lines': _sample_lines, 'sample_total': _sample_total,
        'other_branch_count': max(len(branch_list) - 1, 0),
        'detail_toggles': [form[n] for n in (
            'show_address', 'show_contacts', 'show_registration', 'show_customer',
            'show_served_by', 'show_batch_details', 'show_barcode')],
    })


@admin_required
@require_http_methods(["POST"])
def receipt_design_reset_view(request, pk):
    pharmacy = get_object_or_404(_configurable_pharmacies(request), pk=pk)
    ReceiptSettings.objects.filter(pharmacy=pharmacy).delete()
    messages.success(request, f"Receipt design for '{pharmacy.name}' was reset to the standard SmartPOS look. Your logo, header line and thank-you message were kept.")
    return redirect('pharmacies:receipt_design_branch', pk=pharmacy.pk)


@admin_required
@require_http_methods(["GET"])
def receipt_design_sample_view(request, pk):
    """Sample receipt rendered with the branch's SAVED design (nothing is stored)."""
    pharmacy = get_object_or_404(_configurable_pharmacies(request), pk=pk)
    response = HttpResponse(render_sample_receipt_bytes(pharmacy, request.user), content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="sample-receipt.pdf"'
    return response
