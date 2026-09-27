"""灶台相位切换与开灶联锁业务规则。

开灶（新建值守 / 修改开灶时刻）三重联锁，统一由 ``validate_run_opening`` 校验：

1. 相位联锁——冷灶禁止开新值守，仅装料 / 升温 / 保温 / 出胶四相位可建；
2. 灶允许窗——开灶钟点须落在该灶每日允许时段（允许跨午夜）；
3. 同灶顺序——同灶未收灶值守按开灶时刻排序须能复算对齐，
   新建不得晚于（或并列于）已有的最晚开灶值守；更新不得跨越任何
   其它未收灶值守（双向都算乱序）。
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

DRAWING_SOFT_POINT_MAX = Decimal("95")


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
    from apps.kiln.models import FireHearth

    if new_phase == FireHearth.PHASE_DRAWING:
        assert_can_enter_drawing(hearth)

    hearth.phase = new_phase
    hearth.save(update_fields=["phase"])
    return hearth


def assert_phase_allows_opening(hearth) -> None:
    """冷灶禁止开新值守；装料 / 升温 / 保温 / 出胶才可建。"""
    from apps.kiln.models import FireHearth

    if hearth.phase not in FireHearth.OPENABLE_PHASES:
        raise ValidationError(
            {
                "phase": (
                    f"当前相位「{hearth.get_phase_display()}」禁止开新值守："
                    "冷灶不能开灶，仅装料、升温、保温、出胶相位可建值守。"
                )
            }
        )


def assert_within_cook_window(hearth, opened_at) -> None:
    """开灶时刻（本地钟点）须落在灶允许窗内。"""
    if not hearth.is_within_cook_window(opened_at):
        local = timezone.localtime(opened_at)
        raise ValidationError(
            {
                "openedAt": (
                    f"开灶时刻 {local:%H:%M} 不在灶允许窗 "
                    f"{hearth.cookWindowStart:%H:%M}–"
                    f"{hearth.cookWindowEnd:%H:%M} 内。"
                )
            }
        )


def assert_open_run_sequence(hearth, opened_at, exclude_run=None) -> None:
    """
    同灶未收灶值守的开灶时刻顺序约束。

    何谓乱序：同灶未收灶值守存在一条公认先后，即按 openedAt 升序排列的
    结果。任何一次新建 / 改时之后，该排序仍须能复算出同一先后——

    * 新建：新时刻必须**严格早于**当前最晚开灶的未收灶值守
      （可插在更早的值守之间，但不得晚于或并列于最晚者）；
    * 更新：被改值守相对每一条其它未收灶值守的先后都不得改变，
      即既不能挪到更晚值守的后面，也不能越过更早值守（含并列）。

    只校验未收灶（closedAt 为空）值守；已收灶的历史值守不参与联锁。
    """
    siblings_qs = hearth.runs.filter(closedAt__isnull=True)
    if exclude_run is not None and exclude_run.pk:
        siblings_qs = siblings_qs.exclude(pk=exclude_run.pk)
    siblings = list(siblings_qs.only("id", "openedAt"))
    if not siblings:
        return

    # 开灶时刻并列会让 openedAt 排序无法确定唯一先后，一律拒绝
    for other in siblings:
        if other.openedAt == opened_at:
            raise ValidationError(
                {
                    "openedAt": (
                        "开灶时刻与同灶另一未收灶值守并列，"
                        "无法按开灶时刻排序对齐，请错开至少一分钟。"
                    )
                }
            )

    if exclude_run is not None and exclude_run.pk:
        # 更新路径：相对任一兄弟值守的前后关系都必须保持不变（双向禁跨）。
        # 注意 exclude_run.openedAt 在表单校验阶段已是新值，旧值须从库中取。
        from apps.kiln.models import CookRun

        old_row = CookRun.objects.filter(pk=exclude_run.pk).values_list(
            "openedAt", flat=True
        ).first()
        old_at = old_row if old_row is not None else exclude_run.openedAt
        for other in siblings:
            later_before = other.openedAt > old_at
            later_after = other.openedAt > opened_at
            if later_before != later_after:
                direction = "之后" if later_before else "之前"
                raise ValidationError(
                    {
                        "openedAt": (
                            f"不得把开灶时刻挪到同灶另一未收灶值守"
                            f"（{timezone.localtime(other.openedAt):%m-%d %H:%M}）"
                            f"{direction}：按开灶时刻排序会乱序。"
                        )
                    }
                )
    else:
        # 新建路径：不得晚于（含并列）当前最晚开灶的未收灶值守
        latest = max(siblings, key=lambda r: (r.openedAt, r.id))
        if opened_at > latest.openedAt:
            raise ValidationError(
                {
                    "openedAt": (
                        "同灶已有开灶更晚（"
                        f"{timezone.localtime(latest.openedAt):%m-%d %H:%M}）"
                        "的未收灶值守，新值守开灶时刻不得再晚于它。"
                    )
                }
            )


def validate_run_opening(hearth, opened_at, exclude_run=None) -> None:
    """
    开灶联锁统一入口：相位 + 允许窗 + 同灶顺序，错误一次性聚合抛出。
    新建与更新（改开灶时刻）共用，exclude_run 为被更新的值守自身。
    """
    errors: dict[str, list[str]] = {}
    for check in (
        lambda: assert_phase_allows_opening(hearth),
        lambda: assert_within_cook_window(hearth, opened_at),
        lambda: assert_open_run_sequence(hearth, opened_at, exclude_run),
    ):
        try:
            check()
        except ValidationError as exc:
            for field, messages in exc.message_dict.items():
                errors.setdefault(field, []).extend(messages)
    if errors:
        raise ValidationError(errors)
