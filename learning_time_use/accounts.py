"""日记覆盖核算与原始时间账。

本模块坚持三条口径：
  1. 只在“完整覆盖一天”的日记上做日内时间替代；部分日与缺失日保留在审计中，
     绝不按回收比例放大或补齐后当作真实安排；
  2. 主活动分钟是互斥的时间账，伴随活动分钟是可与主活动重叠的暴露账，
     两者分别汇总，不合并、不比较总量；
  3. 确实为 0 分钟（活动出现在 zero_activities 或有显式 0 记录）与未记录
     （部分日未覆盖窗口、伴随活动留空）分开表示。

工作日（weekday）与周末（weekend）全程分开核算，避免回收比例不同造成的伪差异。
"""
from __future__ import annotations

from dataclasses import dataclass, field

STRATA = ("weekday", "weekend")
ACTIVITIES = ("学习", "照护", "家务", "休闲", "其他")


@dataclass
class DayAccount:
    """单个日记日的时间账，数值单位均为分钟。"""

    day_id: str
    person_id: str
    wave: int
    stratum: str
    covered_minutes: int
    expected_minutes: int
    is_complete: bool
    missing_reason: str
    primary_minutes: dict            # 活动 -> 已记录主活动分钟；未覆盖部分不补
    secondary_exposure: dict         # 活动 -> 伴随暴露分钟；仅统计有记录的时段
    secondary_unrecorded_minutes: int  # 伴随活动留空的主活动分钟（未记录，不是 0）
    zero_activities: list            # 当日确实没做的活动（显式零分钟）
    entry_ids: list = field(default_factory=list)

    @property
    def coverage_ratio(self):
        return self.covered_minutes / self.expected_minutes if self.expected_minutes else None

    def primary_total(self):
        return sum(self.primary_minutes.values())


def build_day_accounts(days, entries):
    """把日记日元数据与时段数据合并为逐日时间账。

    完整日要求 covered_minutes == expected_minutes，且主活动分钟合计恰好等于
    覆盖分钟数；任何对不上的情况直接报错，防止静默错账。
    """
    entries_by_day: dict[str, list[dict]] = {}
    for entry in entries:
        entries_by_day.setdefault(entry["day_id"], []).append(entry)

    accounts = []
    for day in days:
        primary = {activity: 0 for activity in ACTIVITIES}
        secondary = {activity: 0 for activity in ACTIVITIES}
        unrecorded_secondary = 0
        entry_ids = []
        for entry in entries_by_day.get(day["day_id"], []):
            activity = entry["primary_activity"]
            if activity not in primary:
                raise ValueError(
                    f"日记日 {day['day_id']} 出现未登记活动: {activity}")
            primary[activity] += entry["duration_minutes"]
            entry_ids.append(entry["entry_id"])
            if entry["secondary_minutes"] is None:
                unrecorded_secondary += entry["duration_minutes"]
            elif entry["secondary_minutes"] > 0:
                companion = entry["secondary_activity"]
                if companion not in secondary:
                    raise ValueError(
                        f"日记日 {day['day_id']} 出现未登记伴随活动: {companion}")
                secondary[companion] += entry["secondary_minutes"]

        is_complete = (
            day["covered_minutes"] == day["expected_minutes"]
            and day["covered_minutes"] > 0
        )
        account = DayAccount(
            day_id=day["day_id"],
            person_id=day["person_id"],
            wave=day["wave"],
            stratum=day["stratum"],
            covered_minutes=day["covered_minutes"],
            expected_minutes=day["expected_minutes"],
            is_complete=is_complete,
            missing_reason=day["missing_reason"],
            primary_minutes=primary,
            secondary_exposure=secondary,
            secondary_unrecorded_minutes=unrecorded_secondary,
            zero_activities=list(day["zero_activities"]),
            entry_ids=entry_ids,
        )
        if is_complete and account.primary_total() != day["expected_minutes"]:
            raise ValueError(
                f"完整日记日 {day['day_id']} 主活动合计 "
                f"{account.primary_total()} != {day['expected_minutes']}，存在错账")
        if is_complete:
            actual_zeros = {activity for activity, minutes in primary.items()
                            if minutes == 0}
            declared_zeros = set(day["zero_activities"])
            if actual_zeros != declared_zeros:
                raise ValueError(
                    f"完整日记日 {day['day_id']} 的零分钟活动清单不一致："
                    f"实际 {sorted(actual_zeros)}，登记 {sorted(declared_zeros)}")
        if not is_complete and account.primary_total() > day["covered_minutes"]:
            raise ValueError(f"部分日记日 {day['day_id']} 主活动分钟超过覆盖窗口")
        accounts.append(account)
    return accounts


def coverage_audit(accounts):
    """按工作日/周末与波次汇总覆盖情况，供报告与情景推荐门槛使用。"""
    audit = {}
    for stratum in STRATA:
        for wave in sorted({account.wave for account in accounts}):
            members = [a for a in accounts
                       if a.stratum == stratum and a.wave == wave]
            complete = [a for a in members if a.is_complete]
            partial = [a for a in members if not a.is_complete and a.covered_minutes > 0]
            missing = [a for a in members if a.covered_minutes == 0]
            audit[(stratum, wave)] = {
                "total_days": len(members),
                "complete_days": len(complete),
                "partial_days": len(partial),
                "missing_days": len(missing),
                "mean_coverage_ratio": (
                    sum(a.coverage_ratio for a in members) / len(members)
                    if members else None),
                "partial_day_ids": [a.day_id for a in partial],
                "missing_day_ids": [a.day_id for a in missing],
            }
    return audit


def raw_time_ledger(accounts):
    """按原记录输出时间账（不补齐、不缩放），用于核对情景用了哪些日子。

    返回每个日记日一行的字典列表，主活动与伴随活动分列，
    并标注完整/部分状态与未记录分钟。
    """
    ledger = []
    for account in sorted(accounts, key=lambda a: (a.person_id, a.wave, a.stratum)):
        row = {
            "day_id": account.day_id,
            "person_id": account.person_id,
            "wave": account.wave,
            "stratum": account.stratum,
            "coverage": "完整" if account.is_complete else (
                "缺失" if account.covered_minutes == 0 else "部分"),
            "covered_minutes": account.covered_minutes,
            "expected_minutes": account.expected_minutes,
            "primary_total": account.primary_total(),
            "secondary_unrecorded_minutes": account.secondary_unrecorded_minutes,
            "missing_reason": account.missing_reason,
        }
        for activity in ACTIVITIES:
            row[f"primary_{activity}"] = account.primary_minutes[activity]
            row[f"secondary_{activity}"] = account.secondary_exposure[activity]
        ledger.append(row)
    return ledger


def person_day_vectors(accounts, stratum, wave):
    """取某人某波某口径下的完整日主活动向量 {person_id: {activity: minutes}}。

    一人一波一口径在本数据中只有一天；若有多天，取均值时只平均完整日，
    且保留天数信息让上层决定样本量是否足够。
    """
    vectors: dict[str, dict] = {}
    day_counts: dict[str, int] = {}
    day_ids: dict[str, list] = {}
    for account in accounts:
        if account.stratum != stratum or account.wave != wave or not account.is_complete:
            continue
        bucket = vectors.setdefault(account.person_id,
                                    {activity: 0 for activity in ACTIVITIES})
        for activity in ACTIVITIES:
            bucket[activity] += account.primary_minutes[activity]
        day_counts[account.person_id] = day_counts.get(account.person_id, 0) + 1
        day_ids.setdefault(account.person_id, []).append(account.day_id)
    averaged = {}
    for person_id, totals in vectors.items():
        n = day_counts[person_id]
        averaged[person_id] = {activity: totals[activity] / n for activity in ACTIVITIES}
    return averaged, day_counts, day_ids
