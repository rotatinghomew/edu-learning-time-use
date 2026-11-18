"""命令行报告：保留原有合成汇总，并追加活动替代情景比较。

运行：python3 -m learning_time_use.report
报告只打印到终端；需要落盘的审计清单由 pipeline 的 audit_dir 参数生成。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from .analysis import summarize
from .audit import explain_excluded_days
from .operations import review
from .pipeline import run_pipeline
from .records import read_records
from .storage import replace_records


def _print_legacy_summary(data):
    rows = read_records(data)
    with tempfile.TemporaryDirectory() as directory:
        stored = replace_records(Path(directory) / "observations.db", rows)
        rows = [{"period": period, "group": group, "count": count, "value": value}
                for period, group, count, value in stored]
    print("== 既有合成汇总（保留原口径） ==")
    for row in review(summarize(rows)):
        print(f"{row['period']} {row['group']}: {row['count']} 条，"
              f"均值 {row['mean']:.2f}，{row['review']}")
    print()


def _print_coverage(result):
    print("== 日记覆盖（不补齐、不缩放，工作日/周末分列） ==")
    for (stratum, wave), stats in sorted(result["coverage_audit"].items()):
        label = "工作日" if stratum == "weekday" else "周末"
        print(f"第{wave}波 {label}: 共 {stats['total_days']} 天，完整 "
              f"{stats['complete_days']}，部分 {stats['partial_days']}，"
              f"缺失 {stats['missing_days']}，平均覆盖率 "
              f"{stats['mean_coverage_ratio']:.1%}")
    print("被排除在日内替代分析之外的日记：")
    for item in explain_excluded_days(result["accounts"]):
        label = "工作日" if item["stratum"] == "weekday" else "周末"
        print(f"  - {item['day_id']}（第{item['wave']}波{label}）：{item['reason']}")
    print()


def _print_cohort_and_association(result):
    for stratum, label in (("weekday", "工作日"), ("weekend", "周末")):
        block = result["strata"][stratum]
        cohort = block["cohort"]
        diagnostics = block["diagnostics"]
        association = block["association"]
        print(f"== {label}队列与替代关联 ==")
        print(f"可共同支持结论的人员（{len(cohort['members'])} 人）: "
              f"{', '.join(cohort['members']) or '无'}")
        if cohort["excluded"]:
            for person_id, reasons in cohort["excluded"].items():
                print(f"  排除 {person_id}: {'；'.join(reasons)}")
        print(f"选择诊断：最大标准化差异 {diagnostics['max_abs_smd']:.3f}，"
              f"覆盖率-满意度变化相关 "
              f"{_fmt(diagnostics['coverage_satisfaction_corr'])}，"
              f"第2波缺失者基线满意度差 "
              f"{_fmt(diagnostics['wave2_missing_baseline_gap'])}")
        for flag in diagnostics["flags"]:
            print(f"  ! {flag}")
        if association["status"] == "已估计":
            print(f"关联模型（n={association['n']}，参照活动："
                  f"{association['reference_activity']}，{association['evidence']}）")
            for activity, fit in association["coefficients"].items():
                low, high = fit["ci_95"]
                print(f"  {activity} 每增加1小时（相对参照）: "
                      f"{fit['beta_per_hour']:+.3f} 分，95%CI "
                      f"[{low:+.3f}, {high:+.3f}]")
        else:
            print(f"关联模型{association['status']}：{association.get('reason', '')}")
        print()


def _print_scenarios(result):
    print("== 情景比较（硬约束：不负时间、总时长不变、保护照护责任） ==")
    for evaluation in result["evaluations"]:
        scenario = evaluation["scenario"]
        print(f"【{scenario.name}】总体结论：{evaluation['overall_status']}")
        print(f"  可行 {evaluation['feasible_people']}/"
              f"{evaluation['cohort_size']} 人，其中处于共同支持范围内 "
              f"{evaluation['supported_people']} 人")
        if evaluation["selection_reason"]:
            print(f"  选择性缺失限制：{evaluation['selection_reason']}")
        prediction = evaluation["prediction"]
        if prediction["status"] == "区间估计":
            low, high = prediction["delta_satisfaction_ci_95"]
            print(f"  满意度变化（{evaluation['evidence_note']}）: "
                  f"点估计 {prediction['delta_satisfaction_point']:+.3f}，"
                  f"95%CI [{low:+.3f}, {high:+.3f}]")
            print(f"  解释：{prediction['interpretation']}")
        else:
            print(f"  不做精确预测：{prediction.get('reason', prediction['status'])}")
        for item in evaluation["per_person"]:
            line = f"  - {item['person_id']}: {item['status']}"
            if item.get("reason"):
                line += f"（{item['reason']}）"
            print(line)
        print()


def _fmt(value):
    return "无法计算" if value is None else f"{value:+.3f}"


def main():
    base = Path(__file__).parent.parent / "data"
    _print_legacy_summary(base / "observations.csv")
    with tempfile.TemporaryDirectory() as audit_dir:
        result = run_pipeline(data_dir=base, audit_dir=audit_dir)
        _print_coverage(result)
        _print_cohort_and_association(result)
        _print_scenarios(result)
        print("== 审计 ==")
        print(f"原始时间账与情景审计已写入临时目录 {audit_dir} "
              f"（time_ledger.csv / scenario_audit.csv）；需要留存时请指定 audit_dir。")


if __name__ == "__main__":
    main()
