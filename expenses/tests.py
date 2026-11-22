"""Ledger-consistency tests. Run: python manage.py test expenses"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from .ledger import CashLedgerService
from .models import CashAuditEvent, CashClosingSnapshot, CashDay
from .services import create_closing_snapshot, record_audit


class _Base(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from pharmacies.models import Pharmacy
        self.user = get_user_model().objects.create_user(username='mgr', password='x')
        self.pharmacy = Pharmacy.objects.create(name='Test Branch')
        self.day = CashDay.objects.create(
            pharmacy=self.pharmacy, date=timezone.localdate(), opening_cash=Decimal('1000'), opened_by=self.user)


class LedgerWindowTests(_Base):
    def test_empty_day_expected_equals_opening(self):
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('1000'))

    def test_window_net_equals_expected_difference(self):
        t0 = timezone.now() - timedelta(hours=1)
        self.assertEqual(
            CashLedgerService.expected_cash(self.day) - CashLedgerService.expected_cash(self.day, t0),
            CashLedgerService.totals(self.day, after=t0).net,
        )

    def test_no_post_close_activity_when_nothing_after_close(self):
        self.assertFalse(CashLedgerService.has_activity_after(self.day, timezone.now()))


class ImmutabilityTests(_Base):
    def test_audit_event_cannot_be_edited_or_deleted(self):
        ev = record_audit(self.day, CashAuditEvent.Action.SPOT_CHECK, self.user, old=1000, new=900, reason='x')
        ev.reason = 'changed'
        with self.assertRaises(ValidationError):
            ev.save()
        with self.assertRaises(ValidationError):
            ev.delete()

    def test_snapshot_revisions_increment_and_are_frozen(self):
        kw = dict(expected=Decimal('1000'), physical=Decimal('994'), variance=Decimal('-6'))
        s1 = create_closing_snapshot(self.day, self.user, **kw)
        s2 = create_closing_snapshot(self.day, self.user, **kw)
        self.assertEqual((s1.revision, s2.revision), (1, 2))
        with self.assertRaises(ValidationError):
            s1.save()
        self.assertEqual(CashClosingSnapshot.objects.filter(cash_day=self.day).count(), 2)


class ChainDeadEndTests(_Base):
    """V37.15 — a CLOSED day with a stale provisional flag must never dead-end the chain."""

    def _closed(self, date, opening, closing, provisional=False):
        now = timezone.now()
        return CashDay.objects.create(
            pharmacy=self.pharmacy, date=date, opening_cash=Decimal(opening), opened_by=self.user,
            status=CashDay.Status.CLOSED, actual_closing_cash=Decimal(closing), closed_by=self.user,
            closed_at=now - timedelta(days=30), opening_is_provisional=provisional)

    def setUp(self):
        super().setUp()
        self.day.delete()
        self.today = timezone.localdate()

    def test_first_day_provisional_flag_is_healed_with_audit(self):
        from .services import heal_stale_provisional_closed_days
        d1 = self._closed(self.today - timedelta(days=5), '1000', '1000', provisional=True)
        self.assertEqual(heal_stale_provisional_closed_days(self.pharmacy, self.user), 1)
        d1.refresh_from_db()
        self.assertFalse(d1.opening_is_provisional)
        self.assertEqual(d1.opening_cash, Decimal('1000'))  # amount untouched
        self.assertTrue(CashAuditEvent.objects.filter(
            cash_day=d1, action=CashAuditEvent.Action.OPENING_RECONCILED).exists())

    def test_flag_before_later_verified_day_is_healed(self):
        from .services import heal_stale_provisional_closed_days
        old = self._closed(self.today - timedelta(days=6), '500', '700', provisional=True)
        self._closed(self.today - timedelta(days=5), '900', '950')  # mismatch, but later day is verified
        self.assertEqual(heal_stale_provisional_closed_days(self.pharmacy, self.user), 1)
        old.refresh_from_db()
        self.assertFalse(old.opening_is_provisional)

    def test_flag_is_kept_when_nothing_anchors_it(self):
        from .services import heal_stale_provisional_closed_days
        self._closed(self.today - timedelta(days=7), '100', '100')
        prov = self._closed(self.today - timedelta(days=6), '999', '999', provisional=True)  # opening != 100
        self.assertEqual(heal_stale_provisional_closed_days(self.pharmacy, self.user), 0)
        prov.refresh_from_db()
        self.assertTrue(prov.opening_is_provisional)

    def test_handover_cannot_close_a_day_while_an_earlier_day_is_unverified(self):
        from .views import _opening_handover_candidate
        earlier = CashDay.objects.create(  # reopened/unverified earlier day
            pharmacy=self.pharmacy, date=self.today - timedelta(days=2), opening_cash=Decimal('100'),
            opened_by=self.user, status=CashDay.Status.OPEN)
        prev = CashDay.objects.create(
            pharmacy=self.pharmacy, date=self.today - timedelta(days=1), opening_cash=Decimal('100'),
            opened_by=self.user, status=CashDay.Status.DAY_ENDED, day_ended_at=timezone.now())
        cur = CashDay.objects.create(
            pharmacy=self.pharmacy, date=self.today, opening_cash=Decimal('100'), opened_by=self.user,
            opening_source=CashDay.OpeningSource.CARRY_FORWARD, carried_from=prev, opening_is_provisional=True)
        from .models import CashCount
        CashCount.objects.create(cash_day=cur, counted_by=self.user, physical_cash=Decimal('100'),
                                 expected_cash=Decimal('100'), variance=Decimal('0'))
        self.assertIsNone(_opening_handover_candidate(cur))


class CloseContextNamingTests(_Base):
    def test_context_does_not_use_reserved_block_name(self):
        """`block` is overwritten by Django's {% block %} tag inside templates, so a
        context variable of that name is always truthy there (V37.15 root cause)."""
        from .views import _close_page_context
        ctx = _close_page_context(self.day, None, pharmacy=self.pharmacy)
        self.assertNotIn('block', ctx)
        self.assertIsNone(ctx['blocker'])


class CashResetCycleTests(_Base):
    """V50.24: professional two-step reset."""

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        CashDay.objects.filter(pharmacy=self.pharmacy, date=self.today).delete()

    def _plan(self, method, amount):
        from .services import build_cash_reset_plan
        return build_cash_reset_plan(self.pharmacy, self.today, method, Decimal(amount))

    def test_plan_on_open_day_known_opening(self):
        CashDay.objects.create(pharmacy=self.pharmacy, date=self.today, opening_cash=Decimal('50'), opened_by=self.user)
        plan = self._plan('START_OF_DAY', '1000')
        self.assertEqual(plan['opening'], Decimal('1000'))
        self.assertEqual(plan['previous_opening'], Decimal('50'))

    def test_count_now_with_no_movement_equals_opening(self):
        plan = self._plan('COUNT_NOW', '700')
        self.assertEqual(plan['opening'], Decimal('700'))
        self.assertEqual(plan['expected_after'], Decimal('700'))

    def test_closed_day_is_refused(self):
        CashDay.objects.create(pharmacy=self.pharmacy, date=self.today, opening_cash=Decimal('50'), opened_by=self.user,
                               status=CashDay.Status.CLOSED, actual_closing_cash=Decimal('50'))
        with self.assertRaises(ValueError):
            self._plan('START_OF_DAY', '10')

    def test_apply_updates_open_day_and_audits(self):
        from .services import build_cash_reset_plan, make_cash_reset_token, apply_cash_cycle_reset
        day = CashDay.objects.create(pharmacy=self.pharmacy, date=self.today, opening_cash=Decimal('50'), opened_by=self.user)
        plan = build_cash_reset_plan(self.pharmacy, self.today, 'START_OF_DAY', Decimal('900'))
        token = make_cash_reset_token(self.pharmacy, self.user, plan, 'Opening recorded incorrectly earlier')
        reset, _ = apply_cash_cycle_reset(self.pharmacy, self.user, token)
        day.refresh_from_db()
        self.assertEqual(day.opening_cash, Decimal('900'))
        self.assertFalse(day.opening_is_provisional)
        self.assertEqual(reset.previous_opening, Decimal('50'))
        self.assertTrue(CashAuditEvent.objects.filter(cash_day=day, action=CashAuditEvent.Action.OPENING_ADJUSTED).exists())

    def test_tampered_token_rejected(self):
        from .services import apply_cash_cycle_reset
        with self.assertRaises(ValueError):
            apply_cash_cycle_reset(self.pharmacy, self.user, 'not-a-valid-token')


class ExpectedBreakdownTests(_Base):
    """V50.41: itemised expected cash; meals are a subset of cash expenses."""

    def _expense(self, amount, category='Staff Meals', source='CASH_SALES', status='APPROVED'):
        from .models import Expense, ExpenseCategory
        cat, _ = ExpenseCategory.objects.get_or_create(pharmacy=self.pharmacy, name=category)
        return Expense.objects.create(
            pharmacy=self.pharmacy, expense_date=self.day.date, category=cat, title=category,
            amount=Decimal(amount), payment_source=source, submitted_by=self.user, status=status,
            decided_at=timezone.now() if status == 'APPROVED' else None)

    def test_meals_are_subset_not_deducted_twice(self):
        self._expense('300'); self._expense('200', category='Transport')
        b = CashLedgerService.breakdown(self.day)
        self.assertEqual((b['staff_meals'], b['other_expenses'], b['cash_expenses_total']),
                         (Decimal('300'), Decimal('200'), Decimal('500')))
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('500'))

    def test_breakdown_total_matches_expected_cash(self):
        self._expense('120'); self._expense('80', category='Water')
        b = CashLedgerService.breakdown(self.day)
        self.assertEqual(b['expected'], CashLedgerService.expected_cash(self.day))
        self.assertEqual(b['opening'] + b['cash_sales'] - b['cash_refunds'] - b['staff_meals'] - b['other_expenses'],
                         b['expected'])

    def test_mobile_money_meal_does_not_reduce_expected(self):
        self._expense('300', source='MOBILE_MONEY')
        b = CashLedgerService.breakdown(self.day)
        self.assertEqual(b['staff_meals'], Decimal('0'))
        self.assertEqual(b['expected'], Decimal('1000'))

    def test_pending_meal_reported_not_deducted(self):
        self._expense('300', status='PENDING')
        b = CashLedgerService.breakdown(self.day)
        self.assertEqual((b['pending_count'], b['pending_expenses']), (1, Decimal('300')))
        self.assertEqual(b['expected'], Decimal('1000'))

    def test_day_property_matches_ledger(self):
        self._expense('50')
        self.assertEqual(self.day.expected_breakdown['expected'], self.day.expected_closing_cash)


class MealCashPaymentTests(_Base):
    """V50.42: EARNED meals are paid from the drawer automatically and reversibly."""

    def _meal(self, amount='500', status='PENDING', meal_type='LUNCH'):
        from accounts.models import MealLedger
        return MealLedger.objects.create(
            employee=self.user, pharmacy=self.pharmacy, date=self.day.date, meal_type=meal_type,
            amount=Decimal(amount), cutoff_at=timezone.now(), status=status)

    def _set(self, meal, status):
        meal.status = status
        meal.save(update_fields=['status', 'updated_at'])

    def test_earned_meal_reduces_expected_cash_once(self):
        m = self._meal()
        self._set(m, 'EARNED')
        self._set(m, 'EARNED')  # re-saving must not duplicate the payment
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('500'))
        self.assertEqual(m.cash_payments.count(), 1)

    def test_debt_or_pending_meal_does_not_reduce_cash(self):
        m = self._meal(status='DEBT')
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('1000'))
        self.assertEqual(m.cash_payments.count(), 0)

    def test_debt_then_funded_deducts_when_funded(self):
        m = self._meal(status='DEBT')
        self._set(m, 'EARNED')
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('500'))

    def test_reversal_returns_cash(self):
        m = self._meal()
        self._set(m, 'EARNED')
        self._set(m, 'DEBT')   # e.g. funding sale voided
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('1000'))

    def test_re_earned_after_reversal_deducts_again_once(self):
        m = self._meal()
        self._set(m, 'EARNED'); self._set(m, 'DEBT'); self._set(m, 'EARNED')
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('500'))
        self.assertEqual(m.cash_payments.filter(reversed_at__isnull=True).count(), 1)

    def test_legacy_payment_is_never_deducted(self):
        from .models import MealCashPayment
        m = self._meal(status='EARNED')
        MealCashPayment.objects.filter(meal=m).update(is_legacy=True)
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('1000'))

    def test_legacy_settlement_charges_today_once_and_is_idempotent(self):
        from .meal_cash import settle_legacy_meal_payments
        from .models import MealCashPayment
        m = self._meal(status='EARNED')
        MealCashPayment.objects.filter(meal=m).update(is_legacy=True)
        self.assertEqual(settle_legacy_meal_payments(dry_run=True), (1, Decimal('500')))
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('1000'))   # dry run changes nothing
        self.assertEqual(settle_legacy_meal_payments(), (1, Decimal('500')))
        self.assertEqual(settle_legacy_meal_payments(), (0, 0))                        # second run: nothing left
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('500'))    # charged once
        pay = MealCashPayment.objects.get(meal=m)
        self.assertFalse(pay.is_legacy)
        self.assertIsNotNone(pay.legacy_settled_at)

    def test_legacy_settlement_skips_meals_already_inside_a_physical_count(self):
        from datetime import timedelta
        from accounts.models import MealLedger
        from .meal_cash import settle_legacy_meal_payments
        from .models import CashDay, MealCashPayment
        yesterday = self.day.date - timedelta(days=1)
        CashDay.objects.create(pharmacy=self.pharmacy, date=yesterday, opening_cash=Decimal('0'),
                               actual_closing_cash=Decimal('900'), opened_by=self.user)
        old = MealLedger.objects.create(employee=self.user, pharmacy=self.pharmacy, date=yesterday - timedelta(days=3),
                                        meal_type='LUNCH', amount=Decimal('800'), cutoff_at=timezone.now(), status='EARNED')
        new = MealLedger.objects.create(employee=self.user, pharmacy=self.pharmacy, date=self.day.date,
                                        meal_type='DINNER', amount=Decimal('500'), cutoff_at=timezone.now(), status='EARNED')
        MealCashPayment.objects.filter(meal__in=[old, new]).update(is_legacy=True)
        self.assertEqual(settle_legacy_meal_payments(), (1, Decimal('500')))   # the old one is inside the count
        self.assertTrue(MealCashPayment.objects.get(meal=old).is_legacy)

    def test_legacy_meal_already_deducted_as_manual_expense_is_not_charged_again(self):
        """V54.30: a legacy meal the manager already recorded as a Staff Meals expense is never charged twice."""
        from accounts.models import MealLedger
        from .meal_cash import legacy_meal_plan, settle_legacy_meal_payments
        from .models import Expense, ExpenseCategory, MealCashPayment
        covered = MealLedger.objects.create(employee=self.user, pharmacy=self.pharmacy, date=self.day.date,
                                            meal_type='LUNCH', amount=Decimal('300'), cutoff_at=timezone.now(), status='EARNED')
        open_ = MealLedger.objects.create(employee=self.user, pharmacy=self.pharmacy, date=self.day.date,
                                          meal_type='DINNER', amount=Decimal('500'), cutoff_at=timezone.now(), status='EARNED')
        MealCashPayment.objects.filter(meal__in=[covered, open_]).update(is_legacy=True)
        cat, _ = ExpenseCategory.objects.get_or_create(pharmacy=self.pharmacy, name='Staff Meals')
        Expense.objects.create(pharmacy=self.pharmacy, expense_date=self.day.date, category=cat, title='meals',
                               amount=Decimal('300'), payment_source='CASH_SALES', submitted_by=self.user,
                               status='APPROVED', decided_at=timezone.now())
        reasons = {x['payment'].meal_id: x['reason'] for x in legacy_meal_plan(self.pharmacy.pk)}
        self.assertEqual(reasons, {covered.pk: 'expense', open_.pk: 'charge'})
        self.assertEqual(settle_legacy_meal_payments(), (1, Decimal('500')))

    def test_repair_gives_back_over_settled_meals_and_is_idempotent(self):
        """V54.30: a meal settled although it is already inside a physical count goes back to 'never charged'."""
        from datetime import timedelta
        from accounts.models import MealLedger
        from .meal_cash import repair_over_settled_meals
        from .models import CashDay, MealCashPayment
        yesterday = self.day.date - timedelta(days=1)
        CashDay.objects.create(pharmacy=self.pharmacy, date=yesterday, opening_cash=Decimal('0'),
                               actual_closing_cash=Decimal('900'), opened_by=self.user)
        old = MealLedger.objects.create(employee=self.user, pharmacy=self.pharmacy, date=yesterday - timedelta(days=2),
                                        meal_type='LUNCH', amount=Decimal('800'), cutoff_at=timezone.now(), status='EARNED')
        now = timezone.now()
        MealCashPayment.objects.filter(meal=old).update(is_legacy=False, paid_at=now, legacy_settled_at=now)
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('200'))      # over-charged
        self.assertEqual(repair_over_settled_meals(dry_run=True), (1, Decimal('800')))
        self.assertEqual(repair_over_settled_meals(), (1, Decimal('800')))
        self.assertEqual(repair_over_settled_meals(), (0, 0))
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('1000'))     # given back
        self.assertTrue(MealCashPayment.objects.get(meal=old).is_legacy)

    def test_legacy_meal_of_open_past_day_is_charged_on_that_day(self):
        """V54.32: yesterday's legacy meal leaves yesterday's drawer, not today's."""
        from datetime import timedelta
        from accounts.models import MealLedger
        from .meal_cash import settle_legacy_meal_payments
        from .models import CashDay, MealCashPayment
        yesterday = self.day.date - timedelta(days=1)
        past = CashDay.objects.create(pharmacy=self.pharmacy, date=yesterday, opening_cash=Decimal('1000'),
                                      status='DAY_ENDED', opened_by=self.user)
        m = MealLedger.objects.create(employee=self.user, pharmacy=self.pharmacy, date=yesterday, meal_type='LUNCH',
                                      amount=Decimal('400'), cutoff_at=timezone.now(), status='EARNED')
        MealCashPayment.objects.filter(meal=m).update(is_legacy=True)
        self.assertEqual(settle_legacy_meal_payments(), (1, Decimal('400')))
        self.assertEqual(CashLedgerService.expected_cash(past), Decimal('600'))
        self.assertEqual(settle_legacy_meal_payments(), (0, 0))

    def test_late_meal_payment_moves_onto_meal_day(self):
        """V54.33: a meal earned after midnight is deducted on the meal's own (not closed) day."""
        from datetime import timedelta
        from accounts.models import MealLedger
        from .meal_cash import relocate_late_meal_payments
        from .models import CashDay
        yesterday = self.day.date - timedelta(days=1)
        past = CashDay.objects.create(pharmacy=self.pharmacy, date=yesterday, opening_cash=Decimal('1000'),
                                      status='DAY_ENDED', opened_by=self.user)
        m = MealLedger.objects.create(employee=self.user, pharmacy=self.pharmacy, date=yesterday, meal_type='LUNCH',
                                      amount=Decimal('400'), cutoff_at=timezone.now(), status='PENDING')
        m.status = 'EARNED'; m.save(update_fields=['status', 'updated_at'])     # paid_at = now (today)
        self.assertEqual(CashLedgerService.expected_cash(past), Decimal('1000'))
        self.assertEqual(len(relocate_late_meal_payments()), 1)
        self.assertEqual(CashLedgerService.expected_cash(past), Decimal('600'))
        self.assertEqual(relocate_late_meal_payments(), [])

    def test_breakdown_and_totals_agree(self):
        m = self._meal(); self._set(m, 'EARNED')
        b = CashLedgerService.breakdown(self.day)
        self.assertEqual(b['staff_meals_auto'], Decimal('500'))
        self.assertEqual(b['expected'], CashLedgerService.expected_cash(self.day))
        daily = CashLedgerService.daily_totals(self.pharmacy.pk, self.day.date, self.day.date)
        self.assertEqual(daily[self.day.date].cash_meals, Decimal('500'))

    def test_meal_and_manual_staff_meal_expense_both_reported(self):
        from .models import Expense, ExpenseCategory
        m = self._meal(); self._set(m, 'EARNED')
        cat, _ = ExpenseCategory.objects.get_or_create(pharmacy=self.pharmacy, name='Staff Meals')
        Expense.objects.create(pharmacy=self.pharmacy, expense_date=self.day.date, category=cat, title='x',
                               amount=Decimal('100'), payment_source='CASH_SALES', submitted_by=self.user,
                               status='APPROVED', decided_at=timezone.now())
        b = CashLedgerService.breakdown(self.day)
        self.assertEqual((b['staff_meals_auto'], b['staff_meals_expense'], b['staff_meals']),
                         (Decimal('500'), Decimal('100'), Decimal('600')))
        self.assertEqual(b['expected'], Decimal('400'))


class LateApprovedExpenseTests(_Base):
    """V54.68: a cash expense that waited as PENDING is deducted on the day it is APPROVED."""

    def test_pending_expense_from_earlier_date_is_deducted_today_on_approval(self):
        from .models import Expense, ExpenseCategory
        from .services import approve_expense
        cat = ExpenseCategory.objects.create(name='Transport')
        old = timezone.localdate() - timedelta(days=5)
        exp = Expense.objects.create(
            pharmacy=self.pharmacy, expense_date=old, category=cat, title='Delivery', amount=Decimal('300'),
            submitted_by=self.user)
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('1000'))  # pending: not deducted
        approve_expense(exp, self.user)
        exp.refresh_from_db()
        self.assertEqual(exp.cash_date, timezone.localdate())
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('700'))
        totals = CashLedgerService.daily_totals(self.pharmacy.pk, old, timezone.localdate())
        self.assertNotIn(old, totals)  # the old day is not rewritten

    def test_legacy_expense_without_cash_date_keeps_expense_date(self):
        from .models import Expense, ExpenseCategory
        cat = ExpenseCategory.objects.create(name='Misc')
        Expense.objects.create(
            pharmacy=self.pharmacy, expense_date=timezone.localdate(), category=cat, title='Old', amount=Decimal('50'),
            submitted_by=self.user, status=Expense.Status.APPROVED, decided_at=timezone.now())
        self.assertEqual(CashLedgerService.expected_cash(self.day), Decimal('950'))


class CashRulesTests(__import__('django.test', fromlist=['SimpleTestCase']).SimpleTestCase):
    """V54.69: sub-shilling residue must never show as a Shortage/Overage."""

    def test_clean_variance_rounds_and_zeroes_residue(self):
        from decimal import Decimal
        from .cash_rules import clean_variance
        self.assertEqual(clean_variance(Decimal('-0.30')), Decimal('0'))
        self.assertEqual(clean_variance(Decimal('0.49')), Decimal('0'))
        self.assertEqual(clean_variance(Decimal('-0.50')), Decimal('-1'))
        self.assertEqual(clean_variance(Decimal('35000.00')), Decimal('35000'))
        self.assertIsNone(clean_variance(None))
