"""把读取、核算、队列、关联与情景串成一条可复核的分析管线。"""
from __future__ import annotations

from pathlib import Path

from .accounts import ACTIVITIES, STRATA, build_day_accounts, coverage_audit
from .audit import explain_excluded_days, export_scenario_audit, export_time_ledger
from .cohort import build_model_cohort, selection_diagnostics
from .diary_io import load_diary_days, load_diary_entries, load_followups, load_persons
from .regression import estimate_substitution
from .scenarios import Scenario, evaluate_scenario

DATA_DIR_DEFAULT = Path(__file__).parent.parent / "data"


def default_scenarios():
    """工作人员要比较的具体可行改变：从可自行安排的休闲转时间给学习。

    照护与“其他”（刚性时间）默认受保护，不设计削减照护责任的情景。
    """
    return [
        Scenario(name="工作日：休闲转出60分钟给学习", stratum="weekday",
                 source_activity="休闲", transfer_minutes=60),
        Scenario(name="工作日：休闲转出120分钟给学习", stratum="weekday",
                 source_activity="休闲", transfer_minutes=120),
        Scenario(name="周末：休闲转出60分钟给学习", stratum="weekend",
                 source_activity="休闲", transfer_minutes=60),
    ]


def run_pipeline(data_dir=DATA_DIR_DEFAULT, scenarios=None, audit_dir=None):
    data_dir = Path(data_dir)
    entries = load_diary_entries(data_dir / "diary_entries.csv")
    days = load_diary_days(data_dir / "diary_days.csv")
    followups = load_followups(data_dir / "followups.csv")
    persons = load_persons(data_dir / "persons.csv")

    accounts = build_day_accounts(days, entries)
    audit = coverage_audit(accounts)
    excluded_days = explain_excluded_days(accounts)

    strata_results = {}
    for stratum in STRATA:
        cohort = build_model_cohort(accounts, followups, stratum)
        diagnostics = selection_diagnostics(accounts, followups, cohort)
        association = estimate_substitution(cohort)
        strata_results[stratum] = {
            "cohort": cohort,
            "diagnostics": diagnostics,
            "association": association,
        }

    scenarios = scenarios or default_scenarios()
    evaluations = []
    for scenario in scenarios:
        result = strata_results[scenario.stratum]
        evaluations.append(evaluate_scenario(
            scenario, accounts, result["association"],
            result["diagnostics"], result["cohort"]))

    if audit_dir is not None:
        audit_dir = Path(audit_dir)
        audit_dir.mkdir(parents=True, exist_ok=True)
        ledger_path = audit_dir / "time_ledger.csv"
        scenario_path = audit_dir / "scenario_audit.csv"
        export_time_ledger(accounts, ledger_path)
        export_scenario_audit(evaluations, scenario_path)

    return {
        "persons": persons,
        "accounts": accounts,
        "coverage_audit": audit,
        "excluded_days": excluded_days,
        "strata": strata_results,
        "evaluations": evaluations,
    }
