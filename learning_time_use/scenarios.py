"""受约束的活动替代情景与共同支持（common support）评估。

情景规则（硬约束，违反即不可行）：
  * 只能从指定来源活动转出时间，受保护活动（默认照护、其他，即不可自行
    再安排的责任与刚性时间）不参与转出；
  * 任何活动时间不得为负，转出量不得超过该日来源活动余额；
  * 一天总时长不变：转出多少分钟，就转入学习多少分钟；
  * 工作日与周末分别评估，不跨口径借时间；
  * 部分日、缺失日不参与情景，避免把漏记时间当成可支配时间。

共同支持规则（防止对未见过的时间组合做精确承诺）：
  情景后某人的活动向量，必须落在同口径下实际观察到的完整日向量邻域内；
  邻域按“学习暴露量 + 来源活动余额”两维匹配。支持不足时只给方向性结果，
  不给精确改善承诺。选择诊断有红旗时，整个口径下的情景降级处理。
"""
from __future__ import annotations

from dataclasses import dataclass

from .accounts import ACTIVITIES

PROTECTED_DEFAULT = ("照护", "其他")
TRANSFER_TARGET = "学习"
MIN_SUPPORT_DAYS = 3
# 共同支持邻域：情景后向量与真实完整日在“学习量/来源活动余额”两维上的
# 最大可接受距离。容差保持在 1-2 小时以内，防止把完全不同的生活安排算作邻居。
LEARNING_TOLERANCE_MINUTES = 60
SOURCE_TOLERANCE_MINUTES = 120

# 推荐状态从强到弱排序，取最高可达等级。
STATUS_ORDER = ("可推荐", "谨慎参考", "证据不足", "不可行", "不适用")


@dataclass(frozen=True)
class Scenario:
    name: str
    stratum: str
    source_activity: str
    transfer_minutes: int
    protected_activities: tuple = PROTECTED_DEFAULT
    min_support_days: int = MIN_SUPPORT_DAYS


def validate_scenario(scenario):
    if scenario.source_activity not in ACTIVITIES:
        raise ValueError(f"来源活动未登记: {scenario.source_activity}")
    if scenario.source_activity == TRANSFER_TARGET:
        raise ValueError("学习是转入活动，不能同时作为来源")
    if scenario.source_activity in scenario.protected_activities:
        raise ValueError(f"来源活动 {scenario.source_activity} 属于受保护活动，不可转出")
    if scenario.transfer_minutes <= 0:
        raise ValueError("转出时间必须为正数")
    unknown_protected = set(scenario.protected_activities) - set(ACTIVITIES)
    if unknown_protected:
        raise ValueError(f"受保护活动未登记: {sorted(unknown_protected)}")


def apply_to_vector(vector, scenario):
    """对单个完整日的主活动向量施加情景，返回新向量与可行性说明。"""
    source = scenario.source_activity
    available = vector[source]
    if available < scenario.transfer_minutes:
        return None, (
            f"来源活动 {source} 仅 {available:.0f} 分钟，"
            f"不足以转出 {scenario.transfer_minutes} 分钟")
    new_vector = dict(vector)
    new_vector[source] = available - scenario.transfer_minutes
    new_vector[TRANSFER_TARGET] = vector[TRANSFER_TARGET] + scenario.transfer_minutes
    if any(value < 0 for value in new_vector.values()):
        return None, "情景后出现负时间"
    if sum(new_vector.values()) != sum(vector.values()):
        return None, "情景改变了一天总时长"
    return new_vector, None


def _support_matches(observed_vectors, new_vector, source_activity, scenario):
    """统计与情景后向量相邻的实际观察日数（同口径完整日）。"""
    matches = []
    for day_id, observed in observed_vectors:
        learning_close = abs(
            observed[TRANSFER_TARGET] - new_vector[TRANSFER_TARGET]
        ) <= LEARNING_TOLERANCE_MINUTES
        source_close = abs(
            observed[source_activity] - new_vector[source_activity]
        ) <= SOURCE_TOLERANCE_MINUTES
        if learning_close and source_close:
            matches.append(day_id)
    return matches


def evaluate_scenario(scenario, accounts, association, diagnostics, cohort):
    """对队列内每个人逐完整日评估情景，并汇总支持度与预测区间。"""
    validate_scenario(scenario)
    if diagnostics["flags"]:
        selection_status = "谨慎参考"
        selection_reason = "；".join(diagnostics["flags"])
    else:
        selection_status = "可推荐"
        selection_reason = ""

    observed_vectors = []
    for account in accounts:
        if account.stratum == scenario.stratum and account.is_complete:
            observed_vectors.append((account.day_id, dict(account.primary_minutes)))

    per_person = []
    supported_people = 0
    feasible_people = 0
    for person_id in cohort["members"]:
        person_days = [
            account for account in accounts
            if account.person_id == person_id
            and account.stratum == scenario.stratum
            and account.is_complete
        ]
        if not person_days:
            per_person.append({"person_id": person_id, "status": "不适用",
                               "reason": "该口径无完整日记"})
            continue
        # 用该人最近一波（第 2 波）完整日作为情景基线。
        baseline_day = max(person_days, key=lambda a: a.wave)
        new_vector, infeasible_reason = apply_to_vector(
            baseline_day.primary_minutes, scenario)
        if new_vector is None:
            per_person.append({
                "person_id": person_id, "status": "不可行",
                "reason": infeasible_reason,
                "baseline_day_id": baseline_day.day_id,
            })
            continue
        feasible_people += 1
        matches = _support_matches(
            observed_vectors, new_vector, scenario.source_activity, scenario)
        in_support = len(matches) >= scenario.min_support_days
        if in_support:
            supported_people += 1
        per_person.append({
            "person_id": person_id,
            "status": "可评估" if in_support else "证据不足",
            "baseline_day_id": baseline_day.day_id,
            "baseline_vector": dict(baseline_day.primary_minutes),
            "counterfactual_vector": new_vector,
            "support_day_ids": matches,
            "support_count": len(matches),
            "reason": "" if in_support else (
                f"仅有 {len(matches)} 个相邻观察日，"
                f"低于阈值 {scenario.min_support_days}，不外推精确承诺"),
        })

    prediction = _prediction(scenario, association)
    overall = _overall_status(
        feasible_people, supported_people, len(cohort["members"]),
        selection_status, association, prediction)

    return {
        "scenario": scenario,
        "stratum": scenario.stratum,
        "selection_status": selection_status,
        "selection_reason": selection_reason,
        "overall_status": overall,
        "feasible_people": feasible_people,
        "supported_people": supported_people,
        "cohort_size": len(cohort["members"]),
        "prediction": prediction,
        "per_person": per_person,
        "evidence_note": association.get("evidence", "关联模型不可用"),
    }


def _prediction(scenario, association):
    """把替代关联系数换算为该情景的满意度变化区间（小时口径）。"""
    if association.get("status") != "已估计":
        return {"status": "无法预测", "reason": association.get("reason", "关联模型不可用")}
    coefficient = association["coefficients"].get(TRANSFER_TARGET)
    if coefficient is None:
        return {"status": "无法预测",
                "reason": f"模型省略了 {TRANSFER_TARGET} 系数（参照活动设置问题）"}
    hours = scenario.transfer_minutes / 60.0
    low, high = coefficient["ci_95"]
    return {
        "status": "区间估计",
        "delta_satisfaction_point": coefficient["beta_per_hour"] * hours,
        "delta_satisfaction_ci_95": (low * hours, high * hours),
        "interpretation": (
            f"在与情景后安排相邻的真实日记所覆盖的人群内，"
            f"相对继续把时间留给{association['reference_activity']}，"
            f"满意度变化的模型区间；属于观察关联，不是个体改善承诺"),
    }


def _overall_status(feasible, supported, cohort_size, selection_status,
                    association, prediction):
    if feasible == 0:
        return "不可行"
    if association.get("status") != "已估计" or prediction["status"] != "区间估计":
        return "证据不足"
    if selection_status == "谨慎参考":
        return "谨慎参考"
    if supported == 0:
        return "证据不足"
    if supported < cohort_size:
        return "谨慎参考"
    return "可推荐"
