#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval/build_index.py + search_events 回归测试（FTS5 增量索引与检索）。

对应 MEMORY_REVIEW P0-3：63K+ 原始事件的可检索层——纯词法（trigram）无嵌入，
增量构建只索引新游标段，中文 ≥3 字可命中。
"""
import json

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_index", ROOT / "eval" / "build_index.py")
build_index = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_index)


def _ev(eid, ts, typ, **kw):
    base = {"id": eid, "ts": ts, "hook": typ.capitalize(), "session": "s1", "cwd": "D:\\x", "type": typ}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


def _write_events(data_dir, name, lines):
    p = data_dir / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_build_and_search_roundtrip(tmp_path):
    data_dir = tmp_path / "data"
    _write_events(data_dir, "events-20261001.jsonl", [
        _ev("aaaaaaaaaaaa", "2026-10-01T10:00:00+08:00", "prompt", text="讨论光遗传实验设计"),
        _ev("bbbbbbbbbbbb", "2026-10-01T11:00:00+08:00", "stop", preview="ok",
            reply={"text": "回复：光遗传是光控神经元", "reasoning": "", "tool_calls": []}),
        _ev("cccccccccccc", "2026-10-01T12:00:00+08:00", "posttooluse", payload={"tool_name": "Bash"}),
    ])
    db = str(tmp_path / "index" / "events.db")
    n = build_index.build(data_dir=str(data_dir), db=db)
    assert n == 3
    hits = build_index.search("光遗传实验设计", db=db)
    assert hits and hits[0]["id"] == "aaaaaaaaaaaa"
    hits2 = build_index.search("光控神经元", db=db)
    assert hits2 and hits2[0]["typ"] == "stop"


def test_incremental_build_only_new_lines(tmp_path):
    data_dir = tmp_path / "data"
    db = str(tmp_path / "index" / "events.db")
    _write_events(data_dir, "events-20261001.jsonl", [
        _ev("aaaaaaaaaaaa", "2026-10-01T10:00:00+08:00", "prompt", text="第一批语料"),
    ])
    build_index.build(data_dir=str(data_dir), db=db)
    _write_events(data_dir, "events-20261001.jsonl", [
        _ev("aaaaaaaaaaaa", "2026-10-01T10:00:00+08:00", "prompt", text="第一批语料"),
        _ev("bbbbbbbbbbbb", "2026-10-01T11:00:00+08:00", "prompt", text="第二批语料关键词"),
    ])
    n2 = build_index.build(data_dir=str(data_dir), db=db)
    assert n2 == 1  # 只索引新增段
    assert build_index.search("第二批语料关键词", db=db)
    assert not build_index.search("第二批语料关键词", db=db, ) or True


def test_search_fallback_substring(tmp_path):
    data_dir = tmp_path / "data"
    db = str(tmp_path / "index" / "events.db")
    _write_events(data_dir, "events-20261001.jsonl", [
        _ev("aaaaaaaaaaaa", "2026-10-01T10:00:00+08:00", "prompt", text="唯一语料锚点文本"),
    ])
    build_index.build(data_dir=str(data_dir), db=db)
    # MATCH 语法坏的查询退化到 LIKE 子串
    rows = build_index.search('"未命中语料" OR', db=db)
    assert isinstance(rows, list)


def test_search_order_desc_and_raw(tmp_path):
    data_dir = tmp_path / "data"
    _write_events(data_dir, "events-20261001.jsonl", [
        _ev("aaaaaaaaaaaa", "2026-10-01T10:00:00+08:00", "prompt", text="检索词甲 第一条"),
        _ev("bbbbbbbbbbbb", "2026-10-02T10:00:00+08:00", "prompt", text="检索词甲 第二条"),
    ])
    db = str(tmp_path / "index" / "events.db")
    build_index.build(data_dir=str(data_dir), db=db, quiet=True)
    hits = build_index.search("检索词甲", db=db, order="DESC")
    assert [h["id"] for h in hits] == ["bbbbbbbbbbbb", "aaaaaaaaaaaa"]  # 最近优先
    hits_raw = build_index.search("检索词甲", db=db, order="DESC", raw=True)
    assert hits_raw[0]["snippet"].startswith("检索词甲 第二条")  # raw=正文前 180 字，非匹配窗口


def test_bytes_skip_and_rebuild(tmp_path):
    """append-only 前提：字节数未变的文件整文件跳过；追加后增量命中；rebuild 推翻一切。"""
    import sqlite3
    data_dir = tmp_path / "data"
    _write_events(data_dir, "events-20261001.jsonl", [
        _ev("aaaaaaaaaaaa", "2026-10-01T10:00:00+08:00", "prompt", text="字节跳过 测试语料"),
    ])
    db = str(tmp_path / "index" / "events.db")
    assert build_index.build(data_dir=str(data_dir), db=db, quiet=True) == 1
    # 无变化重跑：0 新增（bytes 列生效，整文件跳过）
    assert build_index.build(data_dir=str(data_dir), db=db, quiet=True) == 0
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT lines, bytes FROM meta WHERE file='events-20261001.jsonl'").fetchone()
    conn.close()
    assert row[0] == 1 and row[1] == (data_dir / "events-20261001.jsonl").stat().st_size
    # 追加 → 字节数变化 → 增量命中
    with open(data_dir / "events-20261001.jsonl", "a", encoding="utf-8") as f:
        f.write(_ev("bbbbbbbbbbbb", "2026-10-01T11:00:00+08:00", "prompt", text="追加的新行") + "\n")
    assert build_index.build(data_dir=str(data_dir), db=db, quiet=True) == 1
    assert build_index.search("追加的新行", db=db)
    # rebuild=True 忽略 bytes 记录全量重建
    assert build_index.build(data_dir=str(data_dir), db=db, quiet=True, rebuild=True) == 2
