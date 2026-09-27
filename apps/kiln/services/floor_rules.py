"""灶台相位切换与开灶联锁业务规则。"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.kiln.models import FireHearth

DRAWING_SOFT_POINT_MAX = Decimal("95")

# 冷灶禁止新建值守；只有下列相位可以开灶。
OPENABLE_PHASES = frozenset(
    {
        FireHearth.PHASE_CHARGING,
        FireHearth.PHASE_RAMPING,
        FireHearth.PHASE_HOLDING,
        FireHearth.PHASE_DRAWING,
    }
)


def assert_can_enter_drawing(hearth) -> None:
    """
    进入「出胶」相位前：当前未收灶的 CookRun 须至少有一条
    softPointC <= 95 的 SoftPointProbe。
    """
    open_run = hearth.open_run()
    if open_run is None:
        raise ValidationError(
            {"phase": "无法进入出胶：该灶没有进行中的值守纪录。"}
        )

    ok = open_run.probes.filter(softPointC__lte=DRAWING_SOFT_POINT_MAX).exists()
    if not ok:
        raise ValidationError(
            {
                "phase": (
                    "无法进入出胶：进行中值守尚无软化点探针 "
                    f"≤ {DRAWING_SOFT_POINT_MAX}℃。"
                )
            }
        )


def change_hearth_phase(hearth, new_phase: str):
    """统一入口：改相位时校验出胶规则并保存。"""
    if new_phase == FireHearth.PHASE_DRAWING:
        assert_can_enter_drawing(hearth)

    hearth.phase = new_phase
    hearth.save(update_fields=["phase"])
    return hearth


def assert_phase_allows_open_run(hearth) -> None:
    """冷灶上禁止新建值守；仅 装料/升温/保温/出胶 相位可开灶。"""
    if hearth.phase not in OPENABLE_PHASES:
        raise ValidationError(
            "冷灶禁止开灶：先将灶台切到 装料 / 升温 / 保温 / 出胶 相位。"
        )


def assert_open_within_window(hearth, opened_at) -> None:
    """开灶时刻必须落在灶台允许开灶窗（本地时钟，窗含起止，支持跨午夜）。"""
    if not hearth.is_within_open_window(opened_at):
        local_t = timezone.localtime(opened_at).time()
        span = (
            f"{hearth.openWindowStart:%H:%M}–{hearth.openWindowEnd:%H:%M}"
        )
        raise ValidationError(
            {
                "openedAt": (
                    f"开灶时刻 {local_t:%H:%M} 不在该灶允许开灶窗（{span}）。"
                )
            }
        )


def assert_no_out_of_order_open_run(hearth, opened_at, *, exclude_run=None) -> None:
    """
    同灶开灶时刻联锁（新建与改开灶时刻共用）：

    未收灶值守代表「当前最晚班次」。目标开灶时刻若晚于任一其它未收灶
    值守（即排到了它之后），即造成乱序——按灶过滤后按 openedAt 复算
    排序时，未收灶值守将不再是该灶最晚一条。等时刻允许并列；已收灶
    历史值守不参与约束。
    """
    qs = hearth.runs.filter(closedAt__isnull=True)
    if exclude_run is not None and exclude_run.pk is not None:
        qs = qs.exclude(pk=exclude_run.pk)
    blocking = qs.filter(openedAt__lt=opened_at).order_by("openedAt", "id").first()
    if blocking is not None:
        raise ValidationError(
            {
                "openedAt": (
                    "开灶时刻乱序：该灶已有 "
                    f"{timezone.localtime(blocking.openedAt):%Y-%m-%d %H:%M} "
                    "开灶且尚未收灶的值守，新/改开灶时刻不得晚于它（"
                    "未收灶值守须为同灶最晚班次，等时刻可并列，已收灶历史不限）。"
                )
            }
        )


def assert_can_open_run(hearth, opened_at) -> None:
    """新建值守：相位联锁 + 允许窗 + 同灶乱序联锁。"""
    assert_phase_allows_open_run(hearth)
    assert_open_within_window(hearth, opened_at)
    assert_no_out_of_order_open_run(hearth, opened_at)


def assert_can_change_opened_at(run, opened_at) -> None:
    """
    改开灶时刻（更新路径）：允许窗 + 同灶乱序联锁，约束与新建一致；
    校验时排除被更新的值守自身。相位联锁只管新建，不在此拦截。
    """
    assert_open_within_window(run.hearth, opened_at)
    assert_no_out_of_order_open_run(
        run.hearth, opened_at, exclude_run=run
    )
