#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KPI 曲线图：读取 eval/kpi.jsonl，绘制纠正率/减法量/fold 覆盖率趋势 → eval/kpi.png。

长期验收标准（FOLD_RULES §8）：
- rate（corrections/signals）应随时间下降 —— 只统计 kind=nightly（mining/maint 的 signals 不可比）
- pruned 不应长期为 0 —— 为 0 说明减法失效，走回"记忆 md 只增不减"的老路

同一天多次运行会按日聚合（signals/corrections/pruned 求和后再算 rate）。
无数据或数据不足时友好退出，供夜间任务/手动随时调用。

用法：python eval/plot_kpi.py [--out eval/kpi.png] [--kind nightly]
"""
import argparse
import collections
import json
import os
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: E402
except ImportError:  # 可选依赖：只做 KPI 聚合（如 CI）时无需安装
    matplotlib = None
    plt = None

ROOT = Path(os.environ["PERSONA_HOME"]) if os.environ.get("PERSONA_HOME") else Path(__file__).resolve().parent.parent
KPI = ROOT / "eval" / "kpi.jsonl"

if plt is not None:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False


def load(kind, kpi_path=KPI):
    """按日聚合 kpi 行；coverage 是快照不是可加量，同日多行取末值。"""
    rows = [json.loads(l) for l in Path(kpi_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r.get("date") and (kind == "all" or r.get("kind", "nightly") == kind)]
    agg = collections.OrderedDict()
    for r in sorted(rows, key=lambda x: x["date"]):
        d = agg.setdefault(r["date"], {"signals": 0, "corrections": 0, "pruned": 0,
                                       "runs": 0, "coverage": None})
        d["signals"] += r.get("signals", 0)
        d["corrections"] += r.get("corrections", 0)
        d["pruned"] += r.get("pruned", 0)
        d["runs"] += 1
        if r.get("coverage") is not None:
            d["coverage"] = r["coverage"]
    return agg


def main():
    ap = argparse.ArgumentParser(description="KPI 趋势图（纠正率/减法量/覆盖率）")
    ap.add_argument("--out", default=str(ROOT / "eval" / "kpi.png"))
    ap.add_argument("--kind", default="nightly", choices=["nightly", "mining", "maint", "all"])
    args = ap.parse_args()
    out = Path(args.out)
    kind = args.kind
    if not KPI.exists():
        print("kpi.jsonl 不存在，尚无数据")
        return 0
    agg = load(kind)
    if len(agg) < 2:
        print(f"kind={kind} 数据不足（{len(agg)} 天），至少积累 2 天再绘图")
        return 0

    if plt is None:
        print("未安装 matplotlib（pip install matplotlib），无法绘图；KPI 聚合数据如上可用")
        return 0

    dates = list(agg)
    rates = [agg[d]["corrections"] / max(agg[d]["signals"], 1) for d in dates]
    pruned = [agg[d]["pruned"] for d in dates]
    corr = [agg[d]["corrections"] for d in dates]
    cov_days = [d for d in dates if agg[d]["coverage"] is not None]
    cov = [agg[d]["coverage"] for d in cov_days]

    fig, (ax1, ax3) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 2]})
    ax1.plot(dates, rates, "o-", color="#c0392b", label="纠正率 rate（期望下降）")
    if cov_days:
        ax1.plot(cov_days, cov, "s--", color="#8e44ad",
                 label="fold 覆盖率 coverage（分母=已消费可折事件，看趋势）")
    ax1.set_ylabel("比率（0 起）")
    ax1.set_ylim(bottom=0)
    ax2 = ax1.twinx()
    ax2.bar(dates, corr, alpha=0.22, color="#2980b9", label="纠正次数")
    ax2.set_ylabel("纠正次数")
    ax1.set_title(f"persona-stream · 折叠质量趋势（kind={kind}，{len(agg)} 天 / "
                  f"{sum(agg[d]['runs'] for d in dates)} 次运行）")
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper right")

    ax3.bar(dates, pruned, color="#27ae60", alpha=0.7, label="pruned 减法量（不应长期为 0）")
    zero_days = sum(1 for p in pruned if p == 0)
    ax3.set_ylabel("减法条目数")
    ax3.set_xlabel(f"日期（pruned=0 的天数：{zero_days}/{len(dates)}）")
    ax3.legend(loc="upper right")
    fig.autofmt_xdate(rotation=45)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    print(f"已生成 {out}（kind={kind}，{len(agg)} 天；pruned=0 占 {zero_days}/{len(dates)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
