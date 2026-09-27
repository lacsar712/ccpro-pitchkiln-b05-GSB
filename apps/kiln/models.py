import datetime

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class ResinLot(models.Model):
    lotCode = models.CharField("来脂批号", max_length=64, unique=True)
    originPlace = models.CharField("来源地", max_length=120)
    arrivalKg = models.DecimalField("到货量(kg)", max_digits=10, decimal_places=2)
    receivedAt = models.DateTimeField("到货时间")

    class Meta:
        ordering = ["-receivedAt", "-id"]
        verbose_name = "来脂批"
        verbose_name_plural = "来脂批"

    def __str__(self):
        return f"{self.lotCode} · {self.originPlace}"


class FireHearth(models.Model):
    PHASE_COLD = "cold"
    PHASE_CHARGING = "charging"
    PHASE_RAMPING = "ramping"
    PHASE_HOLDING = "holding"
    PHASE_DRAWING = "drawing"
    PHASE_CHOICES = [
        (PHASE_COLD, "冷灶"),
        (PHASE_CHARGING, "装料"),
        (PHASE_RAMPING, "升温"),
        (PHASE_HOLDING, "保温"),
        (PHASE_DRAWING, "出胶"),
    ]
    # 允许「新建值守 / 改开灶时刻」的相位：冷灶禁止，其余四相位可建
    OPENABLE_PHASES = (
        PHASE_CHARGING,
        PHASE_RAMPING,
        PHASE_HOLDING,
        PHASE_DRAWING,
    )

    lane = models.PositiveIntegerField("过道号")
    tag = models.CharField("灶牌", max_length=40, unique=True)
    resinGrade = models.CharField("松香品级标签", max_length=80)
    phase = models.CharField(
        "相位",
        max_length=20,
        choices=PHASE_CHOICES,
        default=PHASE_COLD,
    )
    # 灶允许窗：每日允许开灶的时段 [cookWindowStart, cookWindowEnd)。
    # end < start 表示跨午夜（如 20:00–04:00）。
    cookWindowStart = models.TimeField("允许开窗时刻", default=datetime.time(6, 0))
    cookWindowEnd = models.TimeField("允许关窗时刻", default=datetime.time(22, 0))

    class Meta:
        ordering = ["lane", "tag"]
        verbose_name = "灶台"
        verbose_name_plural = "灶台"

    def __str__(self):
        return f"L{self.lane}-{self.tag}"

    def open_runs(self):
        """该灶全部未收灶值守，按开灶时刻从晚到早（看板最新在上）。"""
        return self.runs.filter(closedAt__isnull=True).order_by("-openedAt", "-id")

    def open_run(self):
        """最近一次未收灶的值守（开灶时刻最晚者）；无则 None。"""
        return (
            self.runs.filter(closedAt__isnull=True)
            .select_related("resinLot")
            .order_by("-openedAt", "-id")
            .first()
        )

    def is_within_cook_window(self, moment: datetime.datetime) -> bool:
        """某时刻（按当前时区本地钟点）是否落在灶允许窗内。"""
        local = timezone.localtime(moment).time()
        start = self.cookWindowStart
        end = self.cookWindowEnd
        if start == end:
            # 起止相同视为全天开窗，避免把窗退化成空集
            return True
        if start < end:
            return start <= local < end
        # 跨午夜：start <= local 或 local < end
        return local >= start or local < end


class CookRun(models.Model):
    hearth = models.ForeignKey(
        FireHearth,
        on_delete=models.CASCADE,
        related_name="runs",
        verbose_name="灶台",
    )
    resinLot = models.ForeignKey(
        ResinLot,
        on_delete=models.PROTECT,
        related_name="runs",
        verbose_name="来脂批",
    )
    openedAt = models.DateTimeField("开灶时间")
    closedAt = models.DateTimeField("收灶时间", null=True, blank=True)
    targetSoftPointC = models.DecimalField(
        "目标软化点(℃)", max_digits=6, decimal_places=2
    )

    class Meta:
        ordering = ["-openedAt", "-id"]
        verbose_name = "熬制值守"
        verbose_name_plural = "熬制值守"

    def __str__(self):
        return f"{self.hearth.tag} @ {self.openedAt:%Y-%m-%d %H:%M}"

    @property
    def is_open(self):
        return self.closedAt is None

    def clean(self):
        """
        新建 / 更新开灶时刻的统一联锁：
        1. 相位联锁——冷灶禁止开新值守；
        2. 灶允许窗——开灶钟点须落在该灶每日允许时段；
        3. 同灶未收灶值守按 openedAt 排序不得乱序。

        表单、admin、种子之外的直接写入都走这里，避免「只挡新建、放行更新」。
        """
        from django.forms.forms import NON_FIELD_ERRORS

        from .services.floor_rules import validate_run_opening

        errors = {}
        if self.hearth_id and self.openedAt:
            try:
                validate_run_opening(self.hearth, self.openedAt, exclude_run=self)
            except ValidationError as exc:
                for field, messages in exc.message_dict.items():
                    # CookRun 本身没有 phase 字段（相位挂在灶上），
                    # 相位错误归到非字段错误，表单 / admin 均能展示
                    key = field if field in {"openedAt"} else NON_FIELD_ERRORS
                    errors.setdefault(key, []).extend(messages)
        if errors:
            raise ValidationError(errors)


class SoftPointProbe(models.Model):
    run = models.ForeignKey(
        CookRun,
        on_delete=models.CASCADE,
        related_name="probes",
        verbose_name="值守",
    )
    sampledAt = models.DateTimeField("取样时间")
    softPointC = models.DecimalField("软化点(℃)", max_digits=6, decimal_places=2)
    samplerName = models.CharField("取样人", max_length=80)

    class Meta:
        ordering = ["-sampledAt", "-id"]
        verbose_name = "软化点探针"
        verbose_name_plural = "软化点探针"

    def __str__(self):
        return f"{self.softPointC}℃ by {self.samplerName}"
