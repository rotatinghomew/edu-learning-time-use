import tempfile
import unittest
from pathlib import Path

from learning_time_use.accounts import (
    ACTIVITIES, build_day_accounts, coverage_audit, person_day_vectors,
    raw_time_ledger,
)
from learning_time_use.diary_io import (
    load_diary_days, load_diary_entries, load_followups,
)

DATA = Path(__file__).parent.parent / "data"


class DiaryIOTests(unittest.TestCase):
    def test_loads_synthetic_entries_with_missing_secondary_as_none(self):
        entries = load_diary_entries(DATA / "diary_entries.csv")
        self.assertTrue(entries)
        blank = [e for e in entries if e["secondary_minutes"] is None]
        explicit_zero = [e for e in entries if e["secondary_minutes"] == 0]
        self.assertTrue(blank, "应存在伴随活动未记录（空字符串）的时段")
        self.assertTrue(explicit_zero, "应存在显式 0 分钟伴随活动的时段")
        # 未记录与显式零必须是两种不同口径，同一时段不能同时落入两类
        self.assertEqual(
            {e["entry_id"] for e in blank} & {e["entry_id"] for e in explicit_zero},
            set())

    def test_overlapping_primary_intervals_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.csv"
            path.write_text(
                "entry_id,day_id,person_id,date,wave,stratum,start_minute,"
                "end_minute,duration_minutes,primary_activity,secondary_activity,"
                "secondary_minutes\n"
                "E1,D1,P1,2026-03-10,1,weekday,0,60,60,学习,无,0\n"
                "E2,D1,P1,2026-03-10,1,weekday,50,100,50,休闲,,\n",
                encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "重叠"):
                load_diary_entries(path)

    def test_secondary_longer_than_primary_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.csv"
            path.write_text(
                "entry_id,day_id,person_id,date,wave,stratum,start_minute,"
                "end_minute,duration_minutes,primary_activity,secondary_activity,"
                "secondary_minutes\n"
                "E1,D1,P1,2026-03-10,1,weekday,0,60,60,家务,学习,90\n",
                encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "伴随活动时长超出"):
                load_diary_entries(path)

    def test_missing_followup_satisfaction_stays_none(self):
        followups = load_followups(DATA / "followups.csv")
        missing = [row for row in followups if row["satisfaction"] is None]
        self.assertEqual({row["person_id"] for row in missing}, {"P07"})

    def test_missing_day_keeps_expected_minutes(self):
        days = load_diary_days(DATA / "diary_days.csv")
        missing = [day for day in days if day["covered_minutes"] == 0]
        self.assertTrue(missing)
        for day in missing:
            self.assertGreater(day["expected_minutes"], 0)


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.entries = load_diary_entries(DATA / "diary_entries.csv")
        self.days = load_diary_days(DATA / "diary_days.csv")
        self.accounts = build_day_accounts(self.days, self.entries)

    def test_complete_days_balance_to_expected_minutes(self):
        for account in self.accounts:
            if account.is_complete:
                self.assertEqual(account.primary_total(),
                                 account.expected_minutes)
                self.assertEqual(sum(account.primary_minutes.values()),
                                 account.expected_minutes)

    def test_partial_days_are_not_scaled_up(self):
        partial = [a for a in self.accounts if not a.is_complete
                   and a.covered_minutes > 0]
        self.assertTrue(partial)
        for account in partial:
            # 已记录主活动分钟不得超过覆盖窗口，且没有被补齐到 expected
            self.assertLessEqual(account.primary_total(),
                                 account.covered_minutes)
            self.assertLess(account.primary_total(),
                            account.expected_minutes)

    def test_primary_and_secondary_ledgers_stay_separate(self):
        for account in self.accounts:
            primary_total = account.primary_total()
            secondary_total = sum(account.secondary_exposure.values())
            # 伴随暴露可以与主活动重叠，因此两个总量不要求相等，
            # 且伴随暴露只可能来自已记录时段，不会超过主活动总量
            self.assertLessEqual(secondary_total, primary_total)

    def test_zero_minutes_distinguished_from_unrecorded(self):
        learners = [a for a in self.accounts
                    if a.primary_minutes["学习"] == 0 and a.is_complete]
        self.assertTrue(learners)
        for account in learners:
            self.assertIn("学习", account.zero_activities,
                          "完整日里 0 分钟应显式登记为确实没做")
        with_unrecorded = [a for a in self.accounts
                           if a.secondary_unrecorded_minutes > 0]
        self.assertTrue(with_unrecorded)

    def test_coverage_audit_splits_strata_and_waves(self):
        audit = coverage_audit(self.accounts)
        weekday_w2 = audit[("weekday", 2)]
        self.assertEqual(weekday_w2["complete_days"], 8)
        self.assertEqual(weekday_w2["partial_days"], 1)
        self.assertEqual(weekday_w2["missing_days"], 1)
        self.assertIn("P07-W2-weekday", weekday_w2["missing_day_ids"])

    def test_person_vectors_only_use_complete_days(self):
        vectors, counts, day_ids = person_day_vectors(
            self.accounts, "weekday", 2)
        self.assertNotIn("P07", vectors, "整日缺失者不能进入完整日向量")
        self.assertNotIn("P10", vectors, "部分日持有者不能被补齐后进入")
        for vector in vectors.values():
            self.assertEqual(sum(vector.values()), 900)

    def test_raw_ledger_preserves_original_records(self):
        ledger = raw_time_ledger(self.accounts)
        partial_rows = [row for row in ledger if row["coverage"] == "部分"]
        self.assertTrue(partial_rows)
        for row in partial_rows:
            self.assertEqual(row["primary_total"], row["covered_minutes"])

    def test_misdeclared_zero_activity_is_rejected(self):
        days = [day.copy() for day in self.days]
        complete_day = next(day for day in days
                            if day["covered_minutes"] == day["expected_minutes"])
        # 谎报一个实际做了 120 分钟的活动为“零分钟”
        complete_day["zero_activities"] = ["照护"]
        with self.assertRaisesRegex(ValueError, "零分钟活动清单不一致"):
            build_day_accounts(days, self.entries)


if __name__ == "__main__":
    unittest.main()
