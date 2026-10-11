#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval/missed_sessions.py 回归测试（2026-10-11 HPMC 漏折事故的完整性网）。

契约：零引用（账本∪evidence 不含任何事件 id）的内容会话按纪元列出；
斜杠命令/空 stop 不算内容；同 id 去重；fold_exclusions 前缀命中即抑制。
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("missed_sessions", ROOT / "eval" / "missed_sessions.py")
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)


def _ev(eid, ts, typ, session, text="", reply=None):
    o = {"id": eid, "ts": ts, "type": typ, "session": session, "cwd": "D:/x"}
    if typ == "prompt":
        o["text"] = text
    else:
        o["reply"] = {"text": reply or ""}
    return json.dumps(o, ensure_ascii=False)


def _mk(tmp_path, lines, cursor=None, exclusions=""):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    (data / "events-20261011.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (data / "state.json").write_text(
        json.dumps({"files": {"events-20261011.jsonl": cursor if cursor is not None else len(lines)}}),
        encoding="utf-8")
    if exclusions:
        (data / "fold_exclusions.jsonl").write_text(exclusions, encoding="utf-8")
    persona = tmp_path / "persona"
    (persona / "threads").mkdir(parents=True, exist_ok=True)
    return str(data), str(persona)


def test_uncited_session_listed_and_buckets(tmp_path):
    lines = [
        _ev("aaaaaaaaaaaa", "2026-10-11T10:00:00+08:00", "prompt", "sess_u1", text="找一篇论文的pdf"),
        _ev("bbbbbbbbbbbb", "2026-10-11T10:05:00+08:00", "stop", "sess_u1", reply="结论：没有合法电子版"),
        _ev("cccccccccccc", "2026-09-20T10:00:00+08:00", "prompt", "sess_old", text="账本前的闲聊"),
        _ev("dddddddddddd", "2026-06-01T10:00:00+08:00", "prompt", "sess_ancient", text="蒸馏器之前的"),
    ]
    data, persona = _mk(tmp_path, lines)
    m = ms.scan(data_dir=data, persona=persona, cited=set())
    assert len(m["ledger"]) == 1 and m["ledger"][0]["session"] == "sess_u1"
    assert m["ledger"][0]["n"] == 2 and "论文" in m["ledger"][0]["snippet"]
    assert len(m["pre_ledger"]) == 1 and m["pre_ledger"][0]["session"] == "sess_old"
    assert m["pre_distiller"] == {"sessions": 1, "events": 1}


def test_cited_and_noise_and_dup_not_counted(tmp_path):
    lines = [
        _ev("aaaaaaaaaaaa", "2026-10-11T10:00:00+08:00", "prompt", "sess_cited", text="实质内容"),
        _ev("bbbbbbbbbbbb", "2026-10-11T10:01:00+08:00", "prompt", "sess_slash", text="/clear"),
        _ev("cccccccccccc", "2026-10-11T10:02:00+08:00", "stop", "sess_empty", reply=""),
        _ev("dddddddddddd", "2026-10-11T10:03:00+08:00", "prompt", "sess_dup", text="重复捕获"),
        _ev("dddddddddddd", "2026-10-11T10:03:00+08:00", "prompt", "sess_dup", text="重复捕获"),
    ]
    data, persona = _mk(tmp_path, lines)
    m = ms.scan(data_dir=data, persona=persona, cited={"aaaaaaaaaaaa"})
    assert m["sessions_total"] == 2          # cited + dup（slash/empty 过滤在前）
    assert [r["session"] for r in m["ledger"]] == ["sess_dup"]
    assert m["ledger"][0]["n"] == 1          # 同 id 只计一次


def test_exclusion_suppresses(tmp_path):
    lines = [_ev("aaaaaaaaaaaa", "2026-10-11T10:00:00+08:00", "prompt",
                 "sess_x1-682d-428a", text="你好")]
    row = json.dumps({"date": "2026-10-11", "session": "sess_x1-682d-428a", "reason": "寒暄"},
                     ensure_ascii=False) + "\n"
    data, persona = _mk(tmp_path, lines, exclusions=row)
    m = ms.scan(data_dir=data, persona=persona, cited=set())
    assert m["ledger"] == [] and m["excluded"] == 1
    # 存短前缀也能命中全 id
    row2 = json.dumps({"date": "2026-10-11", "session": "sess_x1", "reason": "前缀"},
                      ensure_ascii=False) + "\n"
    data2, persona2 = _mk(tmp_path / "b", lines, exclusions=row2)
    m2 = ms.scan(data_dir=data2, persona=persona2, cited=set())
    assert m2["excluded"] == 1
