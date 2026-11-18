"""时间日记与随访记录的读取、字段校验。

口径约定（与情景分析的可信度直接相关）：
  * 主活动时间（primary_activity / duration_minutes）是互斥的分钟账；
  * 伴随活动（secondary_*）是暴露时长，空字符串表示“未记录”，
    与 secondary_minutes=0（确实没有伴随活动）严格区分；
  * 日记日的覆盖情况以 diary_days.csv 的 covered_minutes/expected_minutes 为准，
    部分日与整日缺失不允许在读取端被补齐；
  * 随访满意度为空表示该波缺失（如失访），不参与差值计算。
"""
from __future__ import annotations

import csv
from pathlib import Path

DIARY_REQUIRED = {
    "entry_id", "day_id", "person_id", "date", "wave", "stratum",
    "start_minute", "end_minute", "duration_minutes", "primary_activity",
    "secondary_activity", "secondary_minutes",
}
DAY_REQUIRED = {
    "day_id", "person_id", "date", "wave", "stratum",
    "covered_minutes", "expected_minutes", "missing_reason", "zero_activities",
}
FOLLOWUP_REQUIRED = {"person_id", "wave", "satisfaction", "note"}
PERSON_REQUIRED = {"person_id", "gender", "age", "status"}

VALID_STRATA = {"weekday", "weekend"}


def _read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    return rows


def _check_fields(rows, required, source):
    if not rows:
        raise ValueError(f"{source} 为空，无法分析")
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"{source} 缺少字段: {', '.join(sorted(missing))}")


def _to_int(value, field, source):
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source} 字段 {field} 不是整数: {value!r}") from exc


def load_diary_entries(path):
    """读取时段级日记，并校验主活动时段的互斥性与字段完整性。"""
    rows = _read_csv(path)
    _check_fields(rows, DIARY_REQUIRED, "diary_entries")
    seen_ids = set()
    parsed_entries = []
    by_day: dict[str, list[dict]] = {}
    for raw in rows:
        entry_id = raw["entry_id"]
        if entry_id in seen_ids:
            raise ValueError(f"时段编号重复: {entry_id}")
        seen_ids.add(entry_id)
        if raw["stratum"] not in VALID_STRATA:
            raise ValueError(f"时段 {entry_id} 的 stratum 非法: {raw['stratum']}")
        start = _to_int(raw["start_minute"], "start_minute", f"时段 {entry_id}")
        end = _to_int(raw["end_minute"], "end_minute", f"时段 {entry_id}")
        duration = _to_int(raw["duration_minutes"], "duration_minutes", f"时段 {entry_id}")
        if start < 0 or end <= start or duration != end - start:
            raise ValueError(f"时段 {entry_id} 的时间区间不一致: {start}-{end}, 时长 {duration}")
        if not raw["primary_activity"].strip():
            raise ValueError(f"时段 {entry_id} 缺少主活动")
        secondary_minutes_raw = raw["secondary_minutes"]
        if secondary_minutes_raw == "":
            secondary_minutes = None  # 未记录
        else:
            secondary_minutes = _to_int(
                secondary_minutes_raw, "secondary_minutes", f"时段 {entry_id}")
            if secondary_minutes < 0 or secondary_minutes > duration:
                raise ValueError(
                    f"时段 {entry_id} 伴随活动时长超出主活动时长")
        entry = {
            "entry_id": entry_id,
            "day_id": raw["day_id"],
            "person_id": raw["person_id"],
            "date": raw["date"],
            "wave": _to_int(raw["wave"], "wave", f"时段 {entry_id}"),
            "stratum": raw["stratum"],
            "start_minute": start,
            "end_minute": end,
            "duration_minutes": duration,
            "primary_activity": raw["primary_activity"].strip(),
            "secondary_activity": raw["secondary_activity"].strip(),
            "secondary_minutes": secondary_minutes,
        }
        by_day.setdefault(entry["day_id"], []).append(entry)
        parsed_entries.append(entry)

    for day_id, entries in by_day.items():
        ordered = sorted(entries, key=lambda item: item["start_minute"])
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier["end_minute"] > later["start_minute"]:
                raise ValueError(f"日记日 {day_id} 的主活动时段重叠")
    return parsed_entries


def load_diary_days(path):
    rows = _read_csv(path)
    _check_fields(rows, DAY_REQUIRED, "diary_days")
    days = []
    for raw in rows:
        covered = _to_int(raw["covered_minutes"], "covered_minutes", raw["day_id"])
        expected = _to_int(raw["expected_minutes"], "expected_minutes", raw["day_id"])
        if covered < 0 or expected <= 0 or covered > expected:
            raise ValueError(f"日记日 {raw['day_id']} 覆盖分钟数非法: {covered}/{expected}")
        if raw["stratum"] not in VALID_STRATA:
            raise ValueError(f"日记日 {raw['day_id']} 的 stratum 非法: {raw['stratum']}")
        days.append({
            "day_id": raw["day_id"],
            "person_id": raw["person_id"],
            "date": raw["date"],
            "wave": _to_int(raw["wave"], "wave", raw["day_id"]),
            "stratum": raw["stratum"],
            "covered_minutes": covered,
            "expected_minutes": expected,
            "missing_reason": raw["missing_reason"].strip(),
            "zero_activities": [item for item in raw["zero_activities"].split("|") if item],
        })
    return days


def load_followups(path):
    rows = _read_csv(path)
    _check_fields(rows, FOLLOWUP_REQUIRED, "followups")
    followups = []
    seen = set()
    for raw in rows:
        key = (raw["person_id"], _to_int(raw["wave"], "wave", raw["person_id"]))
        if key in seen:
            raise ValueError(f"随访记录重复: {key}")
        seen.add(key)
        if raw["satisfaction"] == "":
            satisfaction = None  # 该波缺失（如失访）
        else:
            satisfaction = float(raw["satisfaction"])
            if not 0.0 <= satisfaction <= 10.0:
                raise ValueError(f"随访 {key} 的满意度超出 0-10 范围: {satisfaction}")
        followups.append({
            "person_id": raw["person_id"],
            "wave": key[1],
            "satisfaction": satisfaction,
            "note": raw["note"].strip(),
        })
    return followups


def load_persons(path):
    rows = _read_csv(path)
    _check_fields(rows, PERSON_REQUIRED, "persons")
    return [{"person_id": raw["person_id"], "gender": raw["gender"],
             "age": _to_int(raw["age"], "age", raw["person_id"]),
             "status": raw["status"]} for raw in rows]
