from django.contrib import admin
from .refs import short_reference
from .models import Category, Product, Order, OrderItem, OrderStatusEvent, PaymentAttempt

class OwnerOnlyAdmin(admin.ModelAdmin):
    def has_module_permission(self, request): return request.user.is_active and request.user.is_superuser
    def has_view_permission(self, request, obj=None): return request.user.is_active and request.user.is_superuser
    def has_add_permission(self, request): return request.user.is_active and request.user.is_superuser
    def has_change_permission(self, request, obj=None): return request.user.is_active and request.user.is_superuser
    def has_delete_permission(self, request, obj=None): return request.user.is_active and request.user.is_superuser

@admin.register(Category)
class CategoryAdmin(OwnerOnlyAdmin):
    list_display=('name','seller_store','is_active')
    list_filter=('is_active',)
    search_fields=('name','seller_store__public_name')
    prepopulated_fields={'slug':('name',)}

@admin.register(Product)
class ProductAdmin(OwnerOnlyAdmin):
    list_display=('name','seller_name','kind','category','price','stock','featured','is_active','updated_at')
    list_filter=('kind','is_movie','category','featured','is_active')
    exclude=('digital_file',)   # sellers' paid files are private: never rendered or linked from the admin
    search_fields=('name','seller_name','description')
    prepopulated_fields={'slug':('name',)}

class OrderItemInline(admin.TabularInline):
    model=OrderItem
    extra=0
    readonly_fields=('product','name','quantity','unit_price','is_digital')
    can_delete=False
    def has_add_permission(self, request, obj=None): return False

class OrderStatusEventInline(admin.TabularInline):
    model=OrderStatusEvent
    extra=0
    readonly_fields=('status','note','actor','created_at')
    can_delete=False
    def has_add_permission(self, request, obj=None): return False

@admin.register(Order)
class OrderAdmin(OwnerOnlyAdmin):
    list_display=('reference','seller_store','customer_name','phone','total','status','payment_status','created_at')
    list_filter=('status','payment_status','created_at')
    search_fields=('customer_name','phone','location')
    readonly_fields=('reference','customer_name','phone','location','notes','total','created_at')
    inlines=[OrderItemInline, OrderStatusEventInline]
    def has_add_permission(self, request): return False
    # Superusers may delete orders (cascades to items, events, download grants).

from .models import SellerStore, MerchantPaymentProfile
admin.site.register(MerchantPaymentProfile)   # SellerStore has its own ModelAdmin below

from .models import OnlineServiceSubscription
@admin.register(OnlineServiceSubscription)
class OnlineServiceSubscriptionAdmin(OwnerOnlyAdmin):
    list_display = ('store','service','status','monthly_amount','period_end','last_payment_reference')
    list_filter = ('service','status')
    search_fields = ('store__public_name','store__pharmacy__name','last_payment_reference')
    readonly_fields = ('created_at','updated_at')

from .models import OnlineServiceRequest
@admin.register(OnlineServiceRequest)
class OnlineServiceRequestAdmin(OwnerOnlyAdmin):
    list_display = ('id', 'store', 'service', 'status', 'amount', 'requested_by', 'created_at', 'reviewed_by')
    list_filter = ('service', 'status', 'created_at')
    search_fields = ('store__public_name', 'store__pharmacy__name', 'requested_by__username', 'admin_note')
    readonly_fields = ('created_at', 'updated_at', 'reviewed_at')


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(OwnerOnlyAdmin):
    """Support lookup: search by the short code the customer sees (e.g. SP-7CA55820), name or phone."""
    list_display = ('short_code', 'customer_name', 'phone', 'total', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('reference', 'customer_name', 'phone')
    readonly_fields = ('reference', 'created_at', 'updated_at')

    @admin.display(description='Reference')
    def short_code(self, obj):
        return short_reference(obj.reference)


# ---------------------------------------------------------------- property listings --
from datetime import timedelta as _timedelta
from django.utils import timezone as _tz
from .models import Listing, ListingImage, Inquiry, ListingReport, SellerStore


class ListingImageInline(admin.TabularInline):
    model = ListingImage
    extra = 0


@admin.register(Listing)
class ListingAdmin(OwnerOnlyAdmin):
    """Platform owner reviews new adverts here before they go public (scam protection)."""
    list_display = ('title', 'store', 'purpose', 'property_type', 'region', 'price', 'status', 'views_count', 'featured_until', 'created_at')
    list_filter = ('status', 'purpose', 'property_type', 'region')
    search_fields = ('title', 'store__public_name', 'district', 'ward', 'contact_phone')
    inlines = [ListingImageInline]
    actions = ['approve', 'reject', 'feature_7_days', 'unfeature']
    list_editable = ('status',)

    @admin.action(description='Approve & publish selected listings')
    def approve(self, request, queryset):
        n = queryset.filter(status__in=('PENDING', 'REJECTED', 'DRAFT')).update(status='PUBLISHED', published_at=_tz.now(), review_note='')
        self.message_user(request, f'{n} listing(s) published.')

    @admin.action(description='Send back for changes (needs changes)')
    def reject(self, request, queryset):
        n = queryset.filter(status='PENDING').update(status='REJECTED', review_note='Please check the photos, location and contact details, then submit again.')
        self.message_user(request, f'{n} listing(s) sent back to the seller.')

    @admin.action(description='Feature for 7 days (Boost)')
    def feature_7_days(self, request, queryset):
        n = queryset.filter(status__in=('PUBLISHED', 'RESERVED')).update(featured_until=_tz.now() + _timedelta(days=7))
        self.message_user(request, f'{n} listing(s) featured for 7 days.')

    @admin.action(description='Remove featured status')
    def unfeature(self, request, queryset):
        queryset.update(featured_until=None)


@admin.register(Inquiry)
class InquiryAdmin(OwnerOnlyAdmin):
    list_display = ('name', 'phone', 'listing', 'status', 'visit_date', 'created_at')
    list_filter = ('status',)
    search_fields = ('name', 'phone', 'listing__title')


@admin.register(ListingReport)
class ListingReportAdmin(OwnerOnlyAdmin):
    list_display = ('listing', 'reason', 'details', 'reporter_phone', 'handled', 'created_at')
    list_filter = ('handled', 'reason')
    actions = ['mark_handled', 'hide_reported_listings']

    @admin.action(description='Mark as handled')
    def mark_handled(self, request, queryset):
        queryset.update(handled=True)

    @admin.action(description='Hide the reported listings (send back to seller) and mark handled')
    def hide_reported_listings(self, request, queryset):
        ids = list(queryset.values_list('listing_id', flat=True))
        Listing.objects.filter(pk__in=ids, status__in=('PUBLISHED', 'RESERVED')).update(status='REJECTED', review_note='Removed pending review after a report. Please contact support.')
        queryset.update(handled=True)


from django import forms as _forms
from .social import PLATFORMS as _SOCIAL, compute_verified as _compute_verified


class SellerStoreAdminForm(_forms.ModelForm):
    """Adds one tick-box per social platform. Ticking records the *exact link* that was verified,
    so the badge disappears automatically if the seller later changes that link."""
    verify_whatsapp = _forms.BooleanField(required=False, label='WhatsApp verified')
    verify_instagram = _forms.BooleanField(required=False, label='Instagram verified')
    verify_tiktok = _forms.BooleanField(required=False, label='TikTok verified')
    verify_facebook = _forms.BooleanField(required=False, label='Facebook verified')
    verify_youtube = _forms.BooleanField(required=False, label='YouTube verified')

    class Meta:
        model = SellerStore
        exclude = ('social_verified',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            for key in self.instance.verified_social_keys:
                self.initial[f'verify_{key}'] = True

    def save(self, commit=True):
        obj = super().save(commit=False)
        wanted = [k for k in _SOCIAL if self.cleaned_data.get(f'verify_{k}')]
        obj.social_verified = _compute_verified({k: getattr(obj, c['field']) for k, c in _SOCIAL.items()}, wanted)
        if commit:
            obj.save()
            self._save_m2m()
        return obj


@admin.register(SellerStore)
class SellerStoreAdmin(OwnerOnlyAdmin):
    form = SellerStoreAdminForm
    list_display = ('public_name', 'pharmacy', 'is_published', 'verified', 'social_status', 'created_at')
    list_filter = ('is_published', 'verified')
    search_fields = ('public_name', 'slug', 'pharmacy__name')
    actions = ['mark_verified', 'remove_verified', 'verify_social_links', 'remove_social_verification']

    @admin.display(description='Social links')
    def social_status(self, obj):
        links = obj.social_links
        return f"{sum(1 for l in links if l['verified'])}/{len(links)} verified" if links else '-'

    @admin.action(description='Verify ALL current social links of selected stores')
    def verify_social_links(self, request, queryset):
        for store in queryset:
            urls = {k: getattr(store, c['field']) for k, c in _SOCIAL.items()}
            queryset.model.objects.filter(pk=store.pk).update(social_verified=_compute_verified(urls, list(_SOCIAL)))

    @admin.action(description='Remove social-link verification from selected stores')
    def remove_social_verification(self, request, queryset):
        queryset.update(social_verified={})

    @admin.action(description='Mark as Verified seller')
    def mark_verified(self, request, queryset):
        queryset.update(verified=True)

    @admin.action(description='Remove Verified badge')
    def remove_verified(self, request, queryset):
        queryset.update(verified=False)


from .models import DownloadGrant


@admin.register(DownloadGrant)
class DownloadGrantAdmin(OwnerOnlyAdmin):
    list_display=('order_item','download_count','max_downloads','revoked','last_downloaded_at','created_at')
    list_filter=('revoked',)
    readonly_fields=('token','order_item','download_count','last_downloaded_at','created_at')
    search_fields=('order_item__name','order_item__order__customer_name','order_item__order__phone')


from .models import ProductReview

@admin.register(ProductReview)
class ProductReviewAdmin(OwnerOnlyAdmin):
    list_display=('product','rating','customer_name','short_comment','is_visible','created_at')
    list_filter=('is_visible','rating','created_at')
    search_fields=('product__name','customer_name','comment')
    readonly_fields=('product','order','rating','customer_name','created_at','updated_at')
    actions=['hide_reviews','show_reviews']
    def has_add_permission(self, request): return False
    def short_comment(self, obj): return (obj.comment or '')[:60]
    @admin.action(description='Hide selected reviews')
    def hide_reviews(self, request, queryset):
        for r in queryset: r.is_visible=False; r.save(update_fields=['is_visible','updated_at'])
    @admin.action(description='Show selected reviews')
    def show_reviews(self, request, queryset):
        for r in queryset: r.is_visible=True; r.save(update_fields=['is_visible','updated_at'])
