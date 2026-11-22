# Attendance SMS kwa Boss (v97.17)

## Inafanya nini
Mfanyakazi akifanya check-in / check-out, owner wa tawi anapata SMS fupi kwa Kiswahili (ASCII, ujumbe 1 = credit 1).

## Modi (kila tawi huchagua)
Workplace Location -> Attendance policy -> **Owner SMS alerts**

| Modi | Boss anapata SMS lini |
|------|-----------------------|
| Off (chaguo-msingi) | Kamwe |
| Exceptions only (inapendekezwa) | Akichelewa, akiondoka mapema, au akiomba idhini ya mkono |
| Every check-in and check-out | Kila check-in na check-out |

Mipangilio mingine: saa ya kuanza, saa ya kufunga, na muda wa kuvumilia (dakika).

## Ulinzi uliowekwa
- SMS hutumwa nyuma ya pazia (Celery) baada ya DB commit; check-in haichelewi wala haivunjiki SMS ikifeli.
- Mara moja tu kwa kila tukio la siku (`check_in_sms_sent_at`, `check_out_sms_sent_at`).
- Hutumia SMS allowance ya owner (wallet ya kawaida); admin anaweza kuzima kote kwa `SMS Settings -> send_attendance_alerts`.
- Huenda kwa owners wa tawi wenye namba sahihi (hawarudiwi). Manager Assistants hupata arifa ya kengele kama kawaida.
- Zamu za usiku (kuvuka saa 00:00) hazitumiki kwa hesabu ya kuchelewa/mapema.

## Deploy
1. Copy faili juu ya mradi, kisha `python manage.py migrate`
   (accounts 0036, pharmacies 0017, sms_integration 0013).
2. Hakikisha **worker** ya Celery inaendesha (railway.worker.json / worker.sh); bila worker hakuna SMS.
3. Washa modi kwenye kila tawi.

## Faili
Mpya: accounts/attendance_sms.py, accounts/tasks.py, accounts/tests/test_attendance_sms.py, migrations 3.
Zilizobadilika: accounts/{attendance_services,models,views}.py, pharmacies/{models,forms}.py,
sms_integration/{models,utils,admin}.py, templates/pharmacies/workplace_location.html
