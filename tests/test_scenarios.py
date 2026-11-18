import csv
import tempfile
import unittest
from pathlib import Path

from learning_time_use.accounts import ACTIVITIES, build_day_accounts
from learning_time_use.audit import (
    explain_excluded_days, export_scenario_audit, export_time_ledger,
)
from learning_time_use.cohort import build_model_cohort, selection_diagnostics
from learning_time_use.diary_io import (
    load_diary_days, load_diary_entries, load_followups,
)
from learning_time_use.regression import estimate_substitution
from learning_time_use.scenarios import (
    PROTECTED_DEFAULT, Scenario, apply_to_vector, evaluate_scenario,
    validate_scenario,
)

DATA = Path(__file__).parent.parent / "data"


class ScenarioConstraintTests(unittest.TestCase):
    def test_transfer_keeps_total_minutes_and_nonnegativity(self):
        vector = {activity: 180 for activity in ACTIVITIES}
        scenario = Scenario(name="t", stratum="weekday",
                            source_activity="休闲", transfer_minutes=60)
        new_vector, reason = apply_to_vector(vector, scenario)
        self.assertIsNone(reason)
        self.assertEqual(sum(new_vector.values()), sum(vector.values()))
        self.assertEqual(new_vector["学习"], 240)
        self.assertEqual(new_vector["休闲"], 120)
        self.assertTrue(all(value >= 0 for value in new_vector.values()))

    def test_transfer_cannot_make_source_negative(self):
        vector = {activity: 180 for activity in ACTIVITIES}
        vector["休闲"] = 30
        scenario = Scenario(name="t", stratum="weekday",
                            source_activity="休闲", transfer_minutes=60)
        new_vector, reason = apply_to_vector(vector, scenario)
        self.assertIsNone(new_vector)
        self.assertIn("不足以转出", reason)

    def test_protected_activities_cannot_be_source(self):
        for protected in PROTECTED_DEFAULT:
            scenario = Scenario(name="t", stratum="weekday",
                                source_activity=protected, transfer_minutes=30)
            with self.assertRaisesRegex(ValueError, "受保护"):
                validate_scenario(scenario)

    def test_nonpositive_transfer_rejected(self):
        scenario = Scenario(name="t", stratum="weekday",
                            source_activity="休闲", transfer_minutes=0)
        with self.assertRaisesRegex(ValueError, "正数"):
            validate_scenario(scenario)


class ScenarioEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        entries = load_diary_entries(DATA / "diary_entries.csv")
        days = load_diary_days(DATA / "diary_days.csv")
        cls.accounts = build_day_accounts(days, entries)
        cls.followups = load_followups(DATA / "followups.csv")
        cls.weekday = build_model_cohort(
            cls.accounts, cls.followups, "weekday")
        cls.diagnostics = selection_diagnostics(
            cls.accounts, cls.followups, cls.weekday)
        cls.association = estimate_substitution(cls.weekday)

    def _evaluate(self, transfer):
        scenario = Scenario(name=f"休闲转学习{transfer}", stratum="weekday",
                            source_activity="休闲", transfer_minutes=transfer)
        return evaluate_scenario(
            scenario, self.accounts, self.association,
            self.diagnostics, self.weekday)

    def test_infeasible_person_is_flagged_not_fitted(self):
        evaluation = self._evaluate(120)
        p05 = next(item for item in evaluation["per_person"]
                   if item["person_id"] == "P05")
        self.assertEqual(p05["status"], "不可行")
        self.assertEqual(evaluation["feasible_people"], 7)

    def test_unsupported_combo_gets_no_precise_promise(self):
        evaluation = self._evaluate(120)
        self.assertEqual(evaluation["supported_people"], 0)
        for item in evaluation["per_person"]:
            if item["status"] == "证据不足":
                self.assertIn("不外推", item["reason"])

    def test_supported_combination_exists_for_modest_transfer(self):
        evaluation = self._evaluate(60)
        self.assertGreaterEqual(evaluation["supported_people"], 1)
        supported = [item for item in evaluation["per_person"]
                     if item["status"] == "可评估"]
        for item in supported:
            self.assertGreaterEqual(item["support_count"], 3)
            # 匹配到的必须是同口径真实完整日
            for day_id in item["support_day_ids"]:
                account = next(a for a in self.accounts if a.day_id == day_id)
                self.assertEqual(account.stratum, "weekday")
                self.assertTrue(account.is_complete)

    def test_selection_flags_downgrade_recommendation(self):
        # 合成数据存在选择性缺失红旗：即使有人落在支持范围内，
        # 总体结论也不得升到“可推荐”
        evaluation = self._evaluate(60)
        self.assertIn(evaluation["overall_status"],
                      {"谨慎参考", "证据不足", "不可行"})
        self.assertTrue(evaluation["selection_reason"])

    def test_prediction_is_interval_and_labeled_observational(self):
        evaluation = self._evaluate(60)
        prediction = evaluation["prediction"]
        self.assertEqual(prediction["status"], "区间估计")
        low, high = prediction["delta_satisfaction_ci_95"]
        self.assertLess(low, high)
        self.assertIn("观察关联", prediction["interpretation"])

    def test_strata_are_never_mixed_in_support(self):
        weekend_cohort = build_model_cohort(
            self.accounts, self.followups, "weekend")
        weekend_diag = selection_diagnostics(
            self.accounts, self.followups, weekend_cohort)
        weekend_assoc = estimate_substitution(weekend_cohort)
        scenario = Scenario(name="周末情景", stratum="weekend",
                            source_activity="休闲", transfer_minutes=60)
        evaluation = evaluate_scenario(
            scenario, self.accounts, weekend_assoc,
            weekend_diag, weekend_cohort)
        for item in evaluation["per_person"]:
            for day_id in item.get("support_day_ids", []):
                account = next(a for a in self.accounts if a.day_id == day_id)
                self.assertEqual(account.stratum, "weekend")


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        entries = load_diary_entries(DATA / "diary_entries.csv")
        days = load_diary_days(DATA / "diary_days.csv")
        cls.accounts = build_day_accounts(days, entries)

    def test_excluded_days_explain_every_incomplete_record(self):
        excluded = explain_excluded_days(self.accounts)
        incomplete = [a for a in self.accounts if not a.is_complete]
        self.assertEqual({item["day_id"] for item in excluded},
                         {a.day_id for a in incomplete})
        for item in excluded:
            self.assertTrue(item["reason"])

    def test_ledger_and_scenario_audit_round_trip(self):
        from learning_time_use.pipeline import run_pipeline
        with tempfile.TemporaryDirectory() as directory:
            result = run_pipeline(data_dir=DATA, audit_dir=directory)
            ledger_path = Path(directory) / "time_ledger.csv"
            scenario_path = Path(directory) / "scenario_audit.csv"
            self.assertTrue(ledger_path.exists())
            self.assertTrue(scenario_path.exists())
            with ledger_path.open(encoding="utf-8") as stream:
                ledger_rows = list(csv.DictReader(stream))
            self.assertEqual(len(ledger_rows), len(self.accounts))
            # 部分日在审计中保持原记录，不出现补齐值
            for row in ledger_rows:
                if row["coverage"] == "部分":
                    self.assertEqual(int(row["primary_total"]),
                                     int(row["covered_minutes"]))
            with scenario_path.open(encoding="utf-8") as stream:
                scenario_rows = list(csv.DictReader(stream))
            expected = sum(len(e["per_person"]) for e in result["evaluations"])
            self.assertEqual(len(scenario_rows), expected)
            n_exported = export_time_ledger(self.accounts,
                                            Path(directory) / "again.csv")
            self.assertEqual(n_exported, len(self.accounts))


if __name__ == "__main__":
    unittest.main()
