import datetime

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kiln", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="firehearth",
            name="openWindowStart",
            field=models.TimeField(
                default=datetime.time(5, 0), verbose_name="允许开灶起"
            ),
        ),
        migrations.AddField(
            model_name="firehearth",
            name="openWindowEnd",
            field=models.TimeField(
                default=datetime.time(22, 0), verbose_name="允许开灶止"
            ),
        ),
    ]
