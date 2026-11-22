# v95 – Mwezi 1 wa majaribio + Google sign-in

## Trial ya siku 30
- Sign-up mpya na "Free trial": akaunti inafunguka mara moja, siku 30 (`TRIAL_DAYS` kwenye `hq/models.py`), Starter features zote, **hakuna kikomo cha mauzo**.
- Kila huduma ya SMS (Debt, Receipt, Marketing) inapata **SMS 20** (`TRIAL_SMS_CREDITS`). Hizi zinatolewa kwenye trial tu.
- Trial ikiisha: akaunti inafungwa hadi mteja alipe (banner inaonyesha siku zilizobaki).
- Mteja anaweza kuchagua **"Pay now · no trial"** wakati wa kujiandikisha: analipa moja kwa moja, hapati trial wala SMS 20 za bure.
- Dashibodi ya mmiliki ina checklist ya kukaribisha (dashboard/onboarding.py): bidhaa ya kwanza, mauzo ya kwanza, mfanyakazi, siku za trial zilizobaki.
- Ukumbusho wa SMS+arifa (siku 10/3/1/0) tayari unafanya kazi kupitia `process_branch_billing` (kila saa), pamoja na trial.
- Akaunti za zamani za "Free Plan ya maisha" zinaendelea kama zilivyo (bado zina kikomo cha mauzo 100/mwezi).

## Google sign-in
1. Google Cloud Console -> APIs & Services -> Credentials -> Create OAuth client ID (Web application).
2. Weka URL ya tovuti yako kwenye **Authorized JavaScript origins** (mf. https://smartpos.example.com).
3. Weka variable ya mazingira: `GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com`. Ikiwa tupu, kitufe hakionekani.
4. `python manage.py migrate` (accounts 0035, hq 0019).

Usalama: ID token inahakikiwa kwenye server (saini ya RS256 kutoka funguo za Google, aud, iss, exp, email_verified, na nonce ya mara moja iliyo kwenye session). Google inaruhusiwa kwa Shop Owner tu; superadmin, staff na wafanyakazi hawawezi kuingia kwa Google. Email inatoka kwa Google, si kwenye fomu. Hakuna package mpya.
