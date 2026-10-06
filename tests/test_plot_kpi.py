#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval/plot_kpi.py 回归测试（2026-10-06 测试员补：此前唯一无测试的 eval 模块）。"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("plot_kpi", ROOT / "eval" / "plot_kpi.py")
plot_kpi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plot_kpi)


def test_load_aggregates_and_coverage_last_wins(tmp_path):
    kpi = tmp_path / "kpi.jsonl"
    rows = [
        {"date": "2026-10-01", "kind": "nightly", "signals": 10, "corrections": 1,
         "pruned": 2, "coverage": 0.01},
        {"date": "2026-10-01", "kind": "nightly", "signals": 5, "corrections": 0,
         "pruned": 0, "coverage": 0.02},  # 同日第二跑：可加量求和，coverage 取末值
        {"date": "2026-10-02", "kind": "maint", "signals": 0, "corrections": 0,
         "pruned": 0},  # maint 不进 nightly 口径
    ]
    kpi.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    agg = plot_kpi.load("nightly", kpi_path=kpi)
    assert set(agg) == {"2026-10-01"}
    d = agg["2026-10-01"]
    assert d["signals"] == 15 and d["corrections"] == 1 and d["pruned"] == 2 and d["runs"] == 2
    assert d["coverage"] == 0.02  # 快照取末值，不求和


def test_main_friendly_exit_without_flag_values(capsys):
    """回归（测试员 2026-10-06）：--out/--kind 缺值曾裸 IndexError 崩溃，argparse 应接管。"""
    import sys
    argv = sys.argv
    sys.argv = ["plot_kpi.py", "--out"]
    try:
        rc = plot_kpi.main()
    except SystemExit as e:  # argparse 缺参报错走 SystemExit(2)
        rc = e.code
    finally:
        sys.argv = argv
    assert rc != 0
