"""reports/serializers.py

The frontend sometimes sends human-friendly reference strings like:
  - EN-000123
  - LAB-000456
  - IMG-000789
  - BIL-000321

The backend report generator, however, needs the underlying numeric ID.
So we accept a string `ref_id` and normalize to an int.
"""

from __future__ import annotations

import re
from datetime import datetime, time

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import serializers


class GenerateReportSerializer(serializers.Serializer):
    REPORT_TYPES = ("ENCOUNTER", "LAB", "IMAGING", "BILLING")

    report_type = serializers.ChoiceField(choices=REPORT_TYPES)
    # Accept strings like "EN-000123" and normalize to integer PK.
    ref_id = serializers.CharField()

    as_pdf = serializers.BooleanField(required=False, default=True)
    save_as_attachment = serializers.BooleanField(required=False, default=False)

    # Optional billing filters (frontend sends YYYY-MM-DD from <input type="date">)
    start = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    end = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def _parse_ref_id(self, raw: str) -> int:
        if raw is None:
            raise serializers.ValidationError("ref_id is required")
        s = str(raw).strip()
        # If it's already numeric, use it.
        if s.isdigit():
            return int(s)

        # Extract the first run of digits (supports EN-000123 style inputs).
        m = re.search(r"(\d+)", s)
        if not m:
            raise serializers.ValidationError("Invalid reference id")
        return int(m.group(1))

    def _parse_dateish(self, value: str | None, *, is_end: bool) -> datetime | None:
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None

        # Accept either full datetime or date.
        dt = parse_datetime(s)
        if dt is not None:
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            return dt

        d = parse_date(s)
        if d is None:
            raise serializers.ValidationError("Invalid date")

        t = time.max if is_end else time.min
        dt = datetime.combine(d, t)
        return timezone.make_aware(dt, timezone.get_current_timezone())

    def validate(self, attrs):
        attrs = super().validate(attrs)

        attrs["ref_id"] = self._parse_ref_id(attrs.get("ref_id"))

        # Normalize start/end to timezone-aware datetimes.
        start = self._parse_dateish(attrs.get("start"), is_end=False)
        end = self._parse_dateish(attrs.get("end"), is_end=True)

        # Only meaningful for BILLING but harmless to carry.
        attrs["start"] = start
        attrs["end"] = end
        return attrs
