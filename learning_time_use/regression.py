"""纯标准库实现的最小二乘回归与替代关联估计。

不引入第三方依赖。所有结果都标注为观察关联：个体内变化回归只能控制
不随时间变化的个体特征，无法排除同时改变活动安排与满意度的时变冲击，
不能解释为“增加学习必然带来满意度提升”的因果效果。
"""
from __future__ import annotations

import math

from .accounts import ACTIVITIES

# 常用自由度下 95% 双侧 t 临界值（自由度 -> 值），超出表尾范围用正态近似。
_T_CRIT = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    20: 2.086, 25: 2.060, 30: 2.042,
}


def t_critical(df, confidence=0.95):
    if confidence != 0.95:
        raise ValueError("当前仅提供 95% 临界值表")
    if df in _T_CRIT:
        return _T_CRIT[df]
    if df > 30:
        return 1.96
    # 小自由度未在表中时，向上取最近的更小自由度（更保守的区间）。
    nearest = max(key for key in _T_CRIT if key < df)
    return _T_CRIT[nearest]


def ols(matrix, targets):
    """带截距的 OLS。matrix 为解释变量行向量列表，targets 为同长度因变量。

    返回系数（截距在首位）、标准误、95% 置信区间与拟合诊断。
    设计矩阵奇异或样本量不足时抛出 ValueError，由上层降级为“无法估计”。
    """
    n = len(targets)
    if n != len(matrix) or n == 0:
        raise ValueError("回归样本为空或行列数不一致")
    x_rows = [[1.0, *row] for row in matrix]
    p = len(x_rows[0])
    if n <= p:
        raise ValueError(f"样本量 {n} 不大于待估参数数 {p}，无法估计")

    xtx = [[sum(x_rows[k][i] * x_rows[k][j] for k in range(n))
            for j in range(p)] for i in range(p)]
    xty = [sum(x_rows[k][i] * targets[k] for k in range(n)) for i in range(p)]
    inverse = _invert(xtx)
    beta = [sum(inverse[i][j] * xty[j] for j in range(p)) for i in range(p)]

    fitted = [sum(x_rows[k][j] * beta[j] for j in range(p)) for k in range(n)]
    residuals = [targets[k] - fitted[k] for k in range(n)]
    rss = sum(r * r for r in residuals)
    tss = _tss(targets)
    r_squared = 1 - rss / tss if tss > 0 else None
    df = n - p
    sigma2 = rss / df
    se = [math.sqrt(max(sigma2 * inverse[i][i], 0.0)) for i in range(p)]
    crit = t_critical(df)
    intervals = [(beta[i] - crit * se[i], beta[i] + crit * se[i]) for i in range(p)]
    return {
        "coefficients": beta,
        "standard_errors": se,
        "confidence_intervals_95": intervals,
        "residual_df": df,
        "n": n,
        "r_squared": r_squared,
    }


def _tss(values):
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values)


def _invert(matrix):
    """高斯-若尔当消元求逆；奇异矩阵抛出 ValueError。"""
    n = len(matrix)
    aug = [list(matrix[i]) + [1.0 if i == j else 0.0 for j in range(n)]
           for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(aug[row][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise ValueError("设计矩阵奇异，无法完成最小二乘估计")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pivot_value = aug[col][col]
        aug[col] = [value / pivot_value for value in aug[col]]
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            aug[row] = [aug[row][j] - factor * aug[col][j] for j in range(2 * n)]
    return [row[n:] for row in aug]


def estimate_substitution(cohort, reference_activity="休闲", min_sample=5):
    """估计活动时间变化与满意度变化的观察关联。

    模型（个体内两波差值，分钟换算为小时）：
        Δ满意度 = α + Σ β_k · Δ活动_k(小时) + ε，k 不含参照活动
    参照活动（默认休闲）的系数被省略：其余 β_k 表示“相对把时间留给休闲，
    每多 1 小时活动 k 的满意度差”。一天总时长固定，活动变化向量之和为 0，
    因此必须留一个参照，否则设计矩阵完全共线。
    """
    members = cohort["members"]
    if len(members) < min_sample:
        return {
            "status": "样本不足",
            "reason": f"合格人数 {len(members)} < 最低样本量 {min_sample}",
            "n": len(members),
            "coefficients": {},
        }

    predictors = [activity for activity in ACTIVITIES if activity != reference_activity]
    matrix, targets = [], []
    for person_id in members:
        first = cohort["compositions"][person_id][cohort["waves"][0]]
        second = cohort["compositions"][person_id][cohort["waves"][1]]
        row = [
            (second[activity] - first[activity]) / 60.0
            for activity in predictors
        ]
        matrix.append(row)
        targets.append(cohort["satisfaction_change"][person_id])

    dropped = [name for name, idx in zip(predictors, range(len(predictors)))
               if _column_variance(matrix, idx) <= 0]
    if dropped:
        keep = [idx for idx, name in enumerate(predictors) if name not in dropped]
        matrix = [[row[idx] for idx in keep] for row in matrix]
        predictors = [predictors[idx] for idx in keep]

    try:
        fit = ols(matrix, targets)
    except ValueError as exc:
        return {"status": "无法估计", "reason": str(exc), "n": len(members),
                "coefficients": {}}

    coefficients = {}
    for idx, activity in enumerate(predictors, start=1):
        coefficients[activity] = {
            "beta_per_hour": fit["coefficients"][idx],
            "se": fit["standard_errors"][idx],
            "ci_95": fit["confidence_intervals_95"][idx],
        }
    return {
        "status": "已估计",
        "evidence": "观察关联（个体内两波差值），非因果效果",
        "stratum": cohort["stratum"],
        "reference_activity": reference_activity,
        "n": fit["n"],
        "residual_df": fit["residual_df"],
        "r_squared": fit["r_squared"],
        "intercept": fit["coefficients"][0],
        "coefficients": coefficients,
        "dropped_predictors": dropped,
    }


def _column_variance(matrix, idx):
    column = [row[idx] for row in matrix]
    mean = sum(column) / len(column)
    return sum((value - mean) ** 2 for value in column)
