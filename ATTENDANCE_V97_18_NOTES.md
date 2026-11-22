# v97.18 - Workday lock, dashboard crash fix, professional attendance SMS

## 1. Mfanyakazi akifunga siku kwa bahati mbaya hawezi kuuza mpaka Boss aidhinishe
**Tatizo:** Dashboard ilisema "Sales continue after approval", lakini server haikuzuia mauzo.
Mfanyakazi aliyefunga siku (au akafanya check-out kwa QR) na kutuma ombi la kufungua tena
angeweza kuendelea kuuza kupitia POS hata kabla Boss hajajibu.

**Fix (server-side, haiwezi kukwepwa kwa browser):** `accounts/workday_guard.py`
Mfanyakazi (role EMPLOYEE) mwenye workday ya leo iliyofungwa (au attendance yenye check-out):

| Mahali | Matokeo |
|--------|---------|
| Ukurasa wa POS (`/sales/pos/`) | Anarudishwa dashboard na ujumbe |
| `api/checkout/` | HTTP 423 `{"workday_closed": true}` - uuzaji haufanyiki |
| `api/sync-offline-sales/` | Mauzo yaliyofanywa OFFLINE **baada ya** kufunga hayapokelewi (`blocked_workday_closed`) na yanabaki kwenye foleni; yaliyofanywa **kabla** ya kufunga yanasawazishwa kawaida |
| Kurekodi malipo ya madeni | Imezuiwa na ujumbe |
| Layaway (kuunda plan/deposit, malipo, handover) | Imezuiwa na ujumbe |

Kufungua ni kwa Boss/manager tu: **Approve & Reopen** (Meals & Attendance) au "Reopen workday".
Mameneja, assistants na wafanyakazi wasioanza workday hawaathiriwi.

Kikomo kinachojulikana: muda wa mauzo ya offline unatoka kwenye saa ya kifaa (`queued_at`);
mauzo yasiyo na `queued_at` halali yanazuiwa (fail-closed).

## 2. Crash ya dashboard (500) kwenye tawi jipya
`TypeError: combine() argument 2 must be datetime.time, not str`
Sababu: `TimeField(default='10:00')` iliacha **string** kwenye request iliyounda `MealTimingSettings`.
Fix: defaults sasa ni `datetime.time(...)` (migration `0037`), `meal_timing_for()` inarefresh row mpya,
na `_aware_on()` inakubali string kwa usalama.

## 3. SMS za mahudhurio kwa Boss - muundo wa kitaalamu
ASCII tupu, bado SMS 1 (herufi 160 = credit 1). Mfano:

```
SMARTPOS - MAHUDHURIO
Neema Juma ameingia kazini
Saa 08:03, 15/11/2026
Hali: Kwa wakati
Tawi: MANAL COSMETICS
```
```
SMARTPOS - MAHUDHURIO
Neema Juma ameondoka kazini
Saa 21:35, 15/11/2026
Muda wa kazi: saa 12 na dakika 32
Hali: Kwa wakati
Tawi: MANAL COSMETICS
```
Kuchelewa: `Hali: AMECHELEWA dakika 33 (kuanza 08:00)` - Kuondoka mapema: `Hali: MAPEMA dakika 50 (kufunga 21:30)`.
Ujumbe ukizidi herufi 160, tarehe huondolewa kwanza, kisha maelezo ya saa, kisha majina hufupishwa.

**Mpya:** mfanyakazi akiomba kufungua siku, Boss anapata SMS (modi za Exceptions na Every event):
```
SMARTPOS - MAHUDHURIO
Neema Juma amefunga siku, anaomba kuifungua tena.
Sababu: Alifunga kwa makosa
Fungua SmartPOS kuidhinisha.
Tawi: MANAL COSMETICS
```
**Mpya:** baada ya Boss kufungua siku, check-out ya mwisho ya mfanyakazi sasa inamfikia Boss
(awali ilizuiwa kwa sababu check-out ya kwanza ya bahati mbaya ilishatumwa).

## Deploy
1. Copy faili juu ya mradi, kisha `python manage.py migrate` (accounts 0037 - hakuna mabadiliko ya data).
2. Hakikisha worker ya Celery inaendesha (SMS hazitumwi bila worker).
3. Hakuna mipangilio mipya; modi za SMS za tawi zinabaki kama zilivyo.

## Faili
Mpya: accounts/workday_guard.py, accounts/migrations/0037_meal_timing_time_defaults.py,
accounts/tests/test_workday_lock.py
Zilizobadilika: accounts/{attendance_sms,meal_services,models,views}.py, sales/views.py, layaway/views.py,
static/js/offline-pos.js, templates/sales/pos.html, accounts/tests/test_attendance_sms.py

---

# v97.19 - Muonekano wa SmartPOS kwenye Google (SEO)

Google huonyesha: kichwa (title), maelezo (description), favicon, jina la tovuti, kiungo, picha ya onyesho na data iliyopangwa (schema.org).
Yote hayo sasa yamepangwa vizuri na ni thabiti.

## Kilichobadilika
| Eneo | Mabadiliko |
|------|-----------|
| Title | `SmartPOS Tanzania - POS, Stock & Branch Management System` |
| Description | Maelezo ya mstari mmoja yenye maneno muhimu (barcode, stock, multi-branch, SMS receipts, Start free) |
| Canonical | Sasa ni `SITE_URL` (chaguo-msingi `https://smartbiashara.store`), si host ya request - Google haitaorodhesha tena domain ya `*.up.railway.app` |
| Noindex | `X-Robots-Tag: noindex` kwa: kurasa za mfumo ukiwa umeingia (login), na kila ukurasa kwenye `*.up.railway.app` |
| Picha ya onyesho | `static/img/og-smartpos.png` (1200x630) kwa Google/WhatsApp/Facebook/X |
| Structured data | `Organization` (+ logo), `WebSite` (jina + `SmartBiashara` kama jina mbadala), `SoftwareApplication` (bei za TZS kutoka HQ Payment Settings + Free plan). Hakuna ratings bandia |
| `/sitemap.xml` | Mpya: kurasa za umma + mali za marketplace zilizochapishwa |
| `/robots.txt` | Inaruhusu kurasa za umma + CSS/JS (`/static/`), inaficha sehemu za mfumo, na inataja sitemap |
| Kurasa nyingine | Login na `/jiunge/` zina title, description na canonical sahihi |

## Mipangilio (Railway -> Variables, zote hiari)
- `SITE_URL=https://smartbiashara.store`
- `SITE_NAME=SmartPOS` (jina litakaloonyeshwa na Google)
- `SITE_ALTERNATE_NAMES=SmartBiashara`
- `GOOGLE_SITE_VERIFICATION=<token>` (kutoka Search Console, njia ya "HTML tag")

## Hatua zako baada ya deploy (Google Search Console)
1. Nenda search.google.com/search-console -> Add property -> **Domain** `smartbiashara.store` (thibitisha kwa DNS TXT) au **URL prefix** (tumia `GOOGLE_SITE_VERIFICATION`).
2. Sitemaps -> ongeza `sitemap.xml`.
3. URL Inspection -> weka `https://smartbiashara.store/` -> **Request indexing**.
4. Subiri siku chache hadi wiki chache. Google ndiyo huamua muonekano wa mwisho (jina la tovuti, favicon, sitelinks); hakuna anayeweza kulazimisha.

## Faili
Mpya: core/seo.py, core/test_seo.py, static/img/og-smartpos.png
Zilizobadilika: core/{views,landing_views}.py, pharmacy_system/{settings,urls}.py,
accounts/{middleware,subscription_middleware}.py, templates/{base.html,public/landing.html,accounts/login.html,hq/signup.html}
