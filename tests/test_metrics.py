#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval/metrics.py 回归测试（审查报告 §7②：fold 覆盖率 + 证据回链率）。"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("metrics", ROOT / "eval" / "metrics.py")
metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(metrics)


def _ev(eid, ts="2026-10-05T10:00:00+08:00", typ="prompt", text="内容"):
    return json.dumps({"id": eid, "ts": ts, "type": typ, "text": text}, ensure_ascii=False)


def _mk(tmp_path, event_ids, cursor_lines, persona_yaml, ledger_rows):
    data = tmp_path / "data"
    data.mkdir()
    (data / "events-20261005.jsonl").write_text(
        "\n".join(_ev(i) for i in event_ids) + "\n", encoding="utf-8")
    (data / "state.json").write_text(json.dumps(
        {"files": {"events-20261005.jsonl": cursor_lines}}), encoding="utf-8")
    persona = tmp_path / "persona"
    persona.mkdir()
    (persona / "threads").mkdir()
    (persona / "threads" / "t.yaml").write_text(persona_yaml, encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in ledger_rows) + "\n",
                      encoding="utf-8")
    return str(data), str(persona), str(ledger)


def test_coverage_backlink_broken(tmp_path):
    pyml = "id: t\nevidence: [\"aaaaaaaaaaaa\", \"ffffffffffff\"]\n"
    data, persona, ledger = _mk(
        tmp_path,
        event_ids=["aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"],
        cursor_lines=3,
        persona_yaml=pyml,
        ledger_rows=[{"date": "2026-10-05", "action": "add", "target": "threads/t",
                      "block_key": "k", "event_ids": ["bbbbbbbbbbbb"]}])
    m = metrics.compute(data_dir=data, persona=persona, ledger_path=ledger)
    # coverage：消费 3 条，persona 引 a + 账本引 b → 2/3
    assert m["consumed"] == 3 and m["coverage"] == 0.6667
    # backlink：账本 1 id（b），在 persona 证据链里 → 0（b 未回链到档案）
    assert m["ledger_ids"] == 1 and m["backlink"] == 0.0
    # broken：persona 引用的 ffffffffffff 全库不存在，且定位到文件:字段
    assert m["broken"] == ["ffffffffffff"]
    assert m["broken_where"]["ffffffffffff"].endswith("t.yaml:evidence")
    # 回链修复后 backlink=1、broken 仍计 f
    fixed = pyml.replace("aaaaaaaaaaaa", "bbbbbbbbbbbb")
    (Path(persona) / "threads" / "t.yaml").write_text(fixed, encoding="utf-8")
    m2 = metrics.compute(data_dir=data, persona=persona, ledger_path=ledger)
    assert m2["backlink"] == 1.0 and m2["broken"] == ["ffffffffffff"]


def test_no_ledger_and_bad_cursor(tmp_path):
    data, persona, ledger = _mk(
        tmp_path,
        event_ids=["aaaaaaaaaaaa"],
        cursor_lines=1,
        persona_yaml="id: t\nevidence: [\"aaaaaaaaaaaa\"]\n",
        ledger_rows=[])
    Path(ledger).write_text("", encoding="utf-8")  # 空账本
    m = metrics.compute(data_dir=data, persona=persona, ledger_path=ledger)
    assert m["backlink"] is None and m["coverage"] == 1.0 and m["broken"] == []
    # 游标损坏 → consumed=0，coverage=None，不炸
    (Path(data) / "state.json").write_text("{broken", encoding="utf-8")
    m2 = metrics.compute(data_dir=data, persona=persona, ledger_path=ledger)
    assert m2["consumed"] == 0 and m2["coverage"] is None
