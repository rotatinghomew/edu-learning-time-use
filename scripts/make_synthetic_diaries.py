"""生成时间日记分析所用的合成记录。

所有记录均为本地合成数据，不包含个人信息。脚本使用固定随机种子，
重复运行得到完全一致的文件，便于测试复核。

输出文件（写入 data/ 目录）：
  persons.csv      人员名册与退出标记
  diary_days.csv   日记日覆盖元数据（完整/部分/缺失原因）
  diary_entries.csv 时段级主活动与伴随活动
  followups.csv    生活满意度随访（含流失）

设计要点（与分析口径对应）：
  * 部分日记只覆盖醒来后的前 840 分钟，不做任何按比例补齐；
  * 主活动各分钟互斥，完整日的主活动合计 900 分钟；
  * 伴随活动可能为空（未记录），与确实做了 0 分钟不是同一口径；
  * 零分钟（确实没做）与未记录在 diary_days 中分开标注；
  * 工作日与周末分别构造、分别分析，回收比例不同也不混合比较。
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DAY_MINUTES = 900  # 合成日记的清醒时段长度（15 小时）
PARTIAL_MINUTES = 840
RANDOM_SEED = 20260318

ACTIVITIES = ["学习", "照护", "家务", "休闲", "其他"]
PERSON_IDS = [f"P{idx:02d}" for idx in range(1, 11)]

# 基线（第 1 波）工作日的主活动安排，合计均为 900 分钟。
BASELINE_WEEKDAY = {
    "P01": {"学习": 60, "照护": 120, "家务": 90, "休闲": 330, "其他": 300},
    "P02": {"学习": 0, "照护": 300, "家务": 60, "休闲": 240, "其他": 300},
    "P03": {"学习": 30, "照护": 60, "家务": 120, "休闲": 390, "其他": 300},
    "P04": {"学习": 90, "照护": 0, "家务": 90, "休闲": 420, "其他": 300},
    "P05": {"学习": 0, "照护": 240, "家务": 150, "休闲": 210, "其他": 300},
    "P06": {"学习": 45, "照护": 90, "家务": 60, "休闲": 405, "其他": 300},
    "P07": {"学习": 15, "照护": 180, "家务": 120, "休闲": 285, "其他": 300},
    "P08": {"学习": 120, "照护": 60, "家务": 60, "休闲": 360, "其他": 300},
    "P09": {"学习": 0, "照护": 0, "家务": 180, "休闲": 420, "其他": 300},
    "P10": {"学习": 75, "照护": 150, "家务": 90, "休闲": 285, "其他": 300},
}

# 基线周末安排：照护普遍更重，学习略少。
BASELINE_WEEKEND = {
    "P01": {"学习": 30, "照护": 180, "家务": 120, "休闲": 330, "其他": 240},
    "P02": {"学习": 0, "照护": 360, "家务": 60, "休闲": 240, "其他": 240},
    "P03": {"学习": 30, "照护": 90, "家务": 150, "休闲": 390, "其他": 240},
    "P04": {"学习": 60, "照护": 0, "家务": 120, "休闲": 480, "其他": 240},
    "P05": {"学习": 0, "照护": 300, "家务": 120, "休闲": 240, "其他": 240},
    "P06": {"学习": 30, "照护": 120, "家务": 90, "休闲": 420, "其他": 240},
    "P07": {"学习": 0, "照护": 240, "家务": 150, "休闲": 270, "其他": 240},
    "P08": {"学习": 90, "照护": 90, "家务": 90, "休闲": 390, "其他": 240},
    "P09": {"学习": 0, "照护": 0, "家务": 150, "休闲": 510, "其他": 240},
    "P10": {"学习": 60, "照护": 180, "家务": 120, "休闲": 300, "其他": 240},
}

# 第 2 波工作日的变化向量（分钟），各列之和为 0：学习时间主要来自休闲，
# 同时加入少量照护/家务/其他的再配置，避免设计矩阵完全共线。
WAVE2_DELTA_WEEKDAY = {
    "P01": {"学习": 90, "照护": 0, "家务": 0, "休闲": -90, "其他": 0},
    "P02": {"学习": 60, "照护": 0, "家务": 0, "休闲": -60, "其他": 0},
    "P03": {"学习": 120, "照护": 0, "家务": -30, "休闲": -90, "其他": 0},
    "P04": {"学习": 60, "照护": 0, "家务": 0, "休闲": -60, "其他": 0},
    "P05": {"学习": 90, "照护": 30, "家务": 0, "休闲": -120, "其他": 0},
    "P06": {"学习": 120, "照护": 0, "家务": 30, "休闲": -150, "其他": 0},
    # P07 第 2 波工作日日记缺失（覆盖问题），仍写出其“真实”安排仅用于随访故事线，
    # 但 diary_days 标记为缺失，分析端不会看到 entries。
    "P07": {"学习": 60, "照护": 0, "家务": 0, "休闲": -60, "其他": 0},
    "P08": {"学习": 60, "照护": -30, "家务": 0, "休闲": -30, "其他": 0},
    "P09": {"学习": 120, "照护": 0, "家务": 0, "休闲": -120, "其他": 0},
    # P10 第 2 波只交了部分日记。
    "P10": {"学习": 90, "照护": 0, "家务": -30, "休闲": -60, "其他": 0},
}

# 周末变化约为工作日的一半强度，照护不被削减。
WAVE2_DELTA_WEEKEND = {
    "P01": {"学习": 60, "照护": 0, "家务": 0, "休闲": -60, "其他": 0},
    "P02": {"学习": 30, "照护": 0, "家务": 0, "休闲": -30, "其他": 0},
    "P03": {"学习": 60, "照护": 0, "家务": -30, "休闲": -30, "其他": 0},
    "P04": {"学习": 30, "照护": 0, "家务": 0, "休闲": -30, "其他": 0},
    "P05": {"学习": 60, "照护": 0, "家务": 0, "休闲": -60, "其他": 0},
    "P06": {"学习": 60, "照护": 0, "家务": 30, "休闲": -90, "其他": 0},
    "P07": {"学习": 30, "照护": 0, "家务": 0, "休闲": -30, "其他": 0},
    "P08": {"学习": 30, "照护": 0, "家务": 0, "休闲": -30, "其他": 0},
    "P09": {"学习": 60, "照护": 0, "家务": 0, "休闲": -60, "其他": 0},
    "P10": {"学习": 30, "照护": 0, "家务": 0, "休闲": -30, "其他": 0},
}

# 第 2 波日记覆盖情况：P07 工作日整日缺失；P10 第 2 波两天均为部分日；
# P02 周末部分日（高照护者漏记晚间）。
WAVE2_COVERAGE = {
    "P02": {"weekday": "full", "weekend": "partial"},
    "P07": {"weekday": "missing", "weekend": "full"},
    "P10": {"weekday": "partial", "weekend": "partial"},
}

# 随访安排：P07 第 2 波流失（无随访），其余有两波满意度。
BASE_SATISFACTION = {
    "P01": 6.0, "P02": 5.0, "P03": 6.5, "P04": 7.0, "P05": 4.5,
    "P06": 6.0, "P07": 5.5, "P08": 7.5, "P09": 6.0, "P10": 5.5,
}
LEARNING_EFFECT_PER_HOUR = 0.5  # 合成“真实”关联：每增加 1 小时学习（来自休闲）
SATISFACTION_NOISE = {
    "P01": 0.1, "P02": -0.2, "P03": 0.25, "P04": -0.1, "P05": 0.15,
    "P06": -0.25, "P07": 0.0, "P08": 0.1, "P09": -0.15, "P10": 0.2,
}

DATES = {
    1: {"weekday": "2026-03-10", "weekend": "2026-03-14"},
    2: {"weekday": "2026-09-08", "weekend": "2026-09-12"},
}


def _apply_delta(base, delta):
    result = {}
    for activity in ACTIVITIES:
        value = base[activity] + delta[activity]
        if value < 0:
            raise ValueError(f"合成安排出现负时间: {activity}={value}")
        result[activity] = value
    if sum(result.values()) != DAY_MINUTES:
        raise ValueError("合成安排合计不等于一天的清醒时长")
    return result


def _schedule(person_id, wave, stratum):
    base = BASELINE_WEEKDAY[person_id] if stratum == "weekday" else BASELINE_WEEKEND[person_id]
    if wave == 1:
        return dict(base)
    delta = WAVE2_DELTA_WEEKDAY[person_id] if stratum == "weekday" else WAVE2_DELTA_WEEKEND[person_id]
    return _apply_delta(base, delta)


def _coverage(person_id, wave, stratum):
    if wave == 1:
        return "full"
    return WAVE2_COVERAGE.get(person_id, {}).get(stratum, "full")


def _secondary_for(person_id, activity, rng):
    """伴随活动暴露：只对部分活动生成，未记录返回 None。"""
    if activity == "家务":
        # 做家务时约一半的时段伴随收听学习类广播/音频。
        return "学习" if rng.random() < 0.5 else None
    if activity == "休闲":
        # 休闲中约四分之一时段伴随轻度学习（读报、看教学视频）。
        return "学习" if rng.random() < 0.25 else None
    if activity == "照护":
        return None  # 照护时段未记录伴随活动（不是 0 分钟，是未记录）
    return None


def _write_csv(path, header, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


def generate():
    rng = random.Random(RANDOM_SEED)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    persons_rows = []
    days_rows = []
    entry_rows = []
    followup_rows = []
    entry_id = 1

    for person_id in PERSON_IDS:
        persons_rows.append([
            person_id,
            "女" if int(person_id[1:]) % 2 == 0 else "男",
            62 + int(person_id[1:]),
            "退出" if person_id == "P07" else "在访",
        ])

    for person_id in PERSON_IDS:
        for wave in (1, 2):
            for stratum in ("weekday", "weekend"):
                coverage = _coverage(person_id, wave, stratum)
                date = DATES[wave][stratum]
                day_id = f"{person_id}-W{wave}-{stratum}"
                if coverage == "missing":
                    days_rows.append([
                        day_id, person_id, date, wave, stratum, 0, DAY_MINUTES,
                        "整日未回收", "",
                    ])
                    continue

                schedule = _schedule(person_id, wave, stratum)
                covered = DAY_MINUTES if coverage == "full" else PARTIAL_MINUTES
                # 把活动按固定顺序切成连续时段，部分日只保留落在窗口内的时段。
                cursor = 0
                zero_activities = []
                recorded_secondary_activities = set()
                for activity in ACTIVITIES:
                    duration = schedule[activity]
                    start, end = cursor, cursor + duration
                    cursor = end
                    if duration == 0:
                        zero_activities.append(activity)
                        continue
                    if start >= covered:
                        continue
                    clipped_end = min(end, covered)
                    secondary = _secondary_for(person_id, activity, rng)
                    if activity == "学习":
                        # 主活动为学习时，伴随活动记为“无”（确实没有伴随活动）。
                        secondary_value = "无"
                        secondary_minutes = 0
                    elif secondary is None:
                        secondary_value = ""  # 未记录：与 0 分钟区分
                        secondary_minutes = ""
                    else:
                        secondary_value = secondary
                        secondary_minutes = clipped_end - start
                        recorded_secondary_activities.add(secondary)
                    entry_rows.append([
                        f"E{entry_id:04d}", day_id, person_id, date, wave,
                        stratum, start, clipped_end, clipped_end - start,
                        activity, secondary_value, secondary_minutes,
                    ])
                    entry_id += 1

                partial_reason = "部分回收：晚间漏记" if coverage == "partial" else ""
                days_rows.append([
                    day_id, person_id, date, wave, stratum,
                    covered, DAY_MINUTES, partial_reason,
                    "|".join(zero_activities),
                ])

    # 随访：第 1 波全员；第 2 波 P07 流失。
    for person_id in PERSON_IDS:
        followup_rows.append([person_id, 1, BASE_SATISFACTION[person_id], ""])
        if person_id == "P07":
            followup_rows.append([person_id, 2, "", "失访：未取得联系"])
            continue
        weekday_delta = WAVE2_DELTA_WEEKDAY[person_id]
        learning_hours = weekday_delta["学习"] / 60.0
        # 合成满意度变化与“学习替代休闲”的小时数挂钩，加入固定小扰动。
        wave2_value = (
            BASE_SATISFACTION[person_id]
            + LEARNING_EFFECT_PER_HOUR * learning_hours
            + SATISFACTION_NOISE[person_id]
        )
        followup_rows.append([person_id, 2, round(max(0.0, min(10.0, wave2_value)), 2), ""])

    _write_csv(DATA_DIR / "persons.csv",
               ["person_id", "gender", "age", "status"], persons_rows)
    _write_csv(DATA_DIR / "diary_days.csv",
               ["day_id", "person_id", "date", "wave", "stratum",
                "covered_minutes", "expected_minutes", "missing_reason",
                "zero_activities"], days_rows)
    _write_csv(DATA_DIR / "diary_entries.csv",
               ["entry_id", "day_id", "person_id", "date", "wave", "stratum",
                "start_minute", "end_minute", "duration_minutes",
                "primary_activity", "secondary_activity",
                "secondary_minutes"], entry_rows)
    _write_csv(DATA_DIR / "followups.csv",
               ["person_id", "wave", "satisfaction", "note"], followup_rows)
    print(f"已生成合成记录：{len(persons_rows)} 人，{len(days_rows)} 个日记日，"
          f"{len(entry_rows)} 条时段，{len(followup_rows)} 条随访")


if __name__ == "__main__":
    generate()
