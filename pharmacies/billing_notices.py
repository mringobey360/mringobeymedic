"""Proactive per-branch billing reminders (in-app + SMS) and audit trail.

Idempotent: each (branch, due date, milestone) is sent once, so it is safe to run
hourly or manually. If a run is missed, the next run sends the most relevant
milestone only (never a burst of old reminders).
"""
import logging

from django.utils import timezone

from .branch_billing import branch_billing_state, policy, GRACE, READ_ONLY, LOCKED
from .models import BranchAddOn, BranchBillingEvent, Pharmacy

logger = logging.getLogger(__name__)


def milestone_for(state, cfg=None):
    """Most relevant reminder milestone for a state, or '' when none is due."""
    cfg = cfg or policy()
    if state is None:
        return ''
    if state['stage'] == LOCKED:
        return 'locked'
    if state['stage'] == READ_ONLY:
        return 'readonly'
    if state['stage'] == GRACE:
        return 'grace-last' if (cfg['grace'] > 1 and state['days_overdue'] == cfg['grace']) else 'grace'
    d = state['days_left']
    if d is None:
        return ''
    if d == 0:
        return 'due-0'
    if d <= 1:
        return 'due-1'
    if d <= 3:
        return 'due-3'
    if d <= cfg['window']:
        return f"due-{cfg['window']}" if d > 3 else ''
    return ''


def _copy(state, ms, cfg):
    name = state['name']
    due = state['due_date']
    due_txt = f"{due:%d %b %Y}" if due else 'haijawekwa'
    start_txt = 'haijabainishwa'
    try:
        from hq.models import Subscription
        if state.get('kind') == 'PRIMARY':
            sub = Subscription.objects.filter(owner__managed_pharmacies__id=state.get('pharmacy_id')).order_by('-end_date').first()
            if sub and sub.start_date:
                start_txt = f"{sub.start_date:%d %b %Y}"
        else:
            addon = BranchAddOn.objects.filter(pharmacy_id=state.get('pharmacy_id')).first()
            activated = getattr(addon, 'activated_at', None) if addon else None
            if activated:
                start_txt = f"{timezone.localtime(activated).date():%d %b %Y}"
    except Exception:
        logger.debug('Could not resolve billing start date for branch %s', state.get('pharmacy_id'), exc_info=True)
    if ms.startswith('due-'):
        d = state['days_left']
        when = 'inaisha leo' if d == 0 else f'inaisha baada ya siku {d}'
        return ('Branch subscription due soon', f"Tawi: {name}. Kipindi cha usajili: {start_txt} hadi {due_txt}. Usajili {when}. Hali: {state['stage_label']}. Lipia mapema kuepuka usumbufu.",
                'WARNING', f"SmartPOS - Tawi: {name}\nUsajili: {start_txt} hadi {due_txt}\nHali: {state['stage_label']}\n{when.capitalize()}. Lipia kupitia SmartPOS kuepuka usumbufu.")
    if ms in ('grace', 'grace-last'):
        left = state['grace_days_left']
        return ('Branch subscription overdue', f"Tawi: {name}. Kipindi cha usajili: {start_txt} hadi {due_txt}. Usajili umeisha; tawi linaendelea kwa siku {left}, kisha litakuwa read-only. Hali: {state['stage_label']}.",
                'DANGER', f"SmartPOS - Tawi: {name}\nUsajili umeisha ({start_txt} hadi {due_txt}).\nSiku {left} zimebaki kabla ya read-only.\nLipia sasa kupitia SmartPOS.")
    if ms == 'readonly':
        return ('Branch is read-only', f"Tawi: {name}. Kipindi cha usajili: {start_txt} hadi {due_txt}. Hali: read-only; mauzo mapya yamesitishwa. Litafungwa baada ya siku {state['lock_in_days']}.",
                'DANGER', f"SmartPOS - Tawi: {name}\nHali: read-only, mauzo mapya yamesitishwa.\nLitafungwa baada ya siku {state['lock_in_days']}.\nLipia sasa kupitia SmartPOS.")
    return ('Branch locked', f"Tawi: {name}. Kipindi cha usajili: {start_txt} hadi {due_txt}. Hali: limefungwa kwa sababu usajili umeisha. Data imehifadhiwa; litafunguka baada ya malipo kuthibitishwa.",
            'DANGER', f"SmartPOS - Tawi: {name}\nTawi limefungwa kwa sababu usajili umeisha.\nData yako iko salama.\nLipia kisha subiri uthibitisho wa malipo.")


# ---------------------------------------------------------------------------
# Free-trial reminders (Kiswahili). Cost control: every SMS is ONE segment (plain GSM-7 text, under
# 160 characters, no accents or emoji, no long links) and only 5 are ever sent per trial.
# ---------------------------------------------------------------------------
def trial_milestone(state):
    """'trial-7' | 'trial-3' | 'trial-1' | 'trial-0' | 'trial-ended' | '' (not a timed trial / nothing due)."""
    if not state or state.get('kind') != 'PRIMARY' or not state.get('trial') or not state.get('trial_timed'):
        return ''
    if state['locked']:
        return 'trial-ended'
    d = state['days_left']
    if d is None or d > 7:
        return ''
    if d <= 0:
        return 'trial-0'
    if d <= 1:
        return 'trial-1'
    if d <= 3:
        return 'trial-3'
    return 'trial-7'


def trial_copy(state, ms, first_name):
    """(title, in-app body, level, sms) for a trial milestone. The SMS is always a single segment."""
    who = (first_name or 'Mteja').strip().split(' ')[0][:14]
    due = f"{state['due_date']:%d/%m}" if state.get('due_date') else ''
    if ms == 'trial-7':
        title, level = 'Free trial: siku 7 zimebaki', 'INFO'
        sms = f"SmartPOS: Habari {who}! Majaribio yako ya bure yanaisha {due} (siku 7). Lipia mapema uendelee kuuza. Data yako yote itabaki salama."
    elif ms == 'trial-3':
        title, level = 'Free trial: siku 3 zimebaki', 'WARNING'
        sms = f"SmartPOS: {who}, zimebaki siku 3 majaribio yaishe ({due}). Lipia leo kupitia Packages usikose mauzo. Data yako ni salama."
    elif ms == 'trial-1':
        title, level = 'Free trial inaisha kesho', 'WARNING'
        sms = f"SmartPOS: {who}, majaribio yako yanaisha kesho. Lipia sasa kupitia Packages uendelee kuuza bila kusimama. Data yako itabaki."
    elif ms == 'trial-0':
        title, level = 'Free trial inaisha leo', 'DANGER'
        sms = f"SmartPOS: {who}, leo ni siku ya mwisho ya majaribio. Lipia leo kupitia Packages uendelee kuuza. Data yako ni salama."
    else:
        title, level = 'Free trial imeisha', 'DANGER'
        sms = f"SmartPOS: {who}, majaribio yameisha. Data yako iko salama. Lipia kupitia SmartPOS kufungua akaunti uendelee kuuza."
    body = {
        'trial-7': f"Majaribio yako ya bure yanaisha {due}. Chagua plan mapema ili uendelee kuuza bila usumbufu. Bidhaa, mauzo na wateja wako vyote vitabaki.",
        'trial-3': f"Zimebaki siku 3. Chagua plan sasa ili usikose mauzo. Data yako yote iko salama.",
        'trial-1': "Majaribio yako yanaisha kesho. Chagua plan sasa ili uendelee kuuza bila kusimama.",
        'trial-0': "Leo ni siku ya mwisho ya majaribio. Lipia leo ili akaunti yako iendelee kufanya kazi.",
        'trial-ended': "Majaribio yameisha. Data yako imehifadhiwa; lipia ili kufungua akaunti na kuendelea kuuza.",
    }[ms]
    return title, body, level, sms


def _sms(phone, message):
    try:
        from sms_integration.models import SMSLog
        from sms_integration.utils import send_sms
        send_sms(phone, message, category=SMSLog.Category.OTHER, owner=None, consume_wallet=False)
    except Exception:
        logger.warning('Billing SMS to %s failed', phone, exc_info=True)


def process_branch_billing(today=None, send_sms=True):
    from notifications.models import Notification
    from notifications.utils import notify
    from notifications.automation import _managers
    from hq.models import Subscription

    today = today or timezone.localdate()
    cfg = policy()
    result = {'branches': 0, 'notices': 0, 'sms': 0}
    for ph in Pharmacy.objects.filter(is_active=True).select_related('addon'):
        addon = getattr(ph, 'addon', None)
        if addon is not None and addon.status != BranchAddOn.Status.ACTIVE:
            continue
        try:
            state = branch_billing_state(ph, today)
            if state is None:
                continue
            result['branches'] += 1
            ms = trial_milestone(state) or milestone_for(state, cfg)
            if state['kind'] == 'PRIMARY' and state['stage'] == LOCKED:
                Subscription.objects.filter(
                    owner_id__in=ph.managers.values_list('pk', flat=True),
                    status=Subscription.Status.ACTIVE, end_date__lt=today,
                ).update(status=Subscription.Status.EXPIRED)
            if not ms:
                continue
            key = f"branch-billing:{ph.pk}:{state['due_date']}:{ms}"
            is_trial_ms = ms.startswith('trial-')
            if not is_trial_ms:
                title, body, level, sms = _copy(state, ms, cfg)
            for manager in _managers(ph):
                if is_trial_ms:
                    title, body, level, sms = trial_copy(state, ms, manager.first_name or manager.username)
                existed = Notification.objects.filter(recipient=manager, dedupe_key=key).exists()
                if existed:
                    continue
                notify(manager, title, body, category=Notification.Category.SYSTEM,
                       level=getattr(Notification.Level, level), link_url=state['renew_url'],
                       pharmacy=ph, dedupe_key=key)
                result['notices'] += 1
                if send_sms and getattr(manager, 'phone_number', ''):
                    _sms(manager.phone_number, sms)
                    result['sms'] += 1
            if not BranchBillingEvent.objects.filter(pharmacy=ph, kind=ms, due_date=state['due_date']).exists():
                BranchBillingEvent.log(ph, ms, stage=state['stage'], due_date=state['due_date'],
                                       detail=f"{state['stage_label']}; days_left={state['days_left']}")
        except Exception:
            # One malformed branch must never stop billing notices for the others.
            logger.exception('Branch billing processing failed for pharmacy #%s', ph.pk)
    return result
