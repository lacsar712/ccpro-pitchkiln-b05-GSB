from datetime import datetime, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import CookRun, FireHearth, ResinLot, SoftPointProbe


def ensure_seed_data():
    """幂等种子：账号 + 来脂批 / 灶台 / 值守 / 探针。"""
    User = get_user_model()

    if not User.objects.filter(username="admin").exists():
        User.objects.create_superuser("admin", "admin@pitchkiln.local", "123456")

    if not User.objects.filter(username="worker").exists():
        User.objects.create_user("worker", "worker@pitchkiln.local", "123456")

    if FireHearth.objects.exists():
        return

    now = timezone.now()
    tz = timezone.get_current_timezone()
    base_day = timezone.localtime(now).date()

    def at(days_offset, hh, mm):
        """本地时区（Asia/Shanghai）某日 HH:MM 的感知时间，固定落在允许窗内。"""
        day = base_day + timezone.timedelta(days=days_offset)
        return timezone.make_aware(datetime.combine(day, time(hh, mm)), tz)

    lot_a = ResinLot.objects.create(
        lotCode="脂-松脂坳-2409A",
        originPlace="松脂坳东沟",
        arrivalKg=Decimal("1860.00"),
        receivedAt=now - timezone.timedelta(days=2),
    )
    lot_b = ResinLot.objects.create(
        lotCode="脂-桐油坑-2409B",
        originPlace="桐油坑北坡",
        arrivalKg=Decimal("1420.50"),
        receivedAt=now - timezone.timedelta(days=1, hours=6),
    )
    lot_c = ResinLot.objects.create(
        lotCode="脂-松脂坳-2409C",
        originPlace="松脂坳西岔",
        arrivalKg=Decimal("980.00"),
        receivedAt=now - timezone.timedelta(hours=10),
    )

    h1 = FireHearth.objects.create(
        lane=1,
        tag="坳火-甲",
        resinGrade="特级脂",
        phase=FireHearth.PHASE_HOLDING,
        openWindowStart=time(5, 0),
        openWindowEnd=time(22, 0),
    )
    h2 = FireHearth.objects.create(
        lane=1,
        tag="坳火-乙",
        resinGrade="一级脂",
        phase=FireHearth.PHASE_RAMPING,
        openWindowStart=time(6, 0),
        openWindowEnd=time(20, 0),
    )
    # 夜班灶：允许窗跨午夜 22:00–次日 05:00。
    h3 = FireHearth.objects.create(
        lane=2,
        tag="坑火-西一",
        resinGrade="特级脂",
        phase=FireHearth.PHASE_DRAWING,
        openWindowStart=time(22, 0),
        openWindowEnd=time(5, 0),
    )
    FireHearth.objects.create(
        lane=2,
        tag="坑火-西二",
        resinGrade="二级脂",
        phase=FireHearth.PHASE_COLD,
        openWindowStart=time(5, 0),
        openWindowEnd=time(22, 0),
    )
    h5 = FireHearth.objects.create(
        lane=3,
        tag="坳火-夜班",
        resinGrade="浮油级",
        phase=FireHearth.PHASE_CHARGING,
        openWindowStart=time(5, 0),
        openWindowEnd=time(22, 0),
    )

    # h1：一条已收灶历史 + 当前未收灶（未收灶为最晚班次，顺序对齐）。
    CookRun.objects.create(
        hearth=h1,
        resinLot=lot_a,
        openedAt=at(-2, 9, 0),
        closedAt=at(-2, 17, 0),
        targetSoftPointC=Decimal("88.00"),
    )
    run1 = CookRun.objects.create(
        hearth=h1,
        resinLot=lot_a,
        openedAt=at(-1, 9, 30),
        closedAt=None,
        targetSoftPointC=Decimal("88.00"),
    )
    SoftPointProbe.objects.create(
        run=run1,
        sampledAt=at(-1, 12, 0),
        softPointC=Decimal("102.40"),
        samplerName="值守周磊",
    )
    SoftPointProbe.objects.create(
        run=run1,
        sampledAt=at(-1, 14, 0),
        softPointC=Decimal("96.20"),
        samplerName="值守周磊",
    )

    run2 = CookRun.objects.create(
        hearth=h2,
        resinLot=lot_c,
        openedAt=at(-1, 11, 0),
        closedAt=None,
        targetSoftPointC=Decimal("90.00"),
    )
    SoftPointProbe.objects.create(
        run=run2,
        sampledAt=at(-1, 13, 0),
        softPointC=Decimal("108.00"),
        samplerName="值守阿坤",
    )

    # h3 一灶多值守：两条已收灶历史在先，未收灶值守开灶最晚，
    # 全部落在跨午夜允许窗 22:00–05:00 内；按 openedAt 升序复算即对齐。
    CookRun.objects.create(
        hearth=h3,
        resinLot=lot_b,
        openedAt=at(-2, 22, 30),
        closedAt=at(-1, 2, 0),
        targetSoftPointC=Decimal("86.00"),
    )
    CookRun.objects.create(
        hearth=h3,
        resinLot=lot_b,
        openedAt=at(-1, 2, 30),
        closedAt=at(-1, 4, 30),
        targetSoftPointC=Decimal("86.00"),
    )
    run3 = CookRun.objects.create(
        hearth=h3,
        resinLot=lot_b,
        openedAt=at(-1, 23, 0),
        closedAt=None,
        targetSoftPointC=Decimal("86.00"),
    )
    SoftPointProbe.objects.create(
        run=run3,
        sampledAt=at(-1, 23, 30),
        softPointC=Decimal("99.10"),
        samplerName="值守阿萍",
    )
    SoftPointProbe.objects.create(
        run=run3,
        sampledAt=at(0, 2, 0),
        softPointC=Decimal("93.50"),
        samplerName="值守阿萍",
    )

    CookRun.objects.create(
        hearth=h5,
        resinLot=lot_a,
        openedAt=at(-1, 16, 0),
        closedAt=None,
        targetSoftPointC=Decimal("87.00"),
    )
