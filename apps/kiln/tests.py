import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from .forms import ChangeOpenedAtForm, OpenCookRunForm
from .models import CookRun, FireHearth, ResinLot
from .services.floor_rules import validate_run_opening


def local_dt(day, hour, minute=0):
    return timezone.make_aware(datetime.datetime(day[0], day[1], day[2], hour, minute))


class CookWindowTests(TestCase):
    def setUp(self):
        self.h = FireHearth.objects.create(
            lane=1,
            tag="试灶",
            resinGrade="特级",
            phase=FireHearth.PHASE_HOLDING,
            cookWindowStart=datetime.time(6, 0),
            cookWindowEnd=datetime.time(22, 0),
        )

    def test_within_and_boundaries(self):
        d = (2026, 9, 27)
        self.assertTrue(self.h.is_within_cook_window(local_dt(d, 6)))
        self.assertTrue(self.h.is_within_cook_window(local_dt(d, 21, 59)))
        # 关窗时刻本身排除（半开区间）
        self.assertFalse(self.h.is_within_cook_window(local_dt(d, 22)))
        self.assertFalse(self.h.is_within_cook_window(local_dt(d, 5, 59)))

    def test_overnight_window(self):
        self.h.cookWindowStart = datetime.time(18, 0)
        self.h.cookWindowEnd = datetime.time(6, 0)
        d = (2026, 9, 27)
        self.assertTrue(self.h.is_within_cook_window(local_dt(d, 23)))
        self.assertTrue(self.h.is_within_cook_window(local_dt(d, 2)))
        self.assertFalse(self.h.is_within_cook_window(local_dt(d, 7)))
        self.assertFalse(self.h.is_within_cook_window(local_dt(d, 17, 59)))

    def test_equal_start_end_means_all_day(self):
        self.h.cookWindowStart = self.h.cookWindowEnd = datetime.time(0, 0)
        self.assertTrue(
            self.h.is_within_cook_window(local_dt((2026, 9, 27), 12, 30))
        )


class _FixtureMixin:
    def make_hearth(self, phase=FireHearth.PHASE_HOLDING, start=(6, 0), end=(22, 0),
                    tag="灶"):
        return FireHearth.objects.create(
            lane=1,
            tag=tag,
            resinGrade="特级脂",
            phase=phase,
            cookWindowStart=datetime.time(*start),
            cookWindowEnd=datetime.time(*end),
        )

    def make_lot(self):
        return ResinLot.objects.create(
            lotCode=f"脂-{ResinLot.objects.count() + 1}",
            originPlace="松脂坳",
            arrivalKg=Decimal("1000"),
            receivedAt=timezone.now(),
        )

    def make_run(self, hearth, when, lot=None, save=True):
        run = CookRun(
            hearth=hearth,
            resinLot=lot or self.make_lot(),
            openedAt=when,
            targetSoftPointC=Decimal("90"),
        )
        run.full_clean()
        if save:
            run.save()
        return run


class PhaseInterlockTests(_FixtureMixin, TestCase):
    def test_cold_hearth_cannot_open(self):
        cold = self.make_hearth(phase=FireHearth.PHASE_COLD)
        lot = self.make_lot()
        with self.assertRaises(ValidationError) as ctx:
            validate_run_opening(cold, local_dt((2026, 9, 27), 10))
        # 服务层错误挂在 phase 键；模型 clean() 会映射为非字段错误
        self.assertIn("phase", ctx.exception.message_dict)

        # 模型实例 clean 映射为 __all__
        run = CookRun(
            hearth=cold,
            resinLot=lot,
            openedAt=local_dt((2026, 9, 27), 10),
            targetSoftPointC=Decimal("90"),
        )
        with self.assertRaises(ValidationError) as model_ctx:
            run.full_clean()
        self.assertIn("__all__", model_ctx.exception.message_dict)

        # 表单层同样拒绝
        form = OpenCookRunForm(
            {"resinLot": lot.pk, "openedAt": "2026-09-27T10:00",
             "targetSoftPointC": "90"},
            hearth=cold,
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(CookRun.objects.filter(hearth=cold).count(), 0)

    def test_each_openable_phase_can_open(self):
        lot = self.make_lot()
        for i, phase in enumerate(FireHearth.OPENABLE_PHASES):
            h = self.make_hearth(phase=phase, tag=f"灶-{phase}")
            run = self.make_run(h, local_dt((2026, 9, 27), 8 + i % 12), lot=lot)
            self.assertTrue(run.pk)

    def test_outside_window_rejected_even_in_openable_phase(self):
        h = self.make_hearth(phase=FireHearth.PHASE_RAMPING)
        with self.assertRaises(ValidationError) as ctx:
            validate_run_opening(h, local_dt((2026, 9, 27), 23))
        self.assertIn("openedAt", ctx.exception.message_dict)


class SequenceCreateTests(_FixtureMixin, TestCase):
    def setUp(self):
        # 全天窗，专注顺序约束
        self.h = self.make_hearth(start=(0, 0), end=(0, 0))
        self.lot = self.make_lot()

    def test_new_run_must_not_be_later_than_latest_open_run(self):
        self.make_run(self.h, local_dt((2026, 9, 27), 10), lot=self.lot)
        # 更晚 -> 拒绝
        with self.assertRaises(ValidationError):
            self.make_run(self.h, local_dt((2026, 9, 27), 11), lot=self.lot)
        # 并列 -> 拒绝
        with self.assertRaises(ValidationError):
            self.make_run(self.h, local_dt((2026, 9, 27), 10), lot=self.lot)
        # 更早 -> 允许（即便已有更早值守，也可插在之间）
        self.make_run(self.h, local_dt((2026, 9, 27), 8), lot=self.lot)
        self.make_run(self.h, local_dt((2026, 9, 27), 9), lot=self.lot)
        self.assertEqual(self.h.runs.filter(closedAt__isnull=True).count(), 3)

    def test_closed_runs_do_not_participate(self):
        old = self.make_run(self.h, local_dt((2026, 9, 27), 10), lot=self.lot)
        old.closedAt = local_dt((2026, 9, 27), 15)
        old.save(update_fields=["closedAt"])
        # 没有未收灶值守，任何窗内时刻都可开
        run = self.make_run(self.h, local_dt((2026, 9, 27), 20), lot=self.lot)
        self.assertTrue(run.pk)

    def test_filtered_runs_sort_and_realign(self):
        """按灶过滤后的未收灶值守，按 openedAt 升序须能复算对齐。"""
        t10 = self.make_run(self.h, local_dt((2026, 9, 27), 10), lot=self.lot)
        t08 = self.make_run(self.h, local_dt((2026, 9, 27), 8), lot=self.lot)
        t09 = self.make_run(self.h, local_dt((2026, 9, 27), 9), lot=self.lot)
        runs = list(self.h.runs.filter(closedAt__isnull=True))
        asc = sorted(runs, key=lambda r: (r.openedAt, r.id))
        self.assertEqual([r.pk for r in asc], [t08.pk, t09.pk, t10.pk])
        # open_run() 始终是最晚开灶者
        self.assertEqual(self.h.open_run().pk, t10.pk)


class SequenceUpdateTests(_FixtureMixin, TestCase):
    def setUp(self):
        self.h = self.make_hearth(
            phase=FireHearth.PHASE_DRAWING, start=(0, 0), end=(0, 0)
        )
        self.lot = self.make_lot()
        # 出胶灶两个未收灶值守：A 09:00 更早，B 11:00 更晚。
        # 新建顺序约束要求后建者更早，故先造 B 再造 A。
        self.b = self.make_run(self.h, local_dt((2026, 9, 27), 11), lot=self.lot)
        self.a = self.make_run(self.h, local_dt((2026, 9, 27), 9), lot=self.lot)

    def _reschedule(self, run, new_at):
        return ChangeOpenedAtForm(
            {"openedAt": timezone.localtime(new_at).strftime("%Y-%m-%dT%H:%M")},
            instance=run,
        )

    def test_later_run_cannot_move_earlier_past_sibling(self):
        # B(11) 挪到 08:00，越过 A(09) -> 乱序，拒绝
        form = self._reschedule(self.b, local_dt((2026, 9, 27), 8))
        self.assertFalse(form.is_valid())
        self.b.refresh_from_db()
        self.assertEqual(self.b.openedAt, local_dt((2026, 9, 27), 11))

    def test_earlier_run_cannot_move_later_past_sibling(self):
        # A(09) 挪到 12:00，越过 B(11) -> 乱序，拒绝
        form = self._reschedule(self.a, local_dt((2026, 9, 27), 12))
        self.assertFalse(form.is_valid())

    def test_move_without_crossing_is_allowed(self):
        # A(09) 在 B(11) 之前挪到 10:00，先后不变 -> 允许
        form = self._reschedule(self.a, local_dt((2026, 9, 27), 10))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.a.refresh_from_db()
        self.assertEqual(self.a.openedAt, local_dt((2026, 9, 27), 10))

    def test_tie_on_update_rejected(self):
        form = self._reschedule(self.a, local_dt((2026, 9, 27), 11))
        self.assertFalse(form.is_valid())

    def test_update_still_checked_against_window_and_phase(self):
        # 出胶灶改时仍受窗约束：换成窄窗后挪到窗外即拒
        self.h.cookWindowStart = datetime.time(6, 0)
        self.h.cookWindowEnd = datetime.time(18, 0)
        self.h.save(update_fields=["cookWindowStart", "cookWindowEnd"])
        form = self._reschedule(self.a, local_dt((2026, 9, 27), 10))
        self.assertTrue(form.is_valid(), form.errors)
        form2 = self._reschedule(self.a, local_dt((2026, 9, 27), 5))
        self.assertFalse(form2.is_valid())
        # 冷灶改时同样禁止
        self.h.phase = FireHearth.PHASE_COLD
        self.h.save(update_fields=["phase"])
        form3 = self._reschedule(self.a, local_dt((2026, 9, 27), 10))
        self.assertFalse(form3.is_valid())


class DrawingProbeAndRescheduleViewTests(_FixtureMixin, TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user("t", password="t12345")
        self.client.force_login(user)
        self.h = self.make_hearth(
            phase=FireHearth.PHASE_DRAWING, start=(0, 0), end=(0, 0)
        )
        self.lot = self.make_lot()
        self.latest = self.make_run(
            self.h, local_dt((2026, 9, 27), 11), lot=self.lot
        )
        self.earlier = self.make_run(
            self.h, local_dt((2026, 9, 27), 9), lot=self.lot
        )

    def test_probe_can_still_be_added_on_drawing_hearth(self):
        resp = self.client.post(
            f"/hearth/{self.h.pk}/probe/",
            {
                "sampledAt": "2026-09-27T10:30",
                "softPointC": "94.00",
                "samplerName": "探针工",
                "run_id": self.earlier.pk,
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.earlier.probes.count(), 1)

    def test_reschedule_view_blocks_disorder(self):
        # 更早值守 A(09) 想挪到 12:00（越过 11:00 值守）-> 拒绝
        resp = self.client.post(
            f"/hearth/{self.h.pk}/run/{self.earlier.pk}/reschedule/",
            {"openedAt": "2026-09-27T12:00"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.earlier.refresh_from_db()
        self.assertEqual(self.earlier.openedAt, local_dt((2026, 9, 27), 9))
        # 合法挪动（10:00，仍早于 11:00）-> 通过
        resp = self.client.post(
            f"/hearth/{self.h.pk}/run/{self.earlier.pk}/reschedule/",
            {"openedAt": "2026-09-27T10:00"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.earlier.refresh_from_db()
        self.assertEqual(self.earlier.openedAt, local_dt((2026, 9, 27), 10))


class OpenRunViewTests(_FixtureMixin, TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user("t2", password="t12345")
        self.client.force_login(user)
        self.lot = self.make_lot()

    def test_cold_hearth_open_run_rejected(self):
        cold = self.make_hearth(phase=FireHearth.PHASE_COLD, tag="冷")
        self.client.post(
            f"/hearth/{cold.pk}/open-run/",
            {"resinLot": self.lot.pk, "openedAt": "2026-09-27T10:00",
             "targetSoftPointC": "90"},
        )
        self.assertEqual(CookRun.objects.filter(hearth=cold).count(), 0)
        cold.refresh_from_db()
        self.assertEqual(cold.phase, FireHearth.PHASE_COLD)

    def test_openable_hearth_open_run_keeps_phase(self):
        h = self.make_hearth(phase=FireHearth.PHASE_RAMPING, tag="升温灶")
        self.client.post(
            f"/hearth/{h.pk}/open-run/",
            {"resinLot": self.lot.pk, "openedAt": "2026-09-27T10:00",
             "targetSoftPointC": "90"},
        )
        self.assertEqual(h.runs.count(), 1)
        h.refresh_from_db()
        # 开灶不再自动把相位拨到装料
        self.assertEqual(h.phase, FireHearth.PHASE_RAMPING)


class CloseRunTests(_FixtureMixin, TestCase):
    def setUp(self):
        self.h = self.make_hearth(
            phase=FireHearth.PHASE_HOLDING, start=(0, 0), end=(0, 0)
        )
        self.lot = self.make_lot()
        self.latest = self.make_run(
            self.h, local_dt((2026, 9, 27), 11), lot=self.lot
        )
        self.earlier = self.make_run(
            self.h, local_dt((2026, 9, 27), 9), lot=self.lot
        )

    def test_closing_one_keeps_phase_closing_last_goes_cold(self):
        self.latest.closedAt = timezone.now()
        self.latest.save(update_fields=["closedAt"])
        self.assertTrue(self.h.runs.filter(closedAt__isnull=True).exists())
        # 模拟视图分支：仍有余留值守时不回冷
        self.assertEqual(self.h.open_run().pk, self.earlier.pk)

        self.earlier.closedAt = timezone.now()
        self.earlier.save(update_fields=["closedAt"])
        self.assertFalse(self.h.runs.filter(closedAt__isnull=True).exists())


class SeedTests(TestCase):
    def test_seed_has_multi_run_hearth_in_window_and_is_idempotent(self):
        from .seed import ensure_seed_data

        ensure_seed_data()
        drawing = FireHearth.objects.get(tag="坑火-西一")
        open_runs = list(drawing.runs.filter(closedAt__isnull=True))
        self.assertGreaterEqual(len(open_runs), 2)
        # 每条种子值守开灶钟点都在该灶允许窗内
        for r in open_runs:
            self.assertTrue(drawing.is_within_cook_window(r.openedAt))
        # 升序排序唯一、可复算
        asc = sorted(open_runs, key=lambda r: (r.openedAt, r.id))
        self.assertEqual(len({r.openedAt for r in asc}), len(asc))
        # 再跑一次不报错、不重复造灶
        ensure_seed_data()
        self.assertEqual(FireHearth.objects.filter(tag="坑火-西一").count(), 1)


class DrawerRenderTests(_FixtureMixin, TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user("t3", password="t12345")
        self.client.force_login(user)

    def test_drawer_renders_multi_run_and_cold_states(self):
        # 多值守出胶灶抽屉能渲染
        h = self.make_hearth(
            phase=FireHearth.PHASE_DRAWING, start=(0, 0), end=(0, 0), tag="多灶"
        )
        lot = self.make_lot()
        self.make_run(h, local_dt((2026, 9, 27), 11), lot=lot)
        self.make_run(h, local_dt((2026, 9, 27), 9), lot=lot)
        resp = self.client.get(f"/hearth/{h.pk}/drawer/", HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("叠灶", self.client.get("/").content.decode())
        self.assertIn("改开灶时刻", body)
        self.assertIn("补到值守", body)

        # 冷灶抽屉渲染禁开提示且无开灶表单
        cold = self.make_hearth(phase=FireHearth.PHASE_COLD, tag="冷灶")
        resp = self.client.get(f"/hearth/{cold.pk}/drawer/", HTTP_HX_REQUEST="true")
        body = resp.content.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("冷灶禁止开新值守", body)
        self.assertNotIn('name="openedAt"', body)
