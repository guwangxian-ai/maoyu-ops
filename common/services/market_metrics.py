"""Normalize evidence without turning absent/rounded counters into exact zeros."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re


def exact_count(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not re.fullmatch(r"\d{1,12}", text):
        return None
    return int(text)


def price_number(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().removeprefix("¥").removeprefix("￥").strip()
    if not re.fullmatch(r"\d{1,10}(?:\.\d{1,2})?", text):
        return None
    try:
        number = Decimal(text)
        return number if number < Decimal("10000000000") else None
    except InvalidOperation:
        return None


def want_from_tags(tags):
    for tag in tags:
        match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)(万|千)?(\+)?\s*人?想要", str(tag))
        if match:
            count = Decimal(match[1]) * {None: 1, "万": 10000, "千": 1000}[match[2]]
            if count >= 10**12 or count != int(count):
                continue
            return int(count), bool(match[2] or match[3] or "." in match[1])
    return None, False


def detail_metrics(result):
    """Only explicit itemDO counters; never recursive lookups into seller totals."""
    item = (result.get("detail") or {}).get("itemDO") or {}
    want = exact_count(item.get("wantCnt"))
    browse = exact_count(item.get("browseCnt"))
    fields = {}
    if want is not None:
        fields.update(want_count=want, want_source="detail.wantCnt", want_approximate=False)
    if browse is not None:
        fields.update(browse_count=browse, browse_source="detail.browseCnt")
    return fields


def metric_change(first, last, metric, source_field, *, sample_count, mixed_sources=False):
    """Endpoint net change only; never sales, a predicted rate, or a recommendation."""
    result = {"value": None, "reason": None}
    if sample_count < 2 or first["observed_at"] == last["observed_at"]:
        result["reason"] = "need_two_samples"
    elif first.get(metric) is None or last.get(metric) is None:
        result["reason"] = "missing_value"
    elif not first.get(source_field) or first.get(source_field) != last.get(source_field) or mixed_sources:
        result["reason"] = "source_changed"
    elif metric == "want_count" and (first.get("want_approximate") or last.get("want_approximate")):
        result["reason"] = "rounded_counter"
    elif metric != "price" and last[metric] < first[metric]:
        result["reason"] = "counter_decreased"
    else:
        value = last[metric] - first[metric]
        result["value"] = round(value, 2) if metric == "price" else value
    return result
