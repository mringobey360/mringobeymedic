"""
Prospects (watarajiwa) - business owners who are not SmartPOS customers yet.

The platform owner collects their phone numbers here and sends them official
SmartPOS invitations (with the SmartPOS link). Sending reuses the announcement
pipeline (SMSAnnouncement + SMSAnnouncementRecipient + background sender), so
every invitation has the same audit trail, delivery report and retry button.
"""
import re

from django.utils import timezone

from accounts.phone_utils import is_sms_deliverable, normalize_phone_number

# No opt-out sentence is added to invitations (removed on request). Opting a number out is done by the
# administrator with the "Opt out" button on the prospect.
OPTOUT_LINE = ''
DEFAULT_GREETING_NAME = 'Ndugu'

# Official invitation messages - the SMS itself is in Swahili (the admin screens are in English).
# Detailed messages that explain what SmartPOS does for the customer. Plain GSM-7 (no accents) so that each SMS
# part holds the maximum characters (153 per part for multi-part messages). Each template is written to
# stay within 3 SMS parts including title and signature.
PROSPECT_TEMPLATES = [
    {
        'key': 'intro', 'label': 'Introduction',
        'title': 'SmartPOS - Simamia Biashara Yako',
        'text': 'Habari {name}, karibu ujaribu SmartPOS, mfumo wa kusimamia biashara kwa simu au kompyuta. '
                'Rekodi mauzo, fuatilia stoo na madeni ya wateja, kisha pokea ripoti ya kila siku kwa SMS. '
                'Jisajili hapa: {link}',
    },
    {
        'key': 'benefits', 'label': 'Benefits',
        'title': 'Jua Faida ya Biashara Yako',
        'text': 'Habari {name}, je, unajua faida halisi ya biashara yako kila siku? '
                'SmartPOS inakuonyesha mauzo, matumizi, faida, madeni na bidhaa zinazokaribia kuisha, popote ulipo. '
                'Anza leo: {link}',
    },
    {
        'key': 'branches', 'label': 'Many branches',
        'title': 'SmartPOS kwa Matawi Yote',
        'text': 'Habari {name}, una duka au tawi zaidi ya moja? '
                'SmartPOS inakuwezesha kuona mauzo na stoo ya kila tawi, kufuatilia wafanyakazi na kupata ripoti '
                'zote kwenye simu yako. Jisajili: {link}',
    },
    {
        'key': 'welcome', 'label': 'Welcome',
        'title': 'Karibu SmartPOS',
        'text': 'Habari {name}, karibu SmartPOS. Kujisajili ni rahisi: weka taarifa za biashara yako, ongeza '
                'bidhaa, kisha anza kuuza. Tuko tayari kukusaidia kila hatua. Jisajili hapa: {link}',
    },
    {
        'key': 'followup', 'label': 'Follow-up',
        'title': 'SmartPOS - Tunakukumbusha',
        'text': 'Habari {name}, tulikutumia ujumbe kuhusu SmartPOS, mfumo unaokusaidia kudhibiti mauzo, stoo, '
                'madeni na wafanyakazi wako. Ukihitaji maelezo zaidi au msaada wa kujisajili, tutakuhudumia. '
                'Tembelea: {link}',
    },
]

_PHONE_RE = re.compile(r'\+?\d[\d \-().]{7,}\d')
_EDGE = ' ,;:|-\t'


def parse_contacts(raw):
    """Split pasted text (one business per line) into [(name, raw_phone)] and [(line, reason)].

    Accepts: '0712345678', 'Juma, 0712 345 678', '0712345678 Duka la Juma', '+255712345678;Neema'.
    A line holding several numbers becomes one nameless entry per number.
    """
    entries, bad = [], []
    for line in re.split(r'[\r\n]+', raw or ''):
        line = line.strip()
        if not line:
            continue
        matches = list(_PHONE_RE.finditer(line))
        phones = []
        for m in matches:
            # Two numbers separated only by a space are matched as one long run - split them again.
            if len(re.sub(r'\D', '', m.group())) > 13:
                phones.extend(m.group().split())
            else:
                phones.append(m.group())
        if not phones:
            bad.append((line[:60], 'No phone number found on this line.'))
        elif len(phones) == 1:
            m = matches[0]
            name = re.sub(r'\s+', ' ', f'{line[:m.start()]} {line[m.end():]}').strip(_EDGE)
            entries.append((name[:150], phones[0]))
        else:
            entries.extend(('', ph) for ph in phones)
    return entries, bad


def clean_phone(raw):
    """Return (normalized, error). error is '' when the number can receive SMS."""
    try:
        phone = normalize_phone_number(raw)
    except Exception as exc:  # forms.ValidationError
        return '', ' '.join(getattr(exc, 'messages', [str(exc)]))
    if not is_sms_deliverable(phone):
        return '', 'This number cannot receive SMS (use a mobile number).'
    return phone, ''


def register_prospects(entries, created_by=None, business_type='OTHER', region=''):
    """Save parsed contacts. Returns {'added': n, 'duplicates': n, 'invalid': [(raw, why)]}."""
    from .models import SMSProspect

    cleaned, invalid, seen = [], [], set()
    result = {'added': 0, 'duplicates': 0, 'invalid': invalid}
    for name, raw in entries:
        phone, err = clean_phone(raw)
        if err:
            invalid.append((raw.strip()[:30], err))
        elif phone in seen:
            result['duplicates'] += 1
        else:
            seen.add(phone)
            cleaned.append((name, phone))

    existing = dict(SMSProspect.objects.filter(phone_number__in=[p for _, p in cleaned])
                    .values_list('phone_number', 'name'))
    new_rows = []
    for name, phone in cleaned:
        if phone in existing:
            result['duplicates'] += 1
            if name and not existing[phone]:   # fill in a name we did not have yet
                SMSProspect.objects.filter(phone_number=phone, name='').update(name=name)
            continue
        new_rows.append(SMSProspect(phone_number=phone, name=name, business_type=business_type,
                                    region=(region or '')[:60], created_by=created_by))
    SMSProspect.objects.bulk_create(new_rows)
    result['added'] = len(new_rows)
    return result


def mark_existing_customers_as_joined():
    """Prospects whose number now belongs to a customer account become JOINED (never messaged again)."""
    from .announcements import customers_queryset
    from .models import SMSProspect

    phones = set()
    for raw in customers_queryset().exclude(phone_number__isnull=True).exclude(phone_number='').values_list('phone_number', flat=True):
        phone, err = clean_phone(raw)
        if phone:
            phones.add(phone)
    if not phones:
        return 0
    return SMSProspect.objects.filter(phone_number__in=phones).exclude(
        status=SMSProspect.Status.JOINED).update(status=SMSProspect.Status.JOINED)


def eligible_queryset():
    from .models import SMSProspect
    return SMSProspect.objects.exclude(status__in=[SMSProspect.Status.OPTED_OUT, SMSProspect.Status.JOINED])


def resolve_prospects(mode, selected_ids=None, business_type='', region=''):
    """mode: 'all' (every eligible number) | 'new' (never messaged) | 'selected' (ticked in the list)."""
    qs = eligible_queryset()
    if mode == 'new':
        qs = qs.filter(sms_count=0)
    elif mode == 'selected':
        qs = qs.filter(pk__in=list(selected_ids or []))
    if business_type:
        qs = qs.filter(business_type=business_type)
    if region:
        qs = qs.filter(region__iexact=region)
    return qs.order_by('created_at', 'id')


def render_prospect_body(template, prospect, link_url='', include_optout=True):
    first = (prospect.name or '').strip().split(' ')[0] or DEFAULT_GREETING_NAME
    text = (template or '').replace('{name}', first).replace('{link}', link_url or '').strip()
    if include_optout and OPTOUT_LINE:
        text = f'{text}\n{OPTOUT_LINE}'
    return text


def build_prospect_recipients(announcement, prospects, include_optout=True):
    """One recipient row per prospect (PENDING, final text stored) - mirrors announcements.build_recipients."""
    from .announcements import _Delivery, compose_customer_message, refresh_counters
    from .models import SMSAnnouncementRecipient
    from .utils import sms_segment_count

    rows = []
    for p in prospects:
        body = render_prospect_body(announcement.message, p, announcement.link_url, include_optout)
        text = compose_customer_message(announcement.title, '', body, include_administrator_footer=True)
        rows.append(SMSAnnouncementRecipient(
            announcement=announcement, prospect=p, display_name=p.display_name, phone_number=p.phone_number,
            status=_Delivery.PENDING, message=text, segments=sms_segment_count(text)))
    SMSAnnouncementRecipient.objects.bulk_create(rows)
    refresh_counters(announcement, save=True, total=len(rows))
    return len(rows)


def mark_prospects_contacted(rows, when=None):
    """Called by the sender for rows that were accepted by the provider."""
    from django.db.models import F
    from .models import SMSProspect

    ids = [r.prospect_id for r in rows if r.prospect_id]
    if not ids:
        return
    when = when or timezone.now()
    SMSProspect.objects.filter(pk__in=ids).update(sms_count=F('sms_count') + 1, last_sms_at=when)
    SMSProspect.objects.filter(pk__in=ids, status=SMSProspect.Status.NEW).update(status=SMSProspect.Status.CONTACTED)
