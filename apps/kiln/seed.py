import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import CookRun, FireHearth, ResinLot, SoftPointProbe


def ensure_seed_data():
    """幂等种子：账号 + 来脂批 / 灶台（含灶允许窗）/ 多值守 / 探针。"""
    User = get_user_model()

    if not User.objects.filter(username="admin").exists():
        User.objects.create_superuser("admin", "admin@pitchkiln.local", "123456")

    if not User.objects.filter(username="worker").exists():
        User.objects.create_user("worker", "worker@pitchkiln.local", "123456")

    if FireHearth.objects.exists():
        return

    now = timezone.localtime()
    today = now.replace(minute=0, second=0, microsecond=0)
    yesterday = today - datetime.timedelta(days=1)

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

    def at(day, hour, minute=0):
        return day.replace(hour=hour, minute=minute)

    h1 = FireHearth.objects.create(
        lane=1,
        tag="坳火-甲",
        resinGrade="特级脂",
        phase=FireHearth.PHASE_HOLDING,
        cookWindowStart=datetime.time(6, 0),
        cookWindowEnd=datetime.time(22, 0),
    )
    h2 = FireHearth.objects.create(
        lane=1,
        tag="坳火-乙",
        resinGrade="一级脂",
        phase=FireHearth.PHASE_RAMPING,
        cookWindowStart=datetime.time(6, 0),
        cookWindowEnd=datetime.time(20, 0),
    )
    # 出胶灶：跨午夜允许窗 18:00–06:00，且一灶挂两个未收灶值守
    h3 = FireHearth.objects.create(
        lane=2,
        tag="坑火-西一",
        resinGrade="特级脂",
        phase=FireHearth.PHASE_DRAWING,
        cookWindowStart=datetime.time(18, 0),
        cookWindowEnd=datetime.time(6, 0),
    )
    FireHearth.objects.create(
        lane=2,
        tag="坑火-西二",
        resinGrade="二级脂",
        phase=FireHearth.PHASE_COLD,
        cookWindowStart=datetime.time(8, 0),
        cookWindowEnd=datetime.time(18, 0),
    )
    h5 = FireHearth.objects.create(
        lane=3,
        tag="坳火-夜班",
        resinGrade="浮油级",
        phase=FireHearth.PHASE_CHARGING,
        cookWindowStart=datetime.time(5, 0),
        cookWindowEnd=datetime.time(14, 0),
    )

    def make_run(hearth, lot, opened_at, target):
        """建值守并跑 full_clean，证明种子自身满足窗 / 相位 / 顺序联锁。"""
        run = CookRun(
            hearth=hearth,
            resinLot=lot,
            openedAt=opened_at,
            closedAt=None,
            targetSoftPointC=Decimal(target),
        )
        run.full_clean()
        run.save()
        return run

    run1 = make_run(h1, lot_a, at(yesterday, 9), "88.00")
    SoftPointProbe.objects.create(
        run=run1,
        sampledAt=at(yesterday, 14),
        softPointC=Decimal("102.40"),
        samplerName="值守周磊",
    )
    SoftPointProbe.objects.create(
        run=run1,
        sampledAt=at(yesterday, 17),
        softPointC=Decimal("96.20"),
        samplerName="值守周磊",
    )

    run2 = make_run(h2, lot_c, at(yesterday, 13), "90.00")
    SoftPointProbe.objects.create(
        run=run2,
        sampledAt=at(yesterday, 16, 20),
        softPointC=Decimal("108.00"),
        samplerName="值守阿坤",
    )

    # 一灶多值守：先建开灶更晚的「最新值守」，再插更早的值守，
    # 二者开灶钟点都落在跨午夜窗 18:00–06:00 内。
    run3_latest = make_run(h3, lot_b, at(today, 2), "86.00")
    run3_earlier = make_run(h3, lot_b, at(yesterday, 20), "89.00")
    SoftPointProbe.objects.create(
        run=run3_earlier,
        sampledAt=at(yesterday, 22),
        softPointC=Decimal("99.10"),
        samplerName="值守阿萍",
    )
    # 最新值守须有 ≤95℃ 探针，出胶相位才站得住
    SoftPointProbe.objects.create(
        run=run3_latest,
        sampledAt=at(today, 3),
        softPointC=Decimal("96.20"),
        samplerName="值守阿萍",
    )
    SoftPointProbe.objects.create(
        run=run3_latest,
        sampledAt=at(today, 5),
        softPointC=Decimal("93.50"),
        samplerName="值守阿萍",
    )

    make_run(h5, lot_a, at(yesterday, 7, 30), "87.00")
