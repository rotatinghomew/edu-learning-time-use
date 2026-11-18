import csv
import tempfile
import unittest
from pathlib import Path

from learning_time_use.accounts import ACTIVITIES
from learning_time_use.pipeline import default_scenarios, run_pipeline
from learning_time_use.scenarios import Scenario

DATA = Path(__file__).parent.parent / "data"


class PipelineTests(unittest.TestCase):
    def test_pipeline_covers_both_strata_and_writes_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_pipeline(data_dir=DATA, audit_dir=directory)
            self.assertEqual(set(result["strata"]), {"weekday", "weekend"})
            self.assertEqual(len(result["evaluations"]), len(default_scenarios()))
            ledger = list(csv.DictReader(
                (Path(directory) / "time_ledger.csv").open(encoding="utf-8")))
            audit = list(csv.DictReader(
                (Path(directory) / "scenario_audit.csv").open(encoding="utf-8")))
            self.assertEqual(len(ledger), 40)  # 10 人 × 2 波 × 2 口径
            self.assertTrue(audit)

    def test_custom_care_reduction_scenario_is_rejected(self):
        # 工作人员不应能构造削减照护责任的情景
        scenario = Scenario(name="削减照护", stratum="weekday",
                            source_activity="照护", transfer_minutes=60)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "受保护"):
                run_pipeline(data_dir=DATA, scenarios=[scenario],
                             audit_dir=directory)

    def test_scenario_vectors_keep_day_length_for_every_person(self):
        result = run_pipeline(data_dir=DATA)
        for evaluation in result["evaluations"]:
            for item in evaluation["per_person"]:
                if "counterfactual_vector" not in item:
                    continue
                baseline_total = sum(item["baseline_vector"].values())
                scenario_total = sum(item["counterfactual_vector"].values())
                self.assertEqual(baseline_total, scenario_total)
                self.assertTrue(all(
                    value >= 0 for value in
                    item["counterfactual_vector"].values()))
                self.assertEqual(baseline_total, 900)

    def test_partial_day_people_absent_from_all_model_cohorts(self):
        result = run_pipeline(data_dir=DATA)
        weekday_members = result["strata"]["weekday"]["cohort"]["members"]
        weekend_members = result["strata"]["weekend"]["cohort"]["members"]
        self.assertNotIn("P10", weekday_members)
        self.assertNotIn("P10", weekend_members)
        self.assertNotIn("P07", weekday_members)
        self.assertNotIn("P07", weekend_members)

    def test_all_activities_accounted_for_in_ledger_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            run_pipeline(data_dir=DATA, audit_dir=directory)
            with (Path(directory) / "time_ledger.csv").open(
                    encoding="utf-8") as stream:
                header = next(csv.reader(stream))
            for activity in ACTIVITIES:
                self.assertIn(f"primary_{activity}", header)
                self.assertIn(f"secondary_{activity}", header)


if __name__ == "__main__":
    unittest.main()
