"""
Free, offline "smart FAQ" engine for the built-in assistant.

This is the DEFAULT brain of the assistant and needs NO paid API key, no
internet call, and no ongoing cost — it runs entirely inside this Django
process. It works by matching the visitor's message against a curated list
of Kiswahili/English keyword phrases (see STAFF_FAQ / PUBLIC_FAQ below) and
returning the best-matching, pre-written answer. A few answers (pricing,
this visitor's signup status, this user's subscription) are generated from
live database values, the same way the AI prompt in knowledge.py does, so
the numbers shown are always correct even though no LLM is involved.

If the pharmacy owner later adds ANTHROPIC_API_KEY, views.py automatically
switches to the smarter Claude-powered assistant (see knowledge.py) instead
— nothing here needs to change for that upgrade path. Until then, this file
keeps the "Help" widget fully working at zero cost, covering every topic in
STAFF_KNOWLEDGE / PUBLIC_KNOWLEDGE.

Keep this in sync with knowledge.py: if a feature changes, update the
matching entry's `answer` here too (and vice versa).
"""
import re

from .knowledge import _clean, _money
from .support import support_numbers, support_numbers_text

# --------------------------------------------------------------- matching --

_WORD_RE = re.compile(r'[^a-z0-9\u00e0-\u024f\s]+')


def _normalize(text):
    text = (text or '').lower()
    text = _WORD_RE.sub(' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _score(normalized, keywords):
    """Sum of word-counts of every keyword phrase that appears in the message.
    Multi-word phrases (e.g. 'bulk upload') score higher than single common
    words, so a specific match wins over a vague one."""
    score = 0
    for kw in keywords:
        if kw in normalized:
            score += len(kw.split())
    return score


def best_entry_scored(user_text, entries):
    """Return (best_entry, score); (None, 0) when nothing matches."""
    normalized = _normalize(user_text)
    if not normalized:
        return None, 0
    best, best_score = None, 0
    for entry in entries:
        s = _score(normalized, entry['keywords'])
        if s > best_score:
            best, best_score = entry, s
    return best, best_score


def best_entry(user_text, entries):
    return best_entry_scored(user_text, entries)[0]


def render_answer(entry, context):
    answer = entry['answer']
    return answer(context) if callable(answer) else answer


# ---------------------------------------------------------- customer care --

# Ends most answers so the customer is invited to keep going. Wording rotates
# (chosen from the message text, so the same question always gets the same reply).
FOLLOW_UPS = [
    "Je, kuna jambo lingine ungependa kujua?",
    "Je, kuna kitu kingine ninachoweza kukusaidia?",
    "Una swali lingine? Niko tayari kukusaidia.",
    "Je, kuna kingine unachotaka kufahamu?",
]

# Short messages that mean "I'm done / thank you". Matched as whole words.
_CLOSERS = [
    'hapana', 'hapana asante', 'sina swali', 'sina swali lingine', 'sina swali jingine', 'sina lingine', 'sina kingine',
    'sina jingine', 'hakuna kingine', 'hakuna lingine', 'hakuna jingine', 'hakuna swali', 'ni hayo tu', 'ndio hayo tu',
    'hayo tu', 'imetosha', 'nimeelewa', 'nimepata', 'nimeipata', 'sawa', 'poa', 'sawa sawa', 'asante', 'ahsante',
    'asante sana', 'shukrani', 'nashukuru', 'kwa sasa', 'thanks', 'thank you', 'thanks a lot', 'no thanks',
    'no thank you', 'nothing else', 'that s all', 'that is all', 'i m good', 'no', 'ok', 'okay', 'kwaheri', 'bye',
    'goodbye', 'tutaonana', 'sana', 'nimefurahi', 'vizuri', 'nzuri', 'safi', 'i', 'am', 'good',
]

SUPPORT_REQUEST_KEYWORDS = [
    'support', 'technical support', 'msaada wa kiufundi', 'tatizo la kiufundi', 'namba ya support', 'namba za support',
    'mawasiliano', 'wasiliana nanyi', 'nawezaje kuwasiliana', 'nipigie', 'nipate msaada', 'customer care',
    'huduma kwa wateja', 'ongea na mtu', 'nataka kuongea na mtu', 'mtu wa kunisaidia', 'binadamu', 'contact you',
    'how can i contact', 'talk to a person', 'speak to someone', 'call you', 'phone number of support',
]


def _pick(options, seed_text):
    return options[sum(ord(c) for c in (seed_text or '')) % len(options)]


def _support_line():
    return f"Timu yetu ya **Technical Support**: {support_numbers_text()}"


def _closing_answer():
    return (
        "Karibu sana, asante kwa kutumia SmartPOS! Ukihitaji msaada wakati wowote, niandikie hapa. "
        "Kwa msaada wa kiufundi wa haraka, piga timu yetu ya **Technical Support**: "
        f"{support_numbers_text()}.\n"
        "Uwe na siku njema!"
    )


def _support_answer(_ctx=None):
    numbers = support_numbers()
    return (
        "Timu yetu ya **Technical Support** iko tayari kukusaidia. Unaweza kuwapigia simu:\n"
        + "\n".join(f"- {n}" for n in numbers)
        + "\nUkipenda, niambie tatizo lako hapa kwanza — nitajaribu kukuelekeza hatua za kulitatua."
    )


def _is_closing(user_text, best_score):
    """True when the message is ONLY a thank-you / "no, nothing else" and not a real new question.
    Works by removing every closing phrase; if nothing is left, the person is done."""
    if '?' in (user_text or ''):
        return False
    normalized = _normalize(user_text)
    if not normalized or best_score >= 2:   # score>=2: it also clearly matches a real topic
        return False
    remainder = f' {normalized} '
    for kw in sorted(_CLOSERS, key=len, reverse=True):
        remainder = re.sub(r'(?<!\S)' + re.escape(kw) + r'(?!\S)', ' ', remainder)
    return remainder.strip() == ''


def _with_support(answer):
    """If an answer sends the person to 'support' but shows no number, add the numbers."""
    if 'support' in answer.lower() and not any(n in answer for n in support_numbers()):
        answer = f"{answer}\n\n{_support_line()}"
    return answer


def _finish(answer, user_text, follow_up=True):
    answer = _with_support(answer)
    if follow_up:
        answer = f"{answer}\n\n{_pick(FOLLOW_UPS, user_text)}"
    return answer


def _resolve(user_text, faq, context, fallback):
    """Shared brain for both assistants: closing -> support request -> FAQ -> fallback."""
    entry, score = best_entry_scored(user_text, faq)
    if _is_closing(user_text, score):
        return _closing_answer()
    if entry is None:
        return _finish(fallback, user_text, follow_up=False)
    answer = render_answer(entry, context)
    return _finish(answer, user_text, follow_up=entry.get('follow_up', True))


FALLBACK_STAFF = (
    "Samahani, sijaelewa swali lako vizuri. Ninaweza kusaidia kuhusu mada kama:\n"
    "- Kuuza (POS), malipo, deni, na kurudisha mauzo\n"
    "- Kuongeza bidhaa, batch, expiry (\"Has expiry date\"), discount, barcode\n"
    "- Bulk Upload / Restock Excel\n"
    "- Reports, Payroll & Meals, Employees, Branches, Subscription\n"
    "- Chat, Notifications, SMS, Change password, Data & Backup\n\n"
    "Jaribu kuuliza kwa maneno mengine, au muulize mmiliki wa duka / wasiliana na support."
)

FALLBACK_PUBLIC = (
    "Samahani, sijaelewa swali lako vizuri. Ninaweza kusaidia kuhusu:\n"
    "- Jinsi ya kujisajili na hatua za malipo\n"
    "- Bei za mipango (Monthly/Yearly) na tawi la ziada\n"
    "- Hali ya usajili wako (Pending Payment / Account Activated / n.k.)\n"
    "- Umesahau password, au namba ya simu ya usajili\n"
    "- Sifa za mfumo na usalama wa data\n\n"
    "Jaribu kuuliza kwa maneno mengine, au wasiliana na support kwa msaada zaidi."
)

MEDICAL_DECLINE = (
    "Mimi ni msaidizi wa programu ya SmartPOS tu, sitoi ushauri wa kitabibu "
    "(dozi, matumizi ya dawa, au tiba). Kwa maswali hayo muone Mfamasia au Daktari. "
    "Nikisaidie kuhusu jinsi ya kutumia mfumo badala yake?"
)


# ---------------------------------------------------------------- STAFF ----

def _requires_expiry_answer(_ctx=None):
    return (
        "Kila bidhaa ina swichi **\"Has expiry date\"** (Edit Product), imewashwa (ON) kwa default.\n"
        "1. Ikiwa IMEWASHWA: kila batch mpya ya bidhaa hiyo (Add Batch, Register Product, au Bulk Upload) "
        "LAZIMA iwe na tarehe ya mwisho wa matumizi — mfumo utakataa kuhifadhi bila hiyo.\n"
        "2. Ikiwa IMEZIMWA: expiry inabaki hiari, kama kawaida — weka hivi kwa vipodozi/vifaa visivyoisha muda.\n"
        "Kwenye Bulk Upload/Restock Excel kuna safu ya hiari **requires_expiry** (YES/NO au NDIYO/HAPANA)."
    )


def _staff_pricing_answer(ctx):
    ps = ctx.get('payment_settings')
    if not ps:
        return "Wasiliana na support kwa bei za sasa."
    return (
        "Bei za sasa:\n- Monthly: {}\n- Yearly: {}\n- Tawi la ziada: {} kwa mwezi".format(
            _money(ps.monthly_price), _money(ps.yearly_price), _money(ps.branch_addon_price),
        )
    )


def _subscription_status_answer(ctx):
    sub = ctx.get('subscription')
    if sub is None:
        return (
            "Sioni maelezo ya subscription yako hapa (huenda wewe ni Employee — subscription inarithiwa "
            "kutoka kwa mmiliki wa tawi lako). Kama ni Owner, fungua ukurasa wa **Subscription** juu ya menyu "
            "kuona tarehe kamili ya mwisho na kuomba renewal."
        )
    try:
        return (
            f"Subscription yako inaisha **{sub.end_date:%d %b %Y}** (siku {sub.days_remaining} zilizobaki). "
            "Ukiisha muda, upatikanaji unafungwa mpaka ilipwe na kuthibitishwa na HQ — nenda **Subscription** "
            "juu ya menyu kuomba renewal."
        )
    except Exception:
        return "Fungua ukurasa wa **Subscription** juu ya menyu kuona tarehe ya mwisho na kuomba renewal."


STAFF_FAQ = [
    {
        'keywords': ['habari', 'mambo', 'hujambo', 'hello', 'hi ', 'niaje', 'vipi'],
        'answer': "Habari, karibu! Mimi ni msaidizi wa SmartPOS. Naweza kukusaidia kuuza, stock, madeni, ripoti na mengine mengi. Ungependa kujua nini leo?",
        'follow_up': False,
    },
    # --- roles / menu ---
    {
        'keywords': [
            'menu ya owner', 'employee anaweza nini', 'tofauti ya owner na employee', 'roles',
            'nafasi za watumiaji', 'ninaweza kufanya nini', 'sioni menu',
        ],
        'answer': (
            "Kila mtu anaona: Dashboard, Chat, Sell (POS), Sales History, Returns & Voids, Debts, Products & Stock.\n"
            "Owner/Supervisor pekee anaona: Needs Review, Reports & Analytics, Payroll & Meals, SMS Centre, "
            "Send Announcement, Employees, Data & Backup, branch switcher, na Subscription.\n"
            "Employee anafanya kazi tawi MOJA tu; Owner anaweza kusimamia matawi kadhaa na kubadilisha kwa branch switcher."
        ),
    },
    # --- POS / selling ---
    {
        'keywords': ['pos', 'kuuza', 'sell', 'uza bidhaa', 'sale', 'fanya mauzo', 'checkout'],
        'answer': (
            "Kuuza: fungua **Sell (POS)**, tafuta bidhaa kwa jina au scan barcode, weka kwenye cart, "
            "rekebisha idadi kisha **checkout**. Njia za malipo: Cash, Mobile Money, au Debt/Deni "
            "(inahitaji jina na namba ya simu ya mteja). Stock inauzwa kutoka batch inayokaribia kuisha muda kwanza (FEFO)."
        ),
    },
    {
        'keywords': ['hakuna mtandao', 'offline', 'mtandao ukikatika', 'internet ikikatika', 'bila internet niuze', 'bila mtandao', 'bila internet'],
        'answer': "POS ina catalog ya offline: ukiuza wakati hakuna internet, mauzo yanahifadhiwa kwenye kifaa na kusawazishwa (sync) mtandao ukirudi.",
    },
    {
        'keywords': ['barcode', 'scan', 'skani', 'lebo ya bidhaa', 'code 128'],
        'answer': "Kila bidhaa inaweza kuwa na barcode ya Code-128 na lebo inayoweza kuchapishwa. Kwenye POS unaweza scan au kuandika barcode moja kwa moja.",
    },
    {
        'keywords': ['risiti', 'receipt', 'pdf ya mauzo', 'namba ya risiti', 'receipt number'],
        'answer': (
            "Risiti (PDF) zinapatikana kwenye **Sales History** — fungua mauzo husika. Ukiwa na Premium Receipt SMS, "
            "unaweza kutuma link ya risiti kwa SMS moja kwa moja kwa mteja. Namba za risiti (#) zinaanzia 1 na "
            "zinahesabiwa tofauti kwa kila tawi."
        ),
    },
    {
        'keywords': ['discount', 'punguzo', 'bei ya kupunguza', 'discount policy', 'min discount', 'max discount'],
        'answer': (
            "Kila bidhaa ina mpangilio wake wa punguzo (Edit Product): \"Discount enabled\" pamoja na asilimia "
            "ndogo na kubwa zinazoruhusiwa (min/max %). Ukiwa POS, unaweza kutoa punguzo kwa bidhaa hiyo tu ndani "
            "ya kiwango kilichowekwa — nje ya hapo mfumo unakataa."
        ),
    },
    {
        'keywords': ['needs review', 'mauzo ya kuangalia', 'sales flagged'],
        'answer': "**Needs Review** (Owner only) inaonyesha mauzo yaliyowekwa alama ya kuangaliwa; Owner anaweza kuyakagua na kuyaweka \"Reviewed\".",
    },
    {
        'keywords': ['return', 'rudisha', 'void', 'batili', 'nimeuza vibaya', 'kosea kuuza', 'voided'],
        'answer': (
            "Uliuza vibaya? Fungua **Sales History**, bonyeza \"Return / Void\" kwenye mauzo husika, "
            "chagua idadi ya kurudisha au \"Void the entire sale\", na toa sababu. Employee anaomba tu ndani ya "
            "masaa 24 kwa mauzo yake mwenyewe, na Owner ndiye anaidhinisha (**Returns & Voids**); Owner anaweza "
            "kurudisha moja kwa moja bila kikomo. Mauzo yaliyofutwa yanabaki kwenye Sales History yakiwa "
            "yameandikwa \"Voided\" na risiti inapigwa muhuri VOIDED — hakuna mauzo yanayofutwa kabisa."
        ),
    },
    {
        'keywords': ['kuuza kwa deni', 'uza kwa deni', 'kuuza deni', 'mauzo ya deni', 'sell on credit', 'sell on debt', 'credit sale', 'debt sale'],
        'answer': (
            "Kuuza kwa deni: kwenye **Sell (POS)** weka bidhaa kwenye cart, kisha chagua njia ya malipo **Debt / Credit**. "
            "Andika jina na namba ya simu ya mteja (lazima), kisha fanya checkout. Deni linaingia kwenye ukurasa wa **Debts**, "
            "ambako utarekodi malipo (kidogo kidogo au yote) na kumtumia mteja kikumbusho cha SMS."
        ),
    },
    {
        'keywords': ['deni', 'debt', 'mteja anadaiwa', 'kumbusha deni', 'malipo ya deni', 'reminder sms'],
        'answer': (
            "**Debts** inaonyesha wateja wenye deni. Unaweza kurekodi malipo (sehemu au yote), kurekebisha "
            "taarifa za deni, na kutuma SMS ya kukumbusha (inahitaji Premium Debt SMS package yenye credits — "
            "ikiwa imezuiwa, mmiliki anunue au ku-renew package kwenye Premium Debt SMS)."
        ),
    },
    # --- products / stock ---
    {
        'keywords': ['ongeza bidhaa', 'add product', 'sajili bidhaa', 'new product', 'register product', 'category mpya'],
        'answer': (
            "**Products & Stock -> New product**: jaza jina, category (ukiandika jina jipya inatengenezwa "
            "kiotomatiki), unit, bei, reorder level, na swichi \"Has expiry date\". Categories ni "
            "tofauti kwa kila duka/owner."
        ),
    },
    {
        'keywords': [
            'inaisha muda', 'requires expiry', 'requires_expiry', 'expiry', 'tarehe ya mwisho',
            'muda wa matumizi', 'expire', 'imekataa expiry', 'lazima iwe na tarehe',
        ],
        'answer': _requires_expiry_answer,
    },
    {
        'keywords': ['batch', 'ongeza stock', 'add batch', 'restock', 'nyongeza ya stock'],
        'answer': (
            "Kuongeza stock: fungua bidhaa husika -> **Add Batch** (batch number, idadi, bei ya kununua, "
            "bei ya kuuza, expiry date ikihitajika). Unaweza pia kutumia **Restock Template** (Excel) kwa bidhaa nyingi mara moja."
        ),
    },
    {
        'keywords': ['bulk upload', 'excel', 'template', 'pakia bidhaa nyingi', 'upload ya jumla'],
        'answer': (
            "**Bulk Upload** (Products & Stock -> Bulk upload): pakua template ya Excel kwanza, usibadilishe majina "
            "ya column, kisha jaza: name, category, batch_number, unit, purchase/selling price, expiry_date, na "
            "safu ya hiari **requires_expiry** (YES/NO). Kama bidhaa inahitaji expiry na safu hiyo iko wazi, "
            "row hiyo itakataliwa kabla ya kuhifadhi chochote."
        ),
    },
    {
        'keywords': ['bulk upload haifanyi kazi', 'bulk upload failed', 'imeshindwa kupakia', 'excel imekataliwa'],
        'answer': (
            "Bulk Upload imeshindwa? Tumia template rasmi bila kubadilisha majina ya column, hakikisha tarehe ni "
            "tarehe halisi (siyo maandishi), namba ziwe namba tu (bila herufi), na hakuna safu zinazorudia (duplicate). "
            "Ikiendelea kushindwa, tuma ujumbe wa error kwa support."
        ),
    },
    {
        'keywords': ['duplicate', 'bidhaa mbili', 'merge', 'unganisha bidhaa', 'nimeongeza mara mbili'],
        'answer': "Ukiona bidhaa ile ile imesajiliwa mara mbili, tumia **Merge** kwenye ukurasa wa bidhaa kuiunganisha na ile sahihi, badala ya kubaki na mbili.",
    },
    {
        'keywords': ['unit ya ununuzi', 'purchase unit', 'box', 'carton', 'ubadilishaji wa unit', 'wac', 'weighted average cost', 'faida'],
        'answer': (
            "Unaweza kununua kwa unit kubwa (mf. 1 Box = 100 Tablets) na kuuza kwa unit ndogo (Tablet); "
            "gharama ya unit moja = bei ya ununuzi / idadi ya units kwenye hiyo package. Faida inahesabiwa kwa "
            "Weighted Average Cost (WAC) wakati wa mauzo."
        ),
    },
    {
        'keywords': ['sku', 'namba ya bidhaa'],
        'answer': "SKU na barcode zinaweza kuachwa wazi wakati wa kusajili bidhaa — mfumo unaziunda kiotomatiki ukizihitaji.",
    },
    {
        'keywords': ['futa bidhaa', 'delete product', 'ondoa bidhaa'],
        'answer': "Bidhaa inaweza kufutwa (moja au nyingi kwa bulk delete) kutoka Products & Stock, lakini kuwa makini — hii inaathiri historia ya bidhaa hiyo.",
    },
    {
        'keywords': ['haionekani kwenye pos', 'product not showing', 'siwezi kuuza bidhaa', 'haionekani dukani'],
        'answer': "Bidhaa haionekani kwenye POS? Hakikisha ina batch yenye stock isiyokwisha muda, ina bei ya kuuza, na uko kwenye tawi (branch) sahihi.",
    },
    # --- reports / payroll / employees / branches / subscription ---
    {
        'keywords': ['report', 'ripoti', 'faida', 'profit report', 'mauzo ripoti', 'ripoti ya tarehe'],
        'answer': "**Reports & Analytics** (Owner only) ina ripoti za mauzo, bidhaa na faida, ripoti za tarehe maalum, ulinganisho wa faida, na PDF exports.",
    },
    {
        'keywords': ['mshahara', 'payroll', 'meal allowance', 'chakula cha mfanyakazi', 'posho ya chakula'],
        'answer': "**Payroll & Meals** inafuatilia mshahara wa kila mfanyakazi (kumbukumbu tu, hairekebishwi moja kwa moja kwenye mauzo) na posho za chakula za kila siku (chai, chakula cha mchana, cha jioni) — hizi huhesabiwa kama gharama ya uendeshaji pale meal ledger inapokuwa EARNED; hazipunguzi mshahara wa mfanyakazi.",
    },
    {
        'keywords': ['mfanyakazi mpya', 'new employee', 'add employee', 'add an employee', 'ongeza mfanyakazi', 'ongeza employee', 'kuongeza employee', 'kuongeza mfanyakazi', 'sajili mfanyakazi', 'employee login', 'zima mfanyakazi', 'deactivate employee', 'wafanyakazi', 'employees'],
        'answer': "**Employees -> New employee** inatengeneza login kwa mtu kwenye tawi husika; password ya muda inatumwa kwa SMS na mfanyakazi anaibadilisha akiingia mara ya kwanza. Owner anaweza kuzima/kuwasha mfanyakazi na kuweka mshahara/posho (Compensation). Mfanyakazi anarithi (inherit) upatikanaji kutoka subscription ya mmiliki wa tawi lake.",
    },
    {
        'keywords': ['tawi jipya', 'new branch', 'branch addon', 'ongeza tawi', 'matawi', 'badilisha jina la tawi', 'receipt header'],
        'answer': "Usajili wako una tawi (branch) 1. Kila tawi la ziada ni malipo ya kila mwezi (add-on), yanayolipwa na kuthibitishwa na HQ kama usajili wenyewe; tawi jipya linatumika baada ya HQ kuthibitisha. Ukifuta add-on, tawi linazimwa bila kufuta data yake. Jina la tawi na receipt header vinabadilishwa kwenye orodha ya matawi — tumia \"Manage branches\" kwenye branch switcher.",
    },
    {
        'keywords': ['subscription', 'usajili wangu', 'muda wa mpango', 'renew', 'longeza mpango', 'imeisha muda subscription'],
        'answer': _subscription_status_answer,
    },
    {
        'keywords': ['bei ya mpango', 'gharama ya usajili', 'malipo ya mwezi', 'monthly price', 'yearly price', 'bei ya tawi'],
        'answer': _staff_pricing_answer,
    },
    # --- chat / notifications / sms / account ---
    {
        'keywords': ['chat', 'ujumbe', 'mazungumzo', 'tuma ujumbe', 'futa ujumbe', 'delete message', 'mention'],
        'answer': "**Chat** ina chat ya tawi, group ya \"All Branches\", ujumbe binafsi kati ya owner na wafanyakazi, na group ya wateja. Unaweza kutuma picha/faili, ku-mention watu, kujibu ujumbe maalum (Reply), na kufuta ujumbe wako mwenyewe kwa wote (Delete — matini na faili vinafutika kabisa).",
    },
    {
        'keywords': ['announcement', 'tangazo', 'send announcement', 'tuma tangazo'],
        'answer': "**Send Announcement** (Owner only) inamruhusu mmiliki kutuma tangazo kwa wafanyakazi wote wa matawi yake.",
    },
    {
        'keywords': ['notification', 'taarifa', 'kengele', 'bell icon'],
        'answer': "Aikoni ya kengele (bell) juu ya menyu inaonyesha taarifa (notifications) mpya za mfumo, mfano stock inayokaribia kuisha au mauzo yanayohitaji kuangaliwa.",
    },
    {
        'keywords': ['sms', 'credit za sms', 'premium sms', 'sms centre'],
        'answer': "Kila akaunti inaanza na SMS credits 500 za kawaida (SMS Centre). Premium Debt SMS na Premium Receipt SMS ni package tofauti za kulipia, zinazonunuliwa na kuthibitishwa na HQ.",
    },
    {
        'keywords': ['badilisha password', 'change password', 'nimesahau password', 'forgot password', 'forgot my password', 'i forgot', 'nimesahau', 'reset password', 'sijui password'],
        'answer': "Kubadilisha password: profile menu -> Change password. Umesahau password: \"Forgot password?\" kwenye ukurasa wa login (OTP kwa SMS). Employee aliyefungwa nje amuulize mmiliki wa duka; mmiliki awasiliane na support.",
    },
    {
        'keywords': ['backup', 'nakala ya data', 'data yangu salama', 'poteza data'],
        'answer': "**Data & Backup** (Owner only) inakuwezesha kutengeneza na kupakua nakala (backup) ya data ya duka lako wakati wowote.",
    },
    {
        'keywords': ['haifunguki', 'page not loading', 'imekwama', 'skrini ya zamani', 'hakuna internet ukurasa'],
        'answer': "Ukurasa hauonyeshi vizuri? Refresh ukurasa (kwenye simu, pull-down au fungua app upya). Bila internet, app inaonyesha ukurasa wa offline hadi mtandao urudi.",
    },
    {
        'keywords': ['dozi', 'kipimo cha dawa', 'dawa gani ya', 'matibabu ya', 'nitumie dawa gani', 'side effect', 'madhara ya dawa'],
        'answer': MEDICAL_DECLINE,
        'follow_up': False,
    },
]


# Listed last on purpose: on a tie a real topic answer wins over the generic support answer.
STAFF_FAQ.append({'keywords': SUPPORT_REQUEST_KEYWORDS, 'answer': _support_answer, 'follow_up': False})


def staff_offline_answer(user_text, user=None, pharmacy=None, subscription=None, payment_settings=None):
    return _resolve(user_text, STAFF_FAQ, {
        'user': user, 'pharmacy': pharmacy, 'subscription': subscription, 'payment_settings': payment_settings,
    }, FALLBACK_STAFF)


# --------------------------------------------------------------- PUBLIC ----

def _pricing_answer(ctx):
    ps = ctx['payment_settings']
    lines = [
        f"Monthly plan: {_money(ps.monthly_price)}",
        f"Yearly plan: {_money(ps.yearly_price)}",
        f"Tawi la ziada: {_money(ps.branch_addon_price)} kwa mwezi",
        f"Lipa Namba: {_clean(ps.lipa_namba)}",
        f"Jina la malipo: {_clean(ps.payee_name)}",
    ]
    return "Bei za sasa:\n" + "\n".join(f"- {l}" for l in lines)


def _signup_status_answer(ctx):
    signup = ctx.get('signup')
    if signup is None:
        return (
            "Sijaona usajili wako maalum kwenye kifungu hiki. Fungua link binafsi ya \"Registration Status\" "
            "uliyopewa baada ya kujisajili ili uone hali ya usajili wako."
        )
    ps = ctx['payment_settings']
    total = ps.price_for(signup.plan, getattr(signup, 'tier', None))
    if signup.sms_package_id:
        total += signup.sms_package.price
    if signup.receipt_sms_package_id:
        total += signup.receipt_sms_package.price
    return (
        f"Usajili wa **{signup.business_name}**: mpango {signup.get_plan_display()}, "
        f"jumla ya kulipa {_money(total)}, hali ya sasa: **{signup.get_status_display()}**.\n"
        "HQ inathibitisha malipo kwa mkono baada ya kuangalia — si papo hapo. Kama umeshalipa muda mrefu bila "
        "kuthibitishwa, wasiliana na support ukiwa na uthibitisho wa malipo."
    )


PUBLIC_FAQ = [
    {
        'keywords': ['habari', 'mambo', 'hujambo', 'hello', 'hi ', 'niaje'],
        'answer': "Habari, karibu SmartPOS! Naweza kukusaidia kuhusu kujisajili, malipo, na akaunti yako. Ungependa kujua nini?",
        'follow_up': False,
    },
    {
        'keywords': ['jinsi ya kujisajili', 'how do i sign up', 'sajili duka', 'fungua akaunti', 'jisajili'],
        'answer': (
            "Kujisajili: fungua \"Create an account\" kwenye ukurasa wa login, jaza jina la duka, jina la mmiliki, "
            "namba ya simu (mfano +255712345678), na uchague mpango (Monthly/Yearly). Baada ya kutuma, utaona namba "
            "ya kulipia na maelekezo. Baada ya kulipa, HQ inathibitisha kwa mkono, kisha username na password ya "
            "muda vinatumwa kwa SMS — badilisha password wakati wa kuingia mara ya kwanza."
        ),
    },
    {
        'keywords': ['status ya usajili wangu maana yake', 'pending payment', 'account activated', 'system delivered', 'rejected', 'hali ya usajili maana'],
        'answer': (
            "Hali za usajili: **Pending Payment** — bado inasubiri malipo/uthibitisho; **Account Activated** — "
            "malipo yamethibitishwa, SMS ya login imetumwa; **System Delivered** — mfumo umekabidhiwa kikamilifu; "
            "**Rejected** — haikuidhinishwa, wasiliana na support."
        ),
    },
    {
        'keywords': ['tawi ngapi', 'branch ngapi', 'branches ngapi', 'matawi mangapi'],
        'answer': "Kila usajili unajumuisha tawi (branch) 1. Tawi la ziada ni malipo ya kila mwezi (add-on), yanayolipwa na kuthibitishwa na HQ kama usajili wenyewe.",
    },
    {
        'keywords': ['bei', 'price', 'prices', 'gharama', 'malipo ngapi', 'inagharimu', 'lipa kiasi gani', 'how much', 'cost', 'costs', 'pricing'],
        'answer': _pricing_answer,
    },
    {
        'keywords': ['lipa namba', 'namba ya kulipia', 'nilipe wapi', 'payment number'],
        'answer': _pricing_answer,
    },
    {
        'keywords': ['nimelipa', 'i paid', 'sijaidhinishwa', 'not approved', 'bado sijaanza kutumia', 'status ya usajili'],
        'answer': _signup_status_answer,
    },
    {
        'keywords': ['badilisha namba ya simu', 'change phone number', 'nilisajili namba mbaya'],
        'answer': "Namba ya simu ya usajili haiwezi kubadilishwa moja kwa moja — wasiliana na support wakusaidie.",
    },
    {
        'keywords': ['namba ya simu format', 'phone number format', '+255', '07', 'namba ya simu inaandikwaje', 'fomati ya namba', 'namba ya simu'],
        'answer': "Namba za simu zinaandikwa kwa format ya kimataifa km +255712345678; namba za mtaani zinazoanza na 07... zinabadilishwa kiotomatiki. Namba moja inaweza kusajiliwa mara moja tu.",
    },
    {
        'keywords': ['nimesahau password', 'forgot password', 'forgot my password', 'i forgot', 'nimesahau', 'reset password', 'sijui password'],
        'answer': "Kwenye ukurasa wa login, bonyeza \"Forgot password?\", utapata OTP kwa SMS kwenye namba uliyosajili, kisha weka password mpya. Mfanyakazi aliyefungwa nje amuulize mmiliki wa duka.",
    },
    {
        'keywords': ['data yangu salama', 'is my data safe', 'usalama wa data'],
        'answer': "Data inahifadhiwa kwenye database ya wingu (cloud), na wamiliki wanaweza kutengeneza na kupakua backup yao wenyewe kutoka **Data & Backup** baada ya kuingia.",
    },
    {
        'keywords': ['sifa za mfumo', 'what does the system do', 'features', 'huduma za mfumo', 'mfumo una nini'],
        'answer': (
            "SmartPOS ina POS (cash/mobile money/deni), bidhaa na batches zenye expiry ya hiari, udhibiti wa "
            "stock, matawi mengi, wafanyakazi na roles, madeni na vikumbusho, risiti za PDF, ripoti, payroll, chat "
            "ya ndani, taarifa, backup ya data, na SMS za Premium (hiari). Kila akaunti ina SMS credits 500 za kuanzia."
        ),
    },
    {
        'keywords': ['dozi', 'kipimo cha dawa', 'matibabu ya', 'nitumie dawa gani', 'side effect'],
        'answer': MEDICAL_DECLINE,
        'follow_up': False,
    },
]


PUBLIC_FAQ.append({'keywords': SUPPORT_REQUEST_KEYWORDS, 'answer': _support_answer, 'follow_up': False})


def public_offline_answer(user_text, payment_settings=None, signup=None):
    return _resolve(user_text, PUBLIC_FAQ, {'payment_settings': payment_settings, 'signup': signup}, FALLBACK_PUBLIC)
