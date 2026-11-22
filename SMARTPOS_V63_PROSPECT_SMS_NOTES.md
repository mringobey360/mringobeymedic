# v59 - Wafanyabiashara Watarajiwa (prospect invitations)

Menu: HQ sidebar > "Wafanyabiashara Watarajiwa"  (/hq/prospects/)  - superadmin only.

* Register numbers: one by one or paste many (one per line, optional name). Duplicates / invalid numbers are rejected.
* Send: /hq/prospects/send/ - 4 official Swahili templates, SmartPOS link (home page or /jiunge/), live SMS preview, segment counter.
* Audience: new only (never messaged) / all eligible / selected. JOINED and OPTED_OUT numbers are never messaged.
* Numbers that match an existing customer phone are marked JOINED automatically.
* Uses the existing announcement sender, so delivery report + retry appear under HQ > SMS Announcements.
* Message = Title + body + opt-out line (optional) + "SmartPOS Team".

Deploy: run `python manage.py migrate` (new migration sms_integration 0011_smsprospect).
Also in this version: DebtPayment import fix (reconciliation.py) and Africa's Talking empty-response retry (sms_integration/utils.py).

## v60 UI refresh
Prospect pages redesigned: gradient header, KPI cards, status funnel bar, tabbed add/import, filter pills, avatar table, empty state; send page with template cards, character meter, phone-style preview, live recipient/segment totals and a confirmation dialog. Styles are scoped inside each template (the project Tailwind build is compiled locally), with light/dark support.

## v61 low-cost templates
All 4 invitation templates are now short: the full SMS (title + message + opt-out + "SmartPOS Team" + link) is 134-157 characters = ONE segment. Default greeting is "Ndugu". The send page warns when a message goes over 160 characters.

## v62 English
Prospects pages, system messages, business-type/status labels and all 4 SMS templates are now in English (sidebar: "Prospects"). Templates stay on ONE SMS segment (135-160 characters including link, opt-out line and signature). New migration 0012_prospect_english_labels - run `python manage.py migrate`.

**Update:** the SMS text itself is Swahili (short, 1 segment); only the admin screens are English.
