"""队列链接、资格规则与选择性缺失诊断。

哪些人的时间构成与满意度随访可以共同支持结论？必须同时满足：
  1. 两波在同一口径（工作日或周末）下都有完整日记日；
  2. 两波都有满意度随访，可以计算个体内变化；
  3. 两波的主活动总量一致（都是完整的一天），替代关系才闭合。

不满足条件的人不进入模型，但会出现在选择诊断里：当满意度变化、日记缺失
与活动构成相互关联时，诊断结果会直接影响情景推荐门槛（见 scenarios 模块），
而不是只写一句免责声明。
"""
from __future__ import annotations

from .accounts import ACTIVITIES


def satisfaction_by_person_wave(followups):
    table = {}
    for row in followups:
        table[(row["person_id"], row["wave"])] = row["satisfaction"]
    return table


def _standardized_mean_diff(group_a, group_b, values):
    """两组在给定均值向量上的标准化均值差（SMD），零方差时返回 None。"""
    if not group_a or not group_b:
        return None
    import math
    diffs = {}
    for key in values:
        a = [values[key][pid] for pid in group_a if pid in values[key]]
        b = [values[key][pid] for pid in group_b if pid in values[key]]
        if not a or not b:
            continue
        mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
        var_a = sum((x - mean_a) ** 2 for x in a) / max(1, len(a) - 1)
        var_b = sum((x - mean_b) ** 2 for x in b) / max(1, len(b) - 1)
        pooled = (var_a + var_b) / 2
        if pooled <= 0:
            continue
        diffs[key] = (mean_a - mean_b) / math.sqrt(pooled)
    return diffs


def build_model_cohort(accounts, followups, stratum, waves=(1, 2)):
    """构造可支持替代关联结论的个体内变化队列。

    返回 dict：
      members            合格人员名单
      compositions       person_id -> {wave: {activity: 分钟}}（仅完整日均值）
      satisfaction_change person_id -> 第 2 波减第 1 波满意度
      excluded           不合格人员及原因（供审计与选择诊断）
    """
    satisfaction = satisfaction_by_person_wave(followups)
    wave_first, wave_second = min(waves), max(waves)

    first_vectors, first_counts, first_days = _collect(accounts, stratum, wave_first)
    second_vectors, second_counts, second_days = _collect(accounts, stratum, wave_second)
    all_people = sorted({a.person_id for a in accounts})

    members, excluded = [], {}
    compositions = {}
    satisfaction_change = {}
    for person_id in all_people:
        reasons = []
        if person_id not in first_vectors:
            reasons.append("第1波无完整日记")
        if person_id not in second_vectors:
            reasons.append("第2波无完整日记")
        s1 = satisfaction.get((person_id, wave_first))
        s2 = satisfaction.get((person_id, wave_second))
        if s1 is None:
            reasons.append("第1波满意度缺失")
        if s2 is None:
            reasons.append("第2波满意度缺失")
        if reasons:
            excluded[person_id] = reasons
            continue
        members.append(person_id)
        compositions[person_id] = {
            wave_first: first_vectors[person_id],
            wave_second: second_vectors[person_id],
        }
        satisfaction_change[person_id] = s2 - s1

    return {
        "stratum": stratum,
        "waves": waves,
        "members": members,
        "compositions": compositions,
        "satisfaction_change": satisfaction_change,
        "excluded": excluded,
        "day_counts": {wave_first: first_counts, wave_second: second_counts},
        "day_ids": {wave_first: first_days, wave_second: second_days},
    }


def _collect(accounts, stratum, wave):
    from .accounts import person_day_vectors
    return person_day_vectors(accounts, stratum, wave)


def selection_diagnostics(accounts, followups, cohort):
    """量化“谁被留下、谁被排除”以及缺失与结果、构成的关联。

    三类信号：
      A. 排除组 vs 纳入组在基线满意度与基线活动份额上的 SMD；
      B. 个体覆盖完整度（两波平均覆盖率）与满意度变化的相关；
      C. 基线满意度与“第2波日记是否完整”的关联（流失/缺失的选择性）。
    """
    satisfaction = satisfaction_by_person_wave(followups)
    members = cohort["members"]
    excluded = sorted(cohort["excluded"])
    stratum = cohort["stratum"]

    baseline_share = {}
    baseline_satisfaction = {}
    for account in accounts:
        if account.wave != 1 or account.stratum != stratum or not account.is_complete:
            continue
        total = account.primary_total() or 1
        baseline_share[account.person_id] = {
            activity: account.primary_minutes[activity] / total
            for activity in ACTIVITIES
        }
    for person_id in {a.person_id for a in accounts}:
        value = satisfaction.get((person_id, 1))
        if value is not None:
            baseline_satisfaction[person_id] = value

    share_values = {
        f"share_{activity}": {pid: baseline_share[pid][activity]
                              for pid in baseline_share}
        for activity in ACTIVITIES
    }
    smd_inputs = {"baseline_satisfaction": baseline_satisfaction, **share_values}
    smds = _standardized_mean_diff(members, excluded, smd_inputs) or {}

    # 信号 B：覆盖率与满意度变化的相关。
    # 必须在“有两波随访”的全体人员上计算——只看队列成员会因覆盖率恒为 1
    # 而失去方差，从而掩盖部分日持有者（如 P10）携带的选择信号。
    coverage_by_person = {}
    for person_id in {a.person_id for a in accounts}:
        ratios = [a.coverage_ratio for a in accounts
                  if a.person_id == person_id and a.stratum == stratum]
        if ratios:
            coverage_by_person[person_id] = sum(ratios) / len(ratios)
    all_changes = {}
    for person_id in coverage_by_person:
        s1 = satisfaction.get((person_id, 1))
        s2 = satisfaction.get((person_id, 2))
        if s1 is not None and s2 is not None:
            all_changes[person_id] = s2 - s1
    paired = [(coverage_by_person[pid], change)
              for pid, change in all_changes.items()]
    coverage_corr = _pearson([x for x, _ in paired], [y for _, y in paired])

    # 信号 C：第 2 波完整日记者 vs 不完整者的基线满意度差。
    wave2_complete = {
        a.person_id for a in accounts
        if a.wave == 2 and a.stratum == stratum and a.is_complete
    }
    complete_sat = [baseline_satisfaction[p] for p in wave2_complete
                    if p in baseline_satisfaction]
    incomplete_people = [
        pid for pid in baseline_satisfaction if pid not in wave2_complete]
    incomplete_sat = [baseline_satisfaction[p] for p in incomplete_people]
    wave2_gap = (
        sum(complete_sat) / len(complete_sat)
        - sum(incomplete_sat) / len(incomplete_sat)
        if complete_sat and incomplete_sat else None
    )

    max_abs_smd = max((abs(v) for v in smds.values()), default=0.0)
    flags = []
    if max_abs_smd > 0.2:
        flags.append("纳入与排除人群在基线特征上的标准化差异超过0.2")
    if coverage_corr is not None and abs(coverage_corr) > 0.3:
        flags.append("日记覆盖完整度与满意度变化相关系数绝对值超过0.3")
    if wave2_gap is not None and abs(wave2_gap) > 0.5:
        flags.append("第2波日记缺失者的基线满意度与保留者相差超过0.5分")

    return {
        "stratum": stratum,
        "included": members,
        "excluded": excluded,
        "exclusion_reasons": cohort["excluded"],
        "smd": smds,
        "max_abs_smd": max_abs_smd,
        "coverage_satisfaction_corr": coverage_corr,
        "wave2_missing_baseline_gap": wave2_gap,
        "flags": flags,
    }


def _pearson(xs, ys):
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / (var_x * var_y) ** 0.5
