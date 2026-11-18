import unittest
from pathlib import Path

from learning_time_use.accounts import build_day_accounts
from learning_time_use.cohort import build_model_cohort, selection_diagnostics
from learning_time_use.diary_io import (
    load_diary_days, load_diary_entries, load_followups,
)
from learning_time_use.regression import estimate_substitution, ols, t_critical

DATA = Path(__file__).parent.parent / "data"


class CohortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        entries = load_diary_entries(DATA / "diary_entries.csv")
        days = load_diary_days(DATA / "diary_days.csv")
        cls.accounts = build_day_accounts(days, entries)
        cls.followups = load_followups(DATA / "followups.csv")

    def test_only_complete_two_wave_people_enter_model(self):
        cohort = build_model_cohort(self.accounts, self.followups, "weekday")
        self.assertEqual(
            set(cohort["members"]),
            {"P01", "P02", "P03", "P04", "P05", "P06", "P08", "P09"})
        self.assertIn("P07", cohort["excluded"])
        self.assertTrue(
            any("满意度缺失" in reason for reason in cohort["excluded"]["P07"]))
        self.assertIn("P10", cohort["excluded"])
        self.assertTrue(
            any("完整日记" in reason for reason in cohort["excluded"]["P10"]))

    def test_weekend_and_weekday_never_pooled(self):
        weekday = build_model_cohort(self.accounts, self.followups, "weekday")
        weekend = build_model_cohort(self.accounts, self.followups, "weekend")
        self.assertNotEqual(weekday["members"], weekend["members"])
        self.assertIn("P02", weekday["members"])
        self.assertNotIn("P02", weekend["members"],
                         "P02 周末为部分日，不得进入周末队列")

    def test_selection_diagnostics_flag_nonrandom_missingness(self):
        cohort = build_model_cohort(self.accounts, self.followups, "weekday")
        diagnostics = selection_diagnostics(self.accounts, self.followups, cohort)
        self.assertTrue(diagnostics["flags"],
                        "合成数据的缺失与基线特征相关，应当产生红旗")
        self.assertGreater(diagnostics["max_abs_smd"], 0.2)
        self.assertIsNotNone(diagnostics["wave2_missing_baseline_gap"])

    def test_coverage_correlation_includes_partial_day_people(self):
        # P10 有两波随访但第 2 波是部分日，纳入后覆盖率才有方差
        cohort = build_model_cohort(self.accounts, self.followups, "weekday")
        diagnostics = selection_diagnostics(self.accounts, self.followups, cohort)
        self.assertIsNotNone(diagnostics["coverage_satisfaction_corr"])

    def test_satisfaction_change_is_within_person(self):
        cohort = build_model_cohort(self.accounts, self.followups, "weekday")
        satisfaction = {(r["person_id"], r["wave"]): r["satisfaction"]
                        for r in self.followups}
        for person_id, change in cohort["satisfaction_change"].items():
            expected = satisfaction[(person_id, 2)] - satisfaction[(person_id, 1)]
            self.assertAlmostEqual(change, expected)


class RegressionTests(unittest.TestCase):
    def test_ols_recovers_known_line(self):
        # y = 2 + 3x，无噪声，系数应精确还原
        matrix = [[1.0], [2.0], [3.0], [4.0]]
        targets = [5.0, 8.0, 11.0, 14.0]
        fit = ols(matrix, targets)
        self.assertAlmostEqual(fit["coefficients"][0], 2.0, places=9)
        self.assertAlmostEqual(fit["coefficients"][1], 3.0, places=9)
        self.assertAlmostEqual(fit["r_squared"], 1.0, places=9)
        self.assertEqual(fit["residual_df"], 2)

    def test_ols_rejects_singular_design(self):
        # 完全共线且样本量不足
        with self.assertRaises(ValueError):
            ols([[1.0], [1.0]], [2.0, 3.0])

    def test_t_critical_table_and_normal_approximation(self):
        self.assertAlmostEqual(t_critical(9), 2.262, places=3)
        self.assertAlmostEqual(t_critical(100), 1.96, places=2)

    def test_substitution_association_is_labeled_observational(self):
        entries = load_diary_entries(DATA / "diary_entries.csv")
        days = load_diary_days(DATA / "diary_days.csv")
        accounts = build_day_accounts(days, entries)
        followups = load_followups(DATA / "followups.csv")
        cohort = build_model_cohort(accounts, followups, "weekday")
        association = estimate_substitution(cohort)
        self.assertEqual(association["status"], "已估计")
        self.assertIn("非因果", association["evidence"])
        self.assertIn("学习", association["coefficients"])
        learning = association["coefficients"]["学习"]
        low, high = learning["ci_95"]
        self.assertLess(low, high)
        self.assertTrue(low <= learning["beta_per_hour"] <= high)
        self.assertEqual(association["reference_activity"], "休闲")

    def test_small_sample_refuses_estimation(self):
        tiny = {
            "members": ["P01", "P02"],
            "waves": (1, 2),
            "compositions": {},
            "satisfaction_change": {"P01": 0.5, "P02": -0.2},
        }
        association = estimate_substitution(tiny)
        self.assertEqual(association["status"], "样本不足")


if __name__ == "__main__":
    unittest.main()
