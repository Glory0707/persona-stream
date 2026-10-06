#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval/check_events.py 回归测试：事件流完整性/密钥兜底/游标一致性。"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_checker():
    spec = importlib.util.spec_from_file_location("check_events", ROOT / "eval" / "check_events.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_event(d, name, obj, raw=None):
    p = d / name
    with open(p, "a", encoding="utf-8") as f:
        f.write(raw if raw is not None else json.dumps(obj, ensure_ascii=False) + "\n")


def base_event(eid="a1", text="正常内容"):
    return {"id": eid, "ts": "2026-09-09T10:00:00+08:00", "hook": "UserPromptSubmit",
            "session": "sess_x", "type": "prompt", "text": text}


def test_clean_stream_passes(tmp_path):
    write_event(tmp_path, "events-20260909.jsonl", base_event("a1"))
    write_event(tmp_path, "events-20260909.jsonl", base_event("a2"))
    m = load_checker()
    errors, warns, stats = m.scan_events(str(tmp_path))
    assert errors == [], errors
    assert stats["events"] == 2 and stats["ids"] == 2


def test_detects_bad_json_dup_id_missing_field(tmp_path):
    write_event(tmp_path, "events-20260909.jsonl", base_event("a1"))
    write_event(tmp_path, "events-20260909.jsonl", base_event("a1"))
    write_event(tmp_path, "events-20260909.jsonl", None, raw="{broken\n")
    bad = {"id": "a3", "hook": "Stop"}  # 缺 ts/session
    write_event(tmp_path, "events-20260909.jsonl", bad)
    m = load_checker()
    errors, _, _ = m.scan_events(str(tmp_path))
    assert any("id 重复" in e for e in errors)
    assert any("JSON 解析失败" in e for e in errors)
    assert any("缺公共字段" in e for e in errors)


def test_detects_unredacted_secret_but_ignores_synthetic(tmp_path):
    write_event(tmp_path, "events-20260909.jsonl",
                base_event("a1", "key 0123456789abcdef0123456789abcdef.DEMOnotareal99"))
    write_event(tmp_path, "events-20260909.jsonl",
                base_event("a2", "fixture sk-abcdefghijklmnopqrstuvwx"))
    m = load_checker()
    errors, _, _ = m.scan_events(str(tmp_path))
    assert any("BigModel" in e for e in errors)
    assert not any("sk-abc" in e for e in errors)


def test_cursor_overrun_flagged(tmp_path):
    write_event(tmp_path, "events-20260909.jsonl", base_event("a1"))
    (tmp_path / "state.json").write_text(
        json.dumps({"updated": "t", "files": {"events-20260909.jsonl": 5}}), encoding="utf-8")
    m = load_checker()
    errors, _, _ = m.scan_events(str(tmp_path))
    assert any("超过实际行数" in e for e in errors)
