from django import forms
from django.contrib import admin
from django.db import transaction
from django.db.models import Q

from accounts.models import User
from hq.models import PaymentSettings
from .models import Pharmacy, BranchAddOn, ReceiptSettings, BranchBillingEvent


class PharmacyAdminForm(forms.ModelForm):
    owner = forms.ModelChoiceField(
        queryset=User.objects.none(), required=False,
        label='Owner / Supervisor',
        help_text='Assign this branch to a Shop Owner/Supervisor. Employees cannot own branches.',
    )

    class Meta:
        model = Pharmacy
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['owner'].queryset = User.objects.filter(
            role=User.Role.ADMIN, is_active=True, is_superuser=False
        ).order_by('first_name', 'last_name', 'username')
        if self.instance.pk:
            current = self.instance.managers.filter(role=User.Role.ADMIN).first()
            self.fields['owner'].initial = current


@admin.register(Pharmacy)
class PharmacyAdmin(admin.ModelAdmin):
    form = PharmacyAdminForm
    list_display = ('name', 'owner_names', 'location', 'phone_number', 'is_active', 'addon_status', 'created_at')
    search_fields = ('name', 'location', 'phone_number', 'email', 'registration_number', 'managers__username', 'managers__phone_number')
    list_filter = ('is_active', 'addon__status')
    readonly_fields = ('created_at', 'last_sale_number')

    @admin.display(description='Owner / Supervisor')
    def owner_names(self, obj):
        owners = obj.managers.filter(role=User.Role.ADMIN).only('username', 'first_name', 'last_name')
        return ', '.join(o.get_full_name() or o.username for o in owners) or '— Unassigned —'

    @admin.display(description='Branch billing')
    def addon_status(self, obj):
        addon = getattr(obj, 'addon', None)
        return addon.get_status_display() if addon else 'Included / no add-on'

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        owner = form.cleaned_data.get('owner')
        existing_addon = getattr(obj, 'addon', None) if obj.pk else None
        was_already_managed_by_owner = bool(obj.pk and owner and owner.managed_pharmacies.filter(pk=obj.pk).exists())

        # A branch created from System Admin must follow exactly the same
        # ownership/billing rules as a branch created by the owner UI.
        if owner:
            owner_branch_count = owner.managed_pharmacies.exclude(pk=obj.pk).count()
            try:
                included_branches = owner.subscription.included_branches
            except Exception:
                included_branches = 1
            is_additional = owner_branch_count >= included_branches
            if not change and is_additional:
                obj.is_active = False
            elif not change:
                obj.is_active = True

        super().save_model(request, obj, form, change)

        if owner:
            # Remove this branch from other owner accounts, then assign the
            # selected owner. This prevents orphaned or multiply-owned branches.
            for previous_owner in User.objects.filter(managed_pharmacies=obj, role=User.Role.ADMIN).exclude(pk=owner.pk):
                previous_owner.managed_pharmacies.remove(obj)
            owner.managed_pharmacies.add(obj)

            # First branch is included. Any branch beyond the first gets a
            # billable add-on, unless it already has one.
            owner_branch_count = owner.managed_pharmacies.exclude(pk=obj.pk).count()
            try:
                included_branches = owner.subscription.included_branches
            except Exception:
                included_branches = 1
            if owner_branch_count >= included_branches and (not change or not was_already_managed_by_owner):
                price = PaymentSettings.load().branch_addon_price
                addon, _ = BranchAddOn.objects.get_or_create(
                    pharmacy=obj,
                    defaults={'owner': owner, 'monthly_price': price},
                )
                if addon.owner_id != owner.pk:
                    addon.owner = owner
                if not addon.monthly_price:
                    addon.monthly_price = price
                addon.save(update_fields=['owner', 'monthly_price'])
                if addon.status != BranchAddOn.Status.ACTIVE:
                    obj.is_active = False
                    Pharmacy.objects.filter(pk=obj.pk).update(is_active=False)
            else:
                # If an existing branch is reassigned as the owner's included
                # branch, do not leave a stale add-on locking it.
                if existing_addon and existing_addon.status != BranchAddOn.Status.CANCELLED:
                    existing_addon.delete()
                Pharmacy.objects.filter(pk=obj.pk).update(is_active=True)
        else:
            # Blank owner means explicitly unassign the branch. It must not
            # remain attached to a previous owner by accident.
            for previous_owner in User.objects.filter(managed_pharmacies=obj):
                previous_owner.managed_pharmacies.remove(obj)
            if existing_addon and existing_addon.status != BranchAddOn.Status.CANCELLED:
                existing_addon.delete()
            Pharmacy.objects.filter(pk=obj.pk).update(is_active=False)


@admin.register(BranchAddOn)
class BranchAddOnAdmin(admin.ModelAdmin):
    list_display = ('pharmacy', 'owner', 'package_tier', 'billing_plan', 'billing_amount', 'status', 'payment_reference', 'requested_at', 'next_billing_date', 'confirmed_by')
    list_filter = ('status', 'requested_at')
    search_fields = ('pharmacy__name', 'owner__username', 'owner__phone_number')
    readonly_fields = ('requested_at', 'activated_at', 'cancelled_at', 'confirmed_by')


@admin.register(ReceiptSettings)
class ReceiptSettingsAdmin(admin.ModelAdmin):
    list_display = ('pharmacy', 'primary_color', 'paper_width_mm', 'font_family', 'updated_at')
    search_fields = ('pharmacy__name',)


@admin.register(BranchBillingEvent)
class BranchBillingEventAdmin(admin.ModelAdmin):
    """Read-only audit trail of branch billing milestones and renewals."""
    list_display = ('pharmacy', 'kind', 'stage', 'due_date', 'detail', 'created_at')
    list_filter = ('kind', 'stage')
    search_fields = ('pharmacy__name', 'detail')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
