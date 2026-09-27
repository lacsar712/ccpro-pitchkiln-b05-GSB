import datetime

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kiln", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="firehearth",
            name="cookWindowStart",
            field=models.TimeField(
                default=datetime.time(6, 0), verbose_name="允许开窗时刻"
            ),
        ),
        migrations.AddField(
            model_name="firehearth",
            name="cookWindowEnd",
            field=models.TimeField(
                default=datetime.time(22, 0), verbose_name="允许关窗时刻"
            ),
        ),
    ]
