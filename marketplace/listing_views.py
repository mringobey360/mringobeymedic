"""Property / advert listings (rent, sale, land) for SmartPOS Marketplace.

Customers *enquire* (WhatsApp, call, visit request) instead of adding to a cart.
Sellers need an online store profile (SellerStore); publishing beyond the free allowance
needs the existing Online Selling subscription.
"""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, F, Prefetch, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .digital import normalise_image_name
from .models import Inquiry, Listing, ListingImage, ListingReport, OnlineServiceSubscription, SellerStore
from .views import _online_selling_active, _seller_store_for_user, _unique_slug

FREE_LISTING_LIMIT = getattr(settings, 'MARKETPLACE_FREE_LISTING_LIMIT', 3)
PAID_LISTING_LIMIT = getattr(settings, 'MARKETPLACE_PAID_LISTING_LIMIT', 100)
MAX_IMAGES = 15
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_EXT = ('.jpg', '.jpeg', '.jfif', '.jpe', '.png', '.webp')   # .jfif is an ordinary JPEG
LIVE_STATUSES = ('PENDING', 'PUBLISHED', 'RESERVED')   # these count against the plan allowance

REGIONS = [
    'Arusha', 'Dar es Salaam', 'Dodoma', 'Geita', 'Iringa', 'Kagera', 'Katavi', 'Kigoma', 'Kilimanjaro',
    'Lindi', 'Manyara', 'Mara', 'Mbeya', 'Morogoro', 'Mtwara', 'Mwanza', 'Njombe', 'Pwani', 'Rukwa',
    'Ruvuma', 'Shinyanga', 'Simiyu', 'Singida', 'Songwe', 'Tabora', 'Tanga',
    'Unguja North', 'Unguja South', 'Mjini Magharibi', 'Pemba North', 'Pemba South',
]


def _listing_limit(store):
    return PAID_LISTING_LIMIT if _online_selling_active(store) else FREE_LISTING_LIMIT


def _live_count(store, exclude_pk=None):
    qs = store.listings.filter(status__in=LIVE_STATUSES)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.count()


def _clean_phone(value):
    return re.sub(r'[^\d+]', '', value or '')[:25]


# ------------------------------------------------------------------ seller form --
class ListingForm(forms.ModelForm):
    amenities = forms.MultipleChoiceField(
        choices=Listing.AMENITY_CHOICES, required=False, widget=forms.CheckboxSelectMultiple,
        label='Features & amenities')
    region = forms.ChoiceField(choices=[('', 'Select region')] + [(r, r) for r in REGIONS])

    class Meta:
        model = Listing
        fields = ['purpose', 'property_type', 'title', 'description',
                  'price', 'price_period', 'negotiable', 'installments', 'payment_terms',
                  'region', 'district', 'ward', 'street', 'landmark', 'latitude', 'longitude',
                  'size_value', 'size_unit', 'bedrooms', 'bathrooms', 'title_type', 'surveyed', 'amenities',
                  'contact_phone', 'whatsapp_phone']
        widgets = {
            'purpose': forms.RadioSelect(),
            'description': forms.Textarea(attrs={'rows': 6, 'placeholder': 'Describe the property: condition, surroundings, what is included, rules, why it is a good choice…'}),
            'title': forms.TextInput(attrs={'placeholder': 'e.g. 3-bedroom house with fence, Mtumba'}),
            'payment_terms': forms.TextInput(attrs={'placeholder': 'e.g. 3 months rent in advance'}),
            'latitude': forms.NumberInput(attrs={'step': 'any', 'placeholder': '-6.1630000'}),
            'longitude': forms.NumberInput(attrs={'step': 'any', 'placeholder': '35.7516000'}),
            'contact_phone': forms.TextInput(attrs={'placeholder': '0712 345 678', 'inputmode': 'tel'}),
            'whatsapp_phone': forms.TextInput(attrs={'placeholder': 'Leave empty to use the phone number', 'inputmode': 'tel'}),
        }
        labels = {
            'purpose': 'Listing type', 'property_type': 'Property type', 'title': 'Headline',
            'price': 'Price (TSh)', 'price_period': 'Price is', 'negotiable': 'Price is negotiable',
            'installments': 'Payment in instalments possible', 'payment_terms': 'Payment terms',
            'district': 'District', 'ward': 'Ward (Kata)', 'street': 'Street / Mtaa / Area',
            'landmark': 'Nearby landmark', 'latitude': 'GPS latitude', 'longitude': 'GPS longitude',
            'size_value': 'Size', 'size_unit': 'Unit', 'bedrooms': 'Bedrooms', 'bathrooms': 'Bathrooms',
            'title_type': 'Ownership document', 'surveyed': 'Surveyed (kimepimwa)',
            'contact_phone': 'Contact phone', 'whatsapp_phone': 'WhatsApp number',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['description'].required = True
        self.fields['district'].required = True
        self.fields['contact_phone'].required = True
        self.fields['title_type'].choices = Listing.TitleType.choices
        if self.instance and self.instance.pk:
            self.initial['amenities'] = list(self.instance.amenities or [])

    def clean_price(self):
        price = self.cleaned_data['price']
        if price <= 0:
            raise forms.ValidationError('Enter a price greater than zero.')
        return price

    def clean_contact_phone(self):
        phone = _clean_phone(self.cleaned_data.get('contact_phone'))
        if len(re.sub(r'\D', '', phone)) < 9:
            raise forms.ValidationError('Enter a valid phone number.')
        return phone

    def clean_whatsapp_phone(self):
        phone = _clean_phone(self.cleaned_data.get('whatsapp_phone'))
        if phone and len(re.sub(r'\D', '', phone)) < 9:
            raise forms.ValidationError('Enter a valid WhatsApp number or leave it empty.')
        return phone

    def clean(self):
        data = super().clean()
        lat, lng = data.get('latitude'), data.get('longitude')
        if (lat is None) != (lng is None):
            raise forms.ValidationError('Enter both GPS latitude and longitude, or leave both empty.')
        if lat is not None and not (Decimal('-90') <= lat <= Decimal('90') and Decimal('-180') <= lng <= Decimal('180')):
            raise forms.ValidationError('The GPS position is not valid.')
        ptype = data.get('property_type')
        if ptype in (Listing.PropertyType.LAND, Listing.PropertyType.FARM):
            data['bedrooms'] = None
            data['bathrooms'] = None
        return data

    def clean_amenities(self):
        return list(self.cleaned_data.get('amenities') or [])


def _validate_images(files):
    """Return (valid_files, error_messages). Pillow verifies each file is a real image."""
    from PIL import Image
    ok, errors = [], []
    for f in files:
        name = (getattr(f, 'name', '') or '').lower()
        if not name.endswith(ALLOWED_IMAGE_EXT):
            errors.append(f'{f.name}: only JPG, JFIF, PNG or WebP photos are allowed.')
            continue
        if f.size > MAX_IMAGE_BYTES:
            errors.append(f'{f.name}: larger than 8 MB.')
            continue
        try:
            img = Image.open(f)
            img.verify()
            f.seek(0)
        except Exception:
            errors.append(f'{f.name}: this file is not a valid image.')
            continue
        if (img.format or '').upper() not in ('JPEG', 'PNG', 'WEBP'):
            errors.append(f'{f.name}: only JPG, JFIF, PNG or WebP photos are allowed.')
            continue
        ok.append(normalise_image_name(f))
    return ok, errors


def _save_images(listing, files):
    start = (listing.images.order_by('-position').values_list('position', flat=True).first() or 0) + 1
    room = MAX_IMAGES - listing.images.count()
    saved = 0
    for i, f in enumerate(files[:max(room, 0)]):
        ListingImage.objects.create(listing=listing, image=f, position=start + i)
        saved += 1
    return saved, max(len(files) - saved, 0)


# ------------------------------------------------------------------ seller views --
def _store_or_redirect(request, store_id):
    store = _seller_store_for_user(request, store_id)
    if store is None:
        raise Http404
    return store


@login_required
def seller_listings(request, store_id=None):
    store = _seller_store_for_user(request, store_id)
    if store is None:
        messages.info(request, 'Create your seller profile first, then you can add listings.')
        return redirect('marketplace:seller_store_setup')
    status = request.GET.get('status', '')
    qs = (store.listings.annotate(new_inquiries=Count('inquiries', filter=Q(inquiries__status='new'), distinct=True),
                                  inquiry_total=Count('inquiries', distinct=True))
          .prefetch_related('images'))
    counts = {s: 0 for s, _ in Listing.Status.choices}
    for row in store.listings.values('status').annotate(n=Count('pk')):
        counts[row['status']] = row['n']
    if status in counts:
        qs = qs.filter(status=status)
    live = _live_count(store)
    limit = _listing_limit(store)
    return render(request, 'marketplace/seller_listings.html', {
        'store': store, 'listings': qs.order_by('-updated_at'), 'counts': counts, 'all_count': sum(counts.values()),
        'selected_status': status, 'statuses': Listing.Status.choices, 'live_count': live, 'limit': limit,
        'subscribed': _online_selling_active(store), 'free_limit': FREE_LISTING_LIMIT,
        'auto_approve': getattr(settings, 'MARKETPLACE_LISTING_AUTO_APPROVE', False),
        'new_inquiry_total': Inquiry.objects.filter(listing__store=store, status='new').count(),
    })


@login_required
def seller_listing_form(request, store_id, pk=None):
    store = _store_or_redirect(request, store_id)
    listing = get_object_or_404(Listing, pk=pk, store=store) if pk else None
    if request.method == 'POST':
        form = ListingForm(request.POST, instance=listing)
        new_files, image_errors = _validate_images(request.FILES.getlist('images'))
        existing = listing.images.count() if listing else 0
        if existing + len(new_files) > MAX_IMAGES:
            image_errors.append(f'You can have at most {MAX_IMAGES} photos per listing.')
        if form.is_valid() and not image_errors:
            obj = form.save(commit=False)
            obj.store = store
            if not obj.pk:
                obj.slug = _unique_slug(Listing, f'{obj.title}-{obj.region}', 200, fallback='listing')
                obj.status = Listing.Status.DRAFT
            elif obj.status in (Listing.Status.PUBLISHED, Listing.Status.RESERVED) and \
                    not getattr(settings, 'MARKETPLACE_LISTING_AUTO_APPROVE', False):
                # A material edit to a live advert goes back for a quick re-check.
                obj.status = Listing.Status.PENDING
            obj.amenities = form.cleaned_data['amenities']
            obj.save()
            saved, skipped = _save_images(obj, new_files)
            if saved:
                messages.success(request, f'{saved} photo(s) added.')
            if skipped:
                messages.warning(request, f'{skipped} photo(s) were skipped (limit of {MAX_IMAGES}).')
            if obj.status == Listing.Status.PENDING:
                messages.info(request, 'Saved. Your changes are awaiting a quick review before they show publicly.')
            else:
                messages.success(request, 'Listing saved.')
            if obj.images.count() == 0:
                messages.warning(request, 'Add at least one photo — listings with photos get far more enquiries.')
            return redirect('marketplace:seller_listing_edit', store_id=store.pk, pk=obj.pk)
        for e in image_errors:
            form.add_error(None, e)
    else:
        form = ListingForm(instance=listing)
    return render(request, 'marketplace/seller_listing_form.html', {
        'store': store, 'form': form, 'listing': listing, 'max_images': MAX_IMAGES,
        'images': listing.images.all() if listing else [],
        'heading': 'Edit listing' if listing else 'New listing',
        'auto_approve': getattr(settings, 'MARKETPLACE_LISTING_AUTO_APPROVE', False),
    })


@login_required
@require_POST
def seller_listing_action(request, store_id, pk):
    store = _store_or_redirect(request, store_id)
    listing = get_object_or_404(Listing, pk=pk, store=store)
    action = request.POST.get('action', '')
    S = Listing.Status
    if action == 'submit':
        auto = getattr(settings, 'MARKETPLACE_LISTING_AUTO_APPROVE', False)
        if listing.status not in ((S.DRAFT, S.REJECTED, S.ARCHIVED, S.PENDING) if auto else (S.DRAFT, S.REJECTED, S.ARCHIVED)):
            messages.info(request, 'This listing is already submitted.')
        elif not listing.images.exists():
            messages.error(request, 'Add at least one photo before publishing.')
            return redirect('marketplace:seller_listing_edit', store_id=store.pk, pk=listing.pk)
        elif _live_count(store, exclude_pk=listing.pk) >= _listing_limit(store):
            if _online_selling_active(store):
                messages.error(request, 'You reached the listing limit of your plan. Archive or close another listing first.')
            else:
                messages.error(request, f'Free accounts can have {FREE_LISTING_LIMIT} live listings. Activate Online Selling in Seller Centre to publish more.')
        elif getattr(settings, 'MARKETPLACE_LISTING_AUTO_APPROVE', False):
            listing.status, listing.published_at, listing.review_note = S.PUBLISHED, timezone.now(), ''
            listing.save(update_fields=['status', 'published_at', 'review_note', 'updated_at'])
            messages.success(request, 'Your listing is now live.')
        else:
            listing.status, listing.review_note = S.PENDING, ''
            listing.save(update_fields=['status', 'review_note', 'updated_at'])
            messages.success(request, 'Submitted for review. It goes live as soon as it is approved.')
    elif action in ('approve', 'reject'):
        # Platform owner only: review a pending advert right from Seller Centre.
        if not (request.user.is_active and request.user.is_superuser):
            messages.error(request, 'Only the platform owner can approve listings.')
        elif listing.status != S.PENDING:
            messages.info(request, 'This listing is not awaiting review.')
        elif action == 'approve':
            listing.status, listing.published_at, listing.review_note = S.PUBLISHED, timezone.now(), ''
            listing.save(update_fields=['status', 'published_at', 'review_note', 'updated_at'])
            messages.success(request, 'Listing approved and now live.')
        else:
            listing.status = S.REJECTED
            listing.review_note = 'Please check the photos, location and contact details, then submit again.'
            listing.save(update_fields=['status', 'review_note', 'updated_at'])
            messages.success(request, 'Listing sent back for changes.')
    elif action == 'reserve' and listing.status == S.PUBLISHED:
        listing.status = S.RESERVED
        listing.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'Marked as reserved.')
    elif action == 'reopen' and listing.status in (S.RESERVED, S.CLOSED):
        listing.status = S.PUBLISHED
        listing.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'Listing is available again.')
    elif action == 'close' and listing.status in (S.PUBLISHED, S.RESERVED):
        listing.status = S.CLOSED
        listing.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'Marked as sold / rented.')
    elif action == 'archive' and listing.status != S.ARCHIVED:
        listing.status = S.ARCHIVED
        listing.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'Listing archived and hidden from the public.')
    elif action == 'delete':
        listing.delete()
        messages.success(request, 'Listing deleted.')
        return redirect('marketplace:seller_store_listings', store_id=store.pk)
    else:
        messages.error(request, 'That action is not available for this listing.')
    if request.POST.get('back') == 'edit':
        return redirect('marketplace:seller_listing_edit', store_id=store.pk, pk=listing.pk)
    return redirect('marketplace:seller_store_listings', store_id=store.pk)


@login_required
@require_POST
def seller_listing_image_action(request, store_id, pk, image_id):
    store = _store_or_redirect(request, store_id)
    listing = get_object_or_404(Listing, pk=pk, store=store)
    image = get_object_or_404(ListingImage, pk=image_id, listing=listing)
    action = request.POST.get('action')
    if action == 'delete':
        image.image.delete(save=False)
        image.delete()
        messages.success(request, 'Photo removed.')
    elif action == 'cover':
        first = listing.images.order_by('position', 'pk').first()
        if first and first.pk != image.pk:
            image.position, first.position = first.position, image.position
            if image.position == first.position:
                image.position = max(first.position - 1, 0)
            image.save(update_fields=['position'])
            first.save(update_fields=['position'])
        messages.success(request, 'Cover photo updated.')
    elif action == 'caption':
        image.caption = request.POST.get('caption', '').strip()[:120]
        image.save(update_fields=['caption'])
    return redirect('marketplace:seller_listing_edit', store_id=store.pk, pk=listing.pk)


@login_required
def seller_inquiries(request, store_id=None):
    store = _seller_store_for_user(request, store_id)
    if store is None:
        return redirect('marketplace:seller_store_setup')
    if request.method == 'POST':
        inquiry = get_object_or_404(Inquiry, pk=request.POST.get('inquiry') or 0, listing__store=store)
        status = request.POST.get('status')
        if status in dict(Inquiry.Status.choices):
            inquiry.status = status
        inquiry.seller_note = request.POST.get('seller_note', inquiry.seller_note).strip()[:300]
        inquiry.save(update_fields=['status', 'seller_note'])
        messages.success(request, 'Enquiry updated.')
        return redirect(request.get_full_path())
    selected = request.GET.get('status', '')
    qs = Inquiry.objects.filter(listing__store=store).select_related('listing')
    if selected in dict(Inquiry.Status.choices):
        qs = qs.filter(status=selected)
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    return render(request, 'marketplace/seller_inquiries.html', {
        'store': store, 'page': page, 'selected': selected, 'statuses': Inquiry.Status.choices,
        'new_total': Inquiry.objects.filter(listing__store=store, status='new').count(),
    })


# ------------------------------------------------------------------ public views --
def _public_listings():
    """Live adverts of published, active seller stores. (Draft/pending/rejected never appear.)"""
    return (Listing.objects.filter(status__in=Listing.PUBLIC_STATUSES, store__pharmacy__is_active=True)
            .select_related('store').prefetch_related(Prefetch('images', queryset=ListingImage.objects.order_by('position', 'pk'))))


def _to_decimal(value):
    try:
        return Decimal(str(value).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return None


def _q_url(qs):
    return ('?' + qs) if qs else ''


def listing_index(request, purpose=None):
    qs = _public_listings()
    params = request.GET
    purpose = (purpose or params.get('purpose') or '').upper()
    if purpose in dict(Listing.Purpose.choices):
        qs = qs.filter(purpose=purpose)
    else:
        purpose = ''
    ptype = params.get('type', '').upper()
    if ptype in dict(Listing.PropertyType.choices):
        qs = qs.filter(property_type=ptype)
    else:
        ptype = ''
    region = params.get('region', '')
    if region in REGIONS:
        qs = qs.filter(region=region)
    else:
        region = ''
    q = params.get('q', '').strip()[:80]
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(district__icontains=q) | Q(ward__icontains=q) |
                       Q(street__icontains=q) | Q(landmark__icontains=q) | Q(description__icontains=q))
    pmin, pmax = _to_decimal(params.get('min')), _to_decimal(params.get('max'))
    if pmin is not None:
        qs = qs.filter(price__gte=pmin)
    if pmax is not None:
        qs = qs.filter(price__lte=pmax)
    beds = params.get('beds', '')
    if beds.isdigit():
        qs = qs.filter(bedrooms__gte=int(beds))
    sort = params.get('sort', 'new')
    order = {'low': ('price',), 'high': ('-price',)}.get(sort, ())
    now = timezone.now()
    # Featured adverts first (Boost), then the chosen order; closed adverts always sink.
    from django.db.models import Case, IntegerField, Value, When
    qs = qs.annotate(
        _rank=Case(When(status='CLOSED', then=Value(2)), When(featured_until__gt=now, then=Value(0)), default=Value(1), output_field=IntegerField())
    ).order_by('_rank', *(order or ('-published_at', '-created_at')))
    page = Paginator(qs, 12).get_page(params.get('page'))
    keep = params.copy()
    keep.pop('page', None)
    keep.pop('partial', None)

    def _qs(**changes):
        """Current filters with some keys changed/removed (value None removes the key)."""
        q_ = params.copy()
        q_.pop('page', None)
        q_.pop('partial', None)
        for k, v in changes.items():
            if v in (None, ''):
                q_.pop(k, None)
            else:
                q_[k] = v
        return q_.urlencode()

    base_path = request.path
    type_labels = dict(Listing.PropertyType.choices)
    quick_types = [{'value': '', 'label': 'All types', 'active': not ptype,
                    'url': base_path + ('?' + _qs(type=None) if _qs(type=None) else '')}]
    for v, label in Listing.PropertyType.choices:
        quick_types.append({'value': v, 'label': label, 'active': v == ptype, 'url': base_path + '?' + _qs(type=v)})
    active_filters = []
    if q:
        active_filters.append({'label': f'\u201c{q}\u201d', 'url': base_path + _q_url(_qs(q=None))})
    if ptype:
        active_filters.append({'label': type_labels.get(ptype, ptype), 'url': base_path + _q_url(_qs(type=None))})
    if region:
        active_filters.append({'label': region, 'url': base_path + _q_url(_qs(region=None))})
    if pmin is not None:
        active_filters.append({'label': f'From TSh {pmin:,.0f}', 'url': base_path + _q_url(_qs(min=None))})
    if pmax is not None:
        active_filters.append({'label': f'Up to TSh {pmax:,.0f}', 'url': base_path + _q_url(_qs(max=None))})
    if beds.isdigit():
        active_filters.append({'label': f'{beds}+ bedrooms', 'url': base_path + _q_url(_qs(beds=None))})
    # Purpose tabs keep the other filters.
    tab_qs = _qs(purpose=None)
    # Compact page window for the pager.
    num, last = page.number, page.paginator.num_pages
    window = [n for n in range(max(1, num - 2), min(last, num + 2) + 1)]
    next_url = ''
    if page.has_next():
        nq = keep.copy()
        nq['page'] = page.next_page_number()
        next_url = base_path + '?' + nq.urlencode()
    if params.get('partial') == '1':
        # "Show more" (progressive loading): only the next batch of cards, as JSON.
        try:
            asked = int(params.get('page') or 1)
        except ValueError:
            asked = 1
        if asked > page.paginator.num_pages:
            return JsonResponse({'html': '', 'next': '', 'total': page.paginator.count})
        html = ''.join(render_to_string('marketplace/_listing_card.html', {'l': l}, request=request) for l in page.object_list)
        return JsonResponse({'html': html, 'next': next_url, 'total': page.paginator.count})
    return render(request, 'marketplace/listing_index.html', {
        'page': page, 'purpose': purpose, 'ptype': ptype, 'region': region, 'q': q, 'sort': sort,
        'pmin': params.get('min', ''), 'pmax': params.get('max', ''), 'beds': beds,
        'regions': REGIONS, 'types': Listing.PropertyType.choices, 'querystring': keep.urlencode(),
        'total': page.paginator.count, 'quick_types': quick_types, 'active_filters': active_filters,
        'has_filters': bool(active_filters), 'has_adv': bool(region or pmin is not None or pmax is not None or beds.isdigit()),
        'tab_qs': tab_qs, 'page_window': window, 'last_page': last,
        'clear_url': base_path, 'next_url': next_url,
    })


def listing_detail(request, slug):
    listing = get_object_or_404(_public_listings(), slug=slug)
    seen = request.session.get('seen_listings', [])
    if listing.pk not in seen:
        Listing.objects.filter(pk=listing.pk).update(views_count=F('views_count') + 1)
        request.session['seen_listings'] = (seen + [listing.pk])[-50:]
    images = list(listing.images.all())
    similar = (_public_listings().filter(purpose=listing.purpose, property_type=listing.property_type, region=listing.region)
               .exclude(pk=listing.pk).exclude(status='CLOSED')[:4])
    initial = request.session.get('inquiry_contact', {})
    return render(request, 'marketplace/listing_detail.html', {
        'listing': listing, 'images': images, 'similar': similar, 'store': listing.store, 'initial': initial,
        'report_reasons': ListingReport.Reason.choices,
    })


@require_POST
def listing_inquiry(request, slug):
    listing = get_object_or_404(_public_listings(), slug=slug)
    back = redirect('marketplace:listing_detail', slug=listing.slug)
    if request.POST.get('website'):            # honeypot: real people never fill this
        return back
    if listing.status == Listing.Status.CLOSED:
        messages.error(request, 'This property is no longer available.')
        return back
    name = request.POST.get('name', '').strip()[:120]
    phone = _clean_phone(request.POST.get('phone'))
    message = request.POST.get('message', '').strip()[:1000]
    visit = request.POST.get('visit_date', '').strip()
    if not name or len(re.sub(r'\D', '', phone)) < 9:
        messages.error(request, 'Enter your name and a valid phone number so the owner can reach you.')
        return back
    ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR', ''))
    key = f'listing-inq:{listing.pk}:{ip}'
    if cache.get(key, 0) >= 3:
        messages.error(request, 'You have sent several enquiries for this property already. The owner will contact you.')
        return back
    cache.set(key, cache.get(key, 0) + 1, 3600)
    visit_date = None
    if visit:
        try:
            visit_date = datetime.strptime(visit, '%Y-%m-%d').date()
            if visit_date < timezone.localdate():
                visit_date = None
        except ValueError:
            visit_date = None
    inquiry = Inquiry.objects.create(listing=listing, name=name, phone=phone, message=message, visit_date=visit_date)
    _send_lead_sms(listing, inquiry)
    request.session['inquiry_contact'] = {'name': name, 'phone': phone}
    messages.success(request, 'Your enquiry was sent. The owner will contact you shortly.')
    return back


def _send_lead_sms(listing, inquiry):
    """Lead SMS: tell the seller at once. Uses the seller's paid Order SMS add-on allowance (never the buyer's money).
    Best effort: a failed SMS never blocks the enquiry."""
    try:
        from django.db.models import F as _F
        addon = (OnlineServiceSubscription.objects
                 .filter(store=listing.store, service=OnlineServiceSubscription.Service.ORDER_SMS,
                         status=OnlineServiceSubscription.Status.ACTIVE, period_end__gt=timezone.now(),
                         sms_used__lt=_F('sms_quota')).first())
        if not addon or not listing.contact_phone:
            return
        from sms_integration.models import SMSLog
        from sms_integration.utils import send_sms
        text = f"SmartPOS: New enquiry on {listing.title[:35]} from {inquiry.name} {inquiry.phone}. Open Seller Centre > Enquiries."
        if send_sms(listing.contact_phone, text, category=SMSLog.Category.OTHER, owner=None, consume_wallet=False):
            OnlineServiceSubscription.objects.filter(pk=addon.pk, sms_used__lt=_F('sms_quota')).update(sms_used=_F('sms_used') + 1)
    except Exception:
        import logging
        logging.getLogger(__name__).warning('Lead SMS failed for listing %s', listing.pk, exc_info=True)


@require_POST
def listing_report(request, slug):
    listing = get_object_or_404(_public_listings(), slug=slug)
    back = redirect('marketplace:listing_detail', slug=listing.slug)
    if request.POST.get('website'):
        return back
    reason = request.POST.get('reason', '')
    if reason not in dict(ListingReport.Reason.choices):
        messages.error(request, 'Choose a reason for the report.')
        return back
    ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR', ''))
    key = f'listing-rep:{ip}'
    if cache.get(key, 0) >= 5:
        messages.error(request, 'Too many reports from this device. Please try again later.')
        return back
    cache.set(key, cache.get(key, 0) + 1, 3600)
    ListingReport.objects.create(listing=listing, reason=reason, details=request.POST.get('details', '').strip()[:500],
                                 reporter_phone=_clean_phone(request.POST.get('phone')))
    messages.success(request, 'Thank you. Our team will review this advert.')
    return back


def _listing_url(request, listing):
    return request.build_absolute_uri(reverse('marketplace:listing_detail', args=[listing.slug]))


def _make_qr(url):
    """Error level M keeps the code less dense (easier to scan from print) than H for long URLs."""
    import segno
    return segno.make(url, error='m', boost_error=False)


def _qr_inline_svg(url):
    """SVG markup embedded directly in the flyer HTML (vector, always sharp in print/PDF, never a broken <img>)."""
    qr = _make_qr(url)
    svg = qr.svg_inline(scale=10, border=4, dark='#0b1f1c', light='#ffffff', omitsize=True)
    return svg.replace('<svg ', '<svg class="qr-svg" shape-rendering="crispEdges" role="img" aria-label="QR code" preserveAspectRatio="xMidYMid meet" ', 1)


def listing_qr(request, slug):
    """Standalone QR code (SVG file) that opens the listing: for signboards, posters, WhatsApp.

    NOTE: segno's svg_inline() drops the xmlns/XML declaration (it is meant for inline HTML only),
    which makes <img src="...svg"> render as a broken image. Here we emit a complete SVG document.
    """
    import io
    listing = get_object_or_404(_public_listings(), slug=slug)
    qr = _make_qr(_listing_url(request, listing))
    buf = io.BytesIO()
    qr.save(buf, kind='svg', scale=10, border=4, dark='#000000', light='#ffffff', xmldecl=True, svgns=True, nl=False)
    response = HttpResponse(buf.getvalue(), content_type='image/svg+xml')
    response['Cache-Control'] = 'public, max-age=3600'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def listing_qr_png(request, slug):
    """High-resolution PNG QR (for printers/apps that do not handle SVG). ?download=1 forces a download."""
    import io
    listing = get_object_or_404(_public_listings(), slug=slug)
    qr = _make_qr(_listing_url(request, listing))
    buf = io.BytesIO()
    qr.save(buf, kind='png', scale=16, border=4, dark='#000000', light='#ffffff')
    response = HttpResponse(buf.getvalue(), content_type='image/png')
    response['Cache-Control'] = 'public, max-age=3600'
    response['X-Content-Type-Options'] = 'nosniff'
    if request.GET.get('download'):
        response['Content-Disposition'] = f'attachment; filename="qr-{listing.slug[:60]}.png"'
    return response


def listing_flyer(request, slug):
    """Printable A4 flyer (photos, price, location, QR). Use the browser's Print > Save as PDF."""
    listing = get_object_or_404(_public_listings(), slug=slug)
    url = _listing_url(request, listing)
    try:
        qr_svg = _qr_inline_svg(url)
    except Exception:  # never break the flyer because of the QR
        qr_svg = ''
    return render(request, 'marketplace/listing_flyer.html', {
        'listing': listing, 'images': list(listing.images.all())[:4], 'store': listing.store,
        'url': url, 'qr_svg': qr_svg,
        'qr_png_url': reverse('marketplace:listing_qr_png', args=[listing.slug]),
    })
