"""Compare the legacy extra-tide term against SMOC, retaining observational limits."""
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from domain import read_json, write_json, current_vector, now_jst


def main():
    h = read_json(ROOT / "data" / "hindcast_marine.json")["hourly"]
    index = {t: i for i, t in enumerate(h["time"])}
    rows = []
    for ds, obs in [("2023-11-05", "オーナー実測: 約2kt・北東流、300匹超。時刻別流速系列なし。"),
                    ("2026-08-23", "オーナー実測: 上げ潮ピークの南岸で東→西、1.5〜8kt。観測点・時刻系列なし。")]:
        variants = {"smoc": [], "legacy": []}
        for hour in range(8, 15):
            i = index.get(f"{ds}T{hour:02d}:00")
            if i is None or i < 1 or i + 1 >= len(h["time"]): continue
            speed = h["ocean_current_velocity"][i]
            direction = h["ocean_current_direction"][i]
            prev, nxt = h["sea_level_height_msl"][i-1], h["sea_level_height_msl"][i+1]
            if any(v is None for v in (speed, direction, prev, nxt)): continue
            for mode in variants:
                v, d, tide = current_vector(speed, direction, (nxt-prev)/2, mode)
                variants[mode].append({"hour": hour, "speed_kt": round(v/1.852, 3), "toward_deg": round(d, 1), "extra_tide_kmh": tide})
        rows.append({"date": ds, "observation": obs, "variants": variants})
    reports = {}
    for mode, filename in [("smoc", "engine/backtest_data.json"), ("legacy", "analysis/backtest_legacy.json")]:
        p = ROOT / filename
        if p.exists():
            d = read_json(p)
            reports[mode] = {"period": d["period"], "summary": d["summary"]}
    write_json(ROOT / "analysis" / "current_comparison.json", {
        "generated_at": now_jst().isoformat(), "source": "https://open-meteo.com/en/docs/marine-weather-api",
        "decision": "Use SMOC total current without re-adding tides; local cape-effect calibration remains unvalidated.",
        "cases": rows, "retrospective": reports})
    lines = ["# 潮流方式の検証", "", "SMOCには潮汐が含まれるため、運用は再加算なしとした。旧方式（潮位変化×−20）は比較実験だけに保存。", "",
             "この比較は補正係数の現地校正を完了したという意味ではない。日単位の実測メモだけでは、沖合モデルと島南岸の局所流を分離・同定できない。", "",
             "| 日付 | 方式 | 平均流速 kt (8〜14時) | 有効時間数 |", "|---|---|---:|---:|"]
    for row in rows:
        for mode, values in row["variants"].items():
            avg = f"{statistics.mean(v['speed_kt'] for v in values):.2f}" if values else "不足"
            lines.append(f"| {row['date']} | {mode} | {avg} | {len(values)} |")
    lines += ["", "## 過去再計算の比較（観測選択・記載・透明度のバイアスあり）", "",
              "| 方式 | 評価日 | 目撃Brier | 目撃AUC | 規模完全一致 |", "|---|---:|---:|---:|---:|"]
    for mode, d in reports.items():
        s=d["summary"]; m=s["seen_metrics"]
        lines.append(f"| {mode} | {s['n_days']} | {m['brier']} | {m['auc']} | {s['size_exact_rate']} |")
    lines += ["", "次の校正には地点・深度・時刻を揃えた流速/流向の観測と、Copernicusの潮汐/非潮汐成分が必要。", "既存の2日分のメモを使って係数を合わせ、同じ2日で精度を主張することはしない。", "",
              "[APIの変数定義](https://open-meteo.com/en/docs/marine-weather-api)。目撃精度の差は流速の物理精度を直接保証しない。"]
    (ROOT / "analysis" / "REPORT_current_validation.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


if __name__ == "__main__": main()
