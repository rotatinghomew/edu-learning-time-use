"""审计轨迹：情景用了哪些日子、舍弃了哪些不完整资料。

输出两份可复核清单：
  * 日记级清单：每个日记日的覆盖状态、主活动分钟、伴随暴露与未记录分钟；
  * 情景级清单：每个人在每个情景下的基线日、可行性、共同支持匹配日。

原始时间账按原记录保留，不做任何补齐或缩放。
"""
from __future__ import annotations

import csv
from pathlib import Path

from .accounts import ACTIVITIES, raw_time_ledger


def export_time_ledger(accounts, path):
    ledger = raw_time_ledger(accounts)
    if not ledger:
        return 0
    fieldnames = list(ledger[0].keys())
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(ledger)
    return len(ledger)


def export_scenario_audit(evaluations, path):
    rows = []
    for evaluation in evaluations:
        scenario = evaluation["scenario"]
        for item in evaluation["per_person"]:
            row = {
                "scenario": scenario.name,
                "stratum": scenario.stratum,
                "source_activity": scenario.source_activity,
                "transfer_minutes": scenario.transfer_minutes,
                "person_id": item["person_id"],
                "status": item["status"],
                "reason": item.get("reason", ""),
                "baseline_day_id": item.get("baseline_day_id", ""),
                "support_count": item.get("support_count", ""),
                "support_day_ids": "|".join(item.get("support_day_ids", [])),
            }
            for activity in ACTIVITIES:
                baseline = item.get("baseline_vector", {}).get(activity, "")
                counterfactual = item.get("counterfactual_vector", {}).get(activity, "")
                row[f"baseline_{activity}"] = baseline
                row[f"scenario_{activity}"] = counterfactual
            rows.append(row)
    if not rows:
        return 0
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def explain_excluded_days(accounts):
    """给出被排除在替代分析之外的日记日及原因，供报告直接引用。"""
    excluded = []
    for account in accounts:
        if account.is_complete:
            continue
        if account.covered_minutes == 0:
            reason = "整日未回收，无法知道时间安排"
        else:
            reason = (
                f"仅覆盖 {account.covered_minutes}/"
                f"{account.expected_minutes} 分钟，未覆盖窗口不补齐")
        excluded.append({
            "day_id": account.day_id,
            "person_id": account.person_id,
            "wave": account.wave,
            "stratum": account.stratum,
            "reason": reason or account.missing_reason,
        })
    return excluded
