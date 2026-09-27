from datetime import datetime, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.kiln.forms import CookRunUpdateForm, OpenCookRunForm
from apps.kiln.models import CookRun, FireHearth, ResinLot
from apps.kiln.seed import ensure_seed_data
from apps.kiln.services.floor_rules import (
    assert_can_change_opened_at,
    assert_can_open_run,
)


def at(day_offset, hh, mm):
    tz = timezone.get_current_timezone()
    day = timezone.localtime(timezone.now()).date() + timezone.timedelta(
        days=day_offset
    )
    return timezone.make_aware(datetime.combine(day, time(hh, mm)), tz)


class OpenWindowTests(TestCase):
    def setUp(self):
        self.day_hearth = FireHearth(
            tag="日班灶",
            openWindowStart=time(5, 0),
            openWindowEnd=time(22, 0),
        )
        self.night_hearth = FireHearth(
            tag="夜班灶",
            openWindowStart=time(22, 0),
            openWindowEnd=time(5, 0),
        )

    def test_daytime_window_contains_and_excludes(self):
        self.assertTrue(self.day_hearth.is_within_open_window(at(0, 5, 0)))
        self.assertTrue(self.day_hearth.is_within_open_window(at(0, 12, 0)))
        self.assertTrue(self.day_hearth.is_within_open_window(at(0, 22, 0)))
        self.assertFalse(self.day_hearth.is_within_open_window(at(0, 4, 59)))
        self.assertFalse(self.day_hearth.is_within_open_window(at(0, 22, 1)))

    def test_overnight_window(self):
        self.assertTrue(self.night_hearth.is_within_open_window(at(0, 22, 0)))
        self.assertTrue(self.night_hearth.is_within_open_window(at(0, 23, 30)))
        self.assertTrue(self.night_hearth.is_within_open_window(at(0, 2, 0)))
        self.assertTrue(self.night_hearth.is_within_open_window(at(0, 5, 0)))
        self.assertFalse(self.night_hearth.is_within_open_window(at(0, 12, 0)))

    def test_opening_outside_window_is_rejected(self):
        hearth = FireHearth.objects.create(
            lane=1,
            tag="日班灶",
            phase=FireHearth.PHASE_HOLDING,
            openWindowStart=time(5, 0),
            openWindowEnd=time(22, 0),
        )
        with self.assertRaises(ValidationError) as ctx:
            assert_can_open_run(hearth, at(0, 23, 0))
        self.assertIn("openedAt", ctx.exception.message_dict)


class PhaseInterlockTests(TestCase):
    def setUp(self):
        self.lot = ResinLot.objects.create(
            lotCode="L1",
            originPlace="松脂坳",
            arrivalKg=Decimal("100"),
            receivedAt=timezone.now(),
        )

    def _hearth(self, phase, tag):
        return FireHearth.objects.create(
            lane=1,
            tag=tag,
            phase=phase,
            openWindowStart=time(0, 0),
            openWindowEnd=time(23, 59),
        )

    def test_cold_hearth_cannot_open_run(self):
        hearth = self._hearth(FireHearth.PHASE_COLD, "冷灶")
        with self.assertRaises(ValidationError):
            assert_can_open_run(hearth, at(0, 10, 0))

    def test_all_four_hot_phases_can_open_run(self):
        for i, phase in enumerate(
            [
                FireHearth.PHASE_CHARGING,
                FireHearth.PHASE_RAMPING,
                FireHearth.PHASE_HOLDING,
                FireHearth.PHASE_DRAWING,
            ]
        ):
            hearth = self._hearth(phase, f"灶{i}")
            # 不抛异常即通过。
            assert_can_open_run(hearth, at(-1, 10, 0))


class OpeningOrderRuleTests(TestCase):
    def setUp(self):
        self.lot = ResinLot.objects.create(
            lotCode="L1",
            originPlace="松脂坳",
            arrivalKg=Decimal("100"),
            receivedAt=timezone.now(),
        )
        self.hearth = FireHearth.objects.create(
            lane=1,
            tag="顺序灶",
            phase=FireHearth.PHASE_HOLDING,
            openWindowStart=time(0, 0),
            openWindowEnd=time(23, 59),
        )

    def test_earlier_open_run_blocks_later_opening(self):
        CookRun.objects.create(
            hearth=self.hearth,
            resinLot=self.lot,
            openedAt=at(-1, 9, 0),
            targetSoftPointC=Decimal("88"),
        )
        with self.assertRaises(ValidationError):
            assert_can_open_run(self.hearth, at(-1, 10, 0))

    def test_equal_opened_at_is_allowed(self):
        CookRun.objects.create(
            hearth=self.hearth,
            resinLot=self.lot,
            openedAt=at(-1, 9, 0),
            targetSoftPointC=Decimal("88"),
        )
        assert_can_open_run(self.hearth, at(-1, 9, 0))  # 不抛异常

    def test_opening_before_open_run_is_allowed(self):
        CookRun.objects.create(
            hearth=self.hearth,
            resinLot=self.lot,
            openedAt=at(-1, 9, 0),
            targetSoftPointC=Decimal("88"),
        )
        assert_can_open_run(self.hearth, at(-1, 8, 0))

    def test_closed_runs_do_not_constrain(self):
        CookRun.objects.create(
            hearth=self.hearth,
            resinLot=self.lot,
            openedAt=at(-2, 9, 0),
            closedAt=at(-2, 15, 0),
            targetSoftPointC=Decimal("88"),
        )
        # 晚于已收灶历史仍可开新灶。
        assert_can_open_run(self.hearth, at(-1, 18, 0))

    def test_update_moving_after_another_open_run_is_rejected(self):
        run_a = CookRun.objects.create(
            hearth=self.hearth,
            resinLot=self.lot,
            openedAt=at(-1, 9, 0),
            targetSoftPointC=Decimal("88"),
        )
        CookRun.objects.create(
            hearth=self.hearth,
            resinLot=self.lot,
            openedAt=at(-1, 9, 0),
            targetSoftPointC=Decimal("88"),
        )
        # 把 A 挪到另一条未收灶值守之后 → 乱序，拒绝。
        with self.assertRaises(ValidationError):
            assert_can_change_opened_at(run_a, at(-1, 9, 30))
        # 等时刻 / 更早则允许（更新不被自身挡住）。
        assert_can_change_opened_at(run_a, at(-1, 9, 0))
        assert_can_change_opened_at(run_a, at(-1, 8, 0))

    def test_update_closed_history_after_open_run_is_still_rejected(self):
        closed = CookRun.objects.create(
            hearth=self.hearth,
            resinLot=self.lot,
            openedAt=at(-2, 8, 0),
            closedAt=at(-2, 12, 0),
            targetSoftPointC=Decimal("88"),
        )
        CookRun.objects.create(
            hearth=self.hearth,
            resinLot=self.lot,
            openedAt=at(-1, 9, 0),
            targetSoftPointC=Decimal("88"),
        )
        # 把已收灶历史改到未收灶值守之后，同样造成乱序，照样拒绝。
        with self.assertRaises(ValidationError):
            assert_can_change_opened_at(closed, at(-1, 10, 0))
        # 挪到在值之前则允许（窗内）。
        assert_can_change_opened_at(closed, at(-1, 8, 0))

    def test_update_outside_window_is_rejected(self):
        hearth = FireHearth.objects.create(
            lane=2,
            tag="窄窗灶",
            phase=FireHearth.PHASE_HOLDING,
            openWindowStart=time(5, 0),
            openWindowEnd=time(22, 0),
        )
        run = CookRun.objects.create(
            hearth=hearth,
            resinLot=self.lot,
            openedAt=at(-1, 9, 0),
            targetSoftPointC=Decimal("88"),
        )
        with self.assertRaises(ValidationError):
            assert_can_change_opened_at(run, at(-1, 23, 0))


class OrderRecomputationTests(TestCase):
    def setUp(self):
        self.lot = ResinLot.objects.create(
            lotCode="L1",
            originPlace="松脂坳",
            arrivalKg=Decimal("100"),
            receivedAt=timezone.now(),
        )
        self.hearth = FireHearth.objects.create(
            lane=1,
            tag="复算灶",
            phase=FireHearth.PHASE_HOLDING,
            openWindowStart=time(0, 0),
            openWindowEnd=time(23, 59),
        )

    def test_filtered_runs_sort_by_opened_at_and_open_must_be_last(self):
        # 直接造一条“在值但更早”的脏数据：复算时它不是最末，
        # 任何晚于它的新开灶都会被规则挡下。
        open_run = CookRun.objects.create(
            hearth=self.hearth,
            resinLot=self.lot,
            openedAt=at(-2, 8, 0),
            targetSoftPointC=Decimal("88"),
        )
        closed_later = CookRun.objects.create(
            hearth=self.hearth,
            resinLot=self.lot,
            openedAt=at(-1, 10, 0),
            closedAt=at(-1, 14, 0),
            targetSoftPointC=Decimal("88"),
        )

        ordered = list(self.hearth.runs_in_opening_order())
        self.assertEqual([r.pk for r in ordered], [open_run.pk, closed_later.pk])
        self.assertEqual(ordered[-1].pk, closed_later.pk)

        # 乱序状态下，09:00 开新灶晚于 08:00 的在值值守，必被拦。
        with self.assertRaises(ValidationError):
            assert_can_open_run(self.hearth, at(-1, 9, 0))


class FormTests(TestCase):
    def setUp(self):
        self.lot = ResinLot.objects.create(
            lotCode="L1",
            originPlace="松脂坳",
            arrivalKg=Decimal("100"),
            receivedAt=timezone.now(),
        )

    def test_open_form_rejects_cold_hearth(self):
        hearth = FireHearth.objects.create(
            lane=1, tag="冷灶", phase=FireHearth.PHASE_COLD
        )
        form = OpenCookRunForm(
            {"resinLot": self.lot.pk,
             "openedAt": at(0, 10, 0).strftime("%Y-%m-%dT%H:%M"),
             "targetSoftPointC": "88"},
            hearth=hearth,
        )
        self.assertFalse(form.is_valid())

    def test_open_form_rejects_out_of_window(self):
        hearth = FireHearth.objects.create(
            lane=1,
            tag="窄窗灶",
            phase=FireHearth.PHASE_HOLDING,
            openWindowStart=time(5, 0),
            openWindowEnd=time(22, 0),
        )
        form = OpenCookRunForm(
            {"resinLot": self.lot.pk,
             "openedAt": at(0, 23, 0).strftime("%Y-%m-%dT%H:%M"),
             "targetSoftPointC": "88"},
            hearth=hearth,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("openedAt", form.errors)

    def test_update_form_rejects_disordering(self):
        hearth = FireHearth.objects.create(
            lane=1,
            tag="顺序灶",
            phase=FireHearth.PHASE_HOLDING,
            openWindowStart=time(0, 0),
            openWindowEnd=time(23, 59),
        )
        run_a = CookRun.objects.create(
            hearth=hearth,
            resinLot=self.lot,
            openedAt=at(-1, 9, 0),
            targetSoftPointC=Decimal("88"),
        )
        CookRun.objects.create(
            hearth=hearth,
            resinLot=self.lot,
            openedAt=at(-1, 9, 0),
            targetSoftPointC=Decimal("88"),
        )
        form = CookRunUpdateForm(
            {"openedAt": at(-1, 10, 0).strftime("%Y-%m-%dT%H:%M")},
            instance=run_a,
            run=run_a,
        )
        self.assertFalse(form.is_valid())


class ViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="tester")
        self.client.force_login(user)
        self.lot = ResinLot.objects.create(
            lotCode="L1",
            originPlace="松脂坳",
            arrivalKg=Decimal("100"),
            receivedAt=timezone.now(),
        )

    def test_post_open_run_on_cold_hearth_does_nothing(self):
        hearth = FireHearth.objects.create(
            lane=1, tag="冷灶", phase=FireHearth.PHASE_COLD
        )
        resp = self.client.post(
            f"/hearth/{hearth.pk}/open-run/",
            {
                "resinLot": self.lot.pk,
                "openedAt": at(0, 10, 0).strftime("%Y-%m-%dT%H:%M"),
                "targetSoftPointC": "88",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(CookRun.objects.filter(hearth=hearth).exists())
        hearth.refresh_from_db()
        self.assertEqual(hearth.phase, FireHearth.PHASE_COLD)

    def test_post_open_run_on_hot_hearth_succeeds(self):
        hearth = FireHearth.objects.create(
            lane=1,
            tag="保温灶",
            phase=FireHearth.PHASE_HOLDING,
            openWindowStart=time(0, 0),
            openWindowEnd=time(23, 59),
        )
        self.client.post(
            f"/hearth/{hearth.pk}/open-run/",
            {
                "resinLot": self.lot.pk,
                "openedAt": at(-1, 10, 0).strftime("%Y-%m-%dT%H:%M"),
                "targetSoftPointC": "88",
            },
        )
        self.assertTrue(CookRun.objects.filter(hearth=hearth).exists())

    def test_post_open_run_after_later_open_run_is_blocked(self):
        hearth = FireHearth.objects.create(
            lane=1,
            tag="顺序灶",
            phase=FireHearth.PHASE_HOLDING,
            openWindowStart=time(0, 0),
            openWindowEnd=time(23, 59),
        )
        CookRun.objects.create(
            hearth=hearth,
            resinLot=self.lot,
            openedAt=at(-1, 12, 0),
            targetSoftPointC=Decimal("88"),
        )
        self.client.post(
            f"/hearth/{hearth.pk}/open-run/",
            {
                "resinLot": self.lot.pk,
                "openedAt": at(-1, 13, 0).strftime("%Y-%m-%dT%H:%M"),
                "targetSoftPointC": "88",
            },
        )
        self.assertEqual(CookRun.objects.filter(hearth=hearth).count(), 1)

    def test_post_edit_run_disordering_is_blocked(self):
        hearth = FireHearth.objects.create(
            lane=1,
            tag="顺序灶",
            phase=FireHearth.PHASE_HOLDING,
            openWindowStart=time(0, 0),
            openWindowEnd=time(23, 59),
        )
        run_a = CookRun.objects.create(
            hearth=hearth,
            resinLot=self.lot,
            openedAt=at(-1, 9, 0),
            targetSoftPointC=Decimal("88"),
        )
        CookRun.objects.create(
            hearth=hearth,
            resinLot=self.lot,
            openedAt=at(-1, 12, 0),
            targetSoftPointC=Decimal("88"),
        )
        resp = self.client.post(
            f"/hearth/{hearth.pk}/run/{run_a.pk}/edit/",
            {"openedAt": at(-1, 13, 0).strftime("%Y-%m-%dT%H:%M")},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        run_a.refresh_from_db()
        self.assertEqual(run_a.openedAt, at(-1, 9, 0))

    def test_drawing_hearth_can_still_add_probe(self):
        hearth = FireHearth.objects.create(
            lane=2,
            tag="出胶灶",
            phase=FireHearth.PHASE_DRAWING,
            openWindowStart=time(0, 0),
            openWindowEnd=time(23, 59),
        )
        run = CookRun.objects.create(
            hearth=hearth,
            resinLot=self.lot,
            openedAt=at(-1, 9, 0),
            targetSoftPointC=Decimal("88"),
        )
        resp = self.client.post(
            f"/hearth/{hearth.pk}/probe/",
            {
                "sampledAt": at(0, 9, 0).strftime("%Y-%m-%dT%H:%M"),
                "softPointC": "93.50",
                "samplerName": "值守测试",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(run.probes.count(), 1)


class SeedDataTests(TestCase):
    def test_seed_has_multi_run_hearth_with_aligned_order(self):
        ensure_seed_data()
        hearth = FireHearth.objects.get(tag="坑火-西一")
        runs = list(hearth.runs_in_opening_order())
        self.assertGreaterEqual(len(runs), 3)
        # 全部落在跨午夜允许窗 22:00–05:00。
        for r in runs:
            self.assertTrue(hearth.is_within_open_window(r.openedAt))
        # 复算顺序对齐：最末一条为未收灶（当前最晚班次）。
        self.assertIsNone(runs[-1].closedAt)
        self.assertGreaterEqual(runs[-1].openedAt, runs[0].openedAt)
        # 至少两条已收灶历史。
        self.assertGreaterEqual(sum(1 for r in runs if r.closedAt is not None), 2)
