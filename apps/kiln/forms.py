from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import CookRun, FireHearth, ResinLot, SoftPointProbe
from .services.floor_rules import (
    assert_can_change_opened_at,
    assert_can_open_run,
    assert_can_enter_drawing,
)

_DATETIME_FORMATS = [
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
]


def _apply_validation_error(form, exc):
    """把服务层 ValidationError（字段字典或整体消息）灌回表单。"""
    message_dict = getattr(exc, "message_dict", None)
    if message_dict:
        for field, msgs in message_dict.items():
            form.add_error(field, msgs)
    else:
        form.add_error(None, exc.messages)


class ResinLotForm(forms.ModelForm):
    class Meta:
        model = ResinLot
        fields = ["lotCode", "originPlace", "arrivalKg", "receivedAt"]
        widgets = {
            "lotCode": forms.TextInput(attrs={"class": "field"}),
            "originPlace": forms.TextInput(attrs={"class": "field"}),
            "arrivalKg": forms.NumberInput(attrs={"class": "field", "step": "0.01"}),
            "receivedAt": forms.DateTimeInput(
                attrs={"class": "field", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["receivedAt"].input_formats = _DATETIME_FORMATS
        if self.instance and self.instance.pk and self.instance.receivedAt:
            local = timezone.localtime(self.instance.receivedAt)
            self.initial["receivedAt"] = local.strftime("%Y-%m-%dT%H:%M")


class PhaseChangeForm(forms.Form):
    phase = forms.ChoiceField(
        label="相位",
        choices=FireHearth.PHASE_CHOICES,
        widget=forms.Select(attrs={"class": "field"}),
    )

    def __init__(self, *args, hearth=None, **kwargs):
        self.hearth = hearth
        super().__init__(*args, **kwargs)
        if hearth is not None and not self.is_bound:
            self.fields["phase"].initial = hearth.phase

    def clean_phase(self):
        phase = self.cleaned_data["phase"]
        if self.hearth is not None and phase == FireHearth.PHASE_DRAWING:
            assert_can_enter_drawing(self.hearth)
        return phase


class SoftPointProbeForm(forms.ModelForm):
    class Meta:
        model = SoftPointProbe
        fields = ["sampledAt", "softPointC", "samplerName"]
        widgets = {
            "sampledAt": forms.DateTimeInput(
                attrs={"class": "field", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "softPointC": forms.NumberInput(attrs={"class": "field", "step": "0.01"}),
            "samplerName": forms.TextInput(attrs={"class": "field"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sampledAt"].input_formats = _DATETIME_FORMATS
        if not self.is_bound and not (self.instance and self.instance.pk):
            self.initial["sampledAt"] = timezone.localtime().strftime("%Y-%m-%dT%H:%M")


class _OpenedAtDateTimeInput(forms.DateTimeInput):
    def __init__(self, **kwargs):
        kwargs.setdefault(
            "attrs", {"class": "field", "type": "datetime-local"}
        )
        kwargs.setdefault("format", "%Y-%m-%dT%H:%M")
        super().__init__(**kwargs)


class OpenCookRunForm(forms.ModelForm):
    class Meta:
        model = CookRun
        fields = ["resinLot", "openedAt", "targetSoftPointC"]
        widgets = {
            "resinLot": forms.Select(attrs={"class": "field"}),
            "openedAt": _OpenedAtDateTimeInput(),
            "targetSoftPointC": forms.NumberInput(
                attrs={"class": "field", "step": "0.01"}
            ),
        }

    def __init__(self, *args, hearth=None, **kwargs):
        self.hearth = hearth
        super().__init__(*args, **kwargs)
        self.fields["openedAt"].input_formats = _DATETIME_FORMATS
        self.fields["resinLot"].queryset = ResinLot.objects.all()
        if not self.is_bound:
            self.initial["openedAt"] = timezone.localtime().strftime("%Y-%m-%dT%H:%M")

    def clean(self):
        cleaned = super().clean()
        opened_at = cleaned.get("openedAt")
        if self.hearth is not None and opened_at is not None:
            try:
                # 相位联锁 + 允许窗 + 同灶乱序（同灶可有在值值守，不再一律互斥）。
                assert_can_open_run(self.hearth, opened_at)
            except ValidationError as exc:
                _apply_validation_error(self, exc)
        return cleaned


class CookRunUpdateForm(forms.ModelForm):
    """更新路径：只改开灶时刻，受与新建相同的允许窗 / 乱序约束。"""

    class Meta:
        model = CookRun
        fields = ["openedAt"]
        widgets = {"openedAt": _OpenedAtDateTimeInput()}

    def __init__(self, *args, run=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.run = run if run is not None else self.instance
        self.fields["openedAt"].input_formats = _DATETIME_FORMATS
        if not self.is_bound and self.instance and self.instance.pk and self.instance.openedAt:
            local = timezone.localtime(self.instance.openedAt)
            self.initial["openedAt"] = local.strftime("%Y-%m-%dT%H:%M")

    def clean(self):
        cleaned = super().clean()
        opened_at = cleaned.get("openedAt")
        if self.run is not None and opened_at is not None:
            try:
                assert_can_change_opened_at(self.run, opened_at)
            except ValidationError as exc:
                _apply_validation_error(self, exc)
        return cleaned
