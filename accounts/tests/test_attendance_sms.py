import datetime as dt
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from accounts import attendance_sms as sms

TZ = ZoneInfo('Africa/Dar_es_Salaam')
START, END = dt.time(8, 0), dt.time(17, 0)


def at(hour, minute):
    return dt.datetime(2026, 11, 14, hour, minute, tzinfo=TZ)


class LatenessTests(SimpleTestCase):
    def test_minutes_after_start(self):
        self.assertEqual(sms.late_minutes_for(at(8, 33), START), 33)

    def test_early_arrival_is_not_late(self):
        self.assertLessEqual(sms.late_minutes_for(at(7, 52), START), 0)

    def test_minutes_before_closing(self):
        self.assertEqual(sms.early_minutes_for(at(16, 20), END), 40)

    def test_after_closing_is_not_early(self):
        self.assertLessEqual(sms.early_minutes_for(at(17, 5), END), 0)


class ShouldSendTests(SimpleTestCase):
    def test_off_never_sends(self):
        for kind in ('IN', 'OUT', 'EXC', 'REO'):
            self.assertFalse(sms.should_send('OFF', kind, is_late=True, is_early=True))

    def test_exceptions_only_alerts_on_problems(self):
        self.assertTrue(sms.should_send('EXCEPTIONS', 'IN', is_late=True))
        self.assertFalse(sms.should_send('EXCEPTIONS', 'IN', is_late=False))
        self.assertTrue(sms.should_send('EXCEPTIONS', 'OUT', is_early=True))
        self.assertFalse(sms.should_send('EXCEPTIONS', 'OUT', is_early=False))
        self.assertTrue(sms.should_send('EXCEPTIONS', 'EXC'))

    def test_all_sends_every_event(self):
        for kind in ('IN', 'OUT', 'EXC', 'REO'):
            self.assertTrue(sms.should_send('ALL', kind))

    def test_reopen_request_always_alerts_when_enabled(self):
        self.assertTrue(sms.should_send('EXCEPTIONS', 'REO'))
        self.assertFalse(sms.should_send('OFF', 'REO'))


class MessageTests(SimpleTestCase):
    def test_on_time_check_in_is_structured(self):
        text = sms.build_message('IN', 'Juma Ali', 'Kariakoo', at(7, 52))
        self.assertEqual(text.splitlines(), [
            'SMARTPOS - MAHUDHURIO',
            'Juma Ali ameingia kazini',
            'Saa 07:52, 14/11/2026',
            'Hali: Kwa wakati',
            'Tawi: Kariakoo',
        ])

    def test_late_check_in_states_minutes(self):
        text = sms.build_message('IN', 'Juma Ali', 'Kariakoo', at(8, 33), late_minutes=33, is_late=True, work_start=START)
        self.assertIn('Hali: AMECHELEWA dakika 33 (kuanza 08:00)', text)

    def test_check_out_shows_hours_worked(self):
        text = sms.build_message('OUT', 'Juma Ali', 'Kariakoo', at(17, 5), worked_seconds=9 * 3600 + 13 * 60)
        self.assertIn('Juma Ali ameondoka kazini', text)
        self.assertIn('Muda wa kazi: saa 9 na dakika 13', text)
        self.assertIn('Hali: Kwa wakati', text)

    def test_worked_text_variants(self):
        self.assertEqual(sms._worked_text(8 * 3600), 'saa 8')
        self.assertEqual(sms._worked_text(45 * 60), 'dakika 45')

    def test_early_check_out(self):
        text = sms.build_message('OUT', 'Juma Ali', 'Kariakoo', at(16, 20), early_minutes=40, is_early=True,
                                 worked_seconds=8 * 3600, work_end=END)
        self.assertIn('Hali: MAPEMA dakika 40 (kufunga 17:00)', text)

    def test_manual_request(self):
        text = sms.build_message('EXC', 'Juma Ali', 'Kariakoo')
        self.assertIn('Juma Ali anaomba idhini ya mahudhurio.', text)
        self.assertIn('Fungua SmartPOS kuidhinisha.', text)

    def test_reopen_request_names_reason_in_swahili(self):
        text = sms.build_message('REO', 'Juma Ali', 'Kariakoo', reason='Closed by mistake')
        self.assertIn('Juma Ali amefunga siku, anaomba kuifungua tena.', text)
        self.assertIn('Sababu: Alifunga kwa makosa', text)

    def test_reopen_other_reason_keeps_free_text(self):
        self.assertEqual(sms.reopen_reason_text('Other reason: Nilifunga ghafla'), 'Nilifunga ghafla')

    def test_always_one_ascii_segment(self):
        long_name = 'Mwanahamisi Abdulrahmani Kassimu-Ngowi'
        long_branch = 'Duka Kuu la Dawa na Vipodozi - Kariakoo Mtaa wa Uhuru'
        for kind, kwargs in (
            ('IN', dict(local_time=at(9, 59), late_minutes=119, is_late=True, work_start=START)),
            ('OUT', dict(local_time=at(16, 5), early_minutes=55, is_early=True, worked_seconds=25200, work_end=END)),
            ('EXC', {}),
            ('REO', dict(reason='Customer arrived after closing: mteja mkubwa sana')),
        ):
            text = sms.build_message(kind, long_name, long_branch, **kwargs)
            self.assertLessEqual(len(text), 160)
            self.assertTrue(text.isascii())

    def test_accents_are_folded_not_dropped(self):
        text = sms.build_message('IN', 'Jos\u00e9 M\u00fcller', 'Caf\u00e9', at(7, 50))
        self.assertIn('Jose Muller', text)


class QueueGateTests(SimpleTestCase):
    class Branch:
        def __init__(self, mode):
            self.attendance_sms_mode = mode

    def test_off_branch_queues_nothing(self):
        with mock.patch('django.db.transaction.on_commit') as on_commit:
            sms.queue_attendance_sms('IN', 1, self.Branch('OFF'))
        on_commit.assert_not_called()

    def test_enabled_branch_schedules_after_commit(self):
        with mock.patch('django.db.transaction.on_commit') as on_commit:
            sms.queue_attendance_sms('IN', 1, self.Branch('EXCEPTIONS'))
        on_commit.assert_called_once()

    def test_scheduling_errors_never_escape(self):
        with mock.patch('django.db.transaction.on_commit', side_effect=RuntimeError('boom')):
            sms.queue_attendance_sms('OUT', 1, self.Branch('ALL'))
