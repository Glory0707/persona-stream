#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collector/collect_dsh.py 回归测试（DSH 会话日志 → 事件流）。

规则基线：
1. user/message(source.kind=user) → prompt；turn/end → stop（该轮最后一条 assistant 的 text/reasoning/tool_calls）
2. 幂等——同一 generation 重跑不重复；generation 升级靠 id 集合去重
3. 坏行不阻断（记 collector_err.log），子智能体/seed 会话跳过
4. 脱敏复用 collect.py 口径（sk- 等落盘前替换）
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone

import pytest
import zstandard as zstd

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
if not (ROOT / "collector" / "collect_dsh.py").exists():
    # collect_dsh.py 是实例层模块（含 DSH 实机路径，sync_open BYTE_FILES 有意不收）：
    # 公开仓没有该文件，整文件跳过；私有仓照常执行。
    pytest.skip("collect_dsh.py 为实例层模块，公开仓无此文件", allow_module_level=True)
_spec = importlib.util.spec_from_file_location("collect_dsh", ROOT / "collector" / "collect_dsh.py")
collect_dsh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collect_dsh)

TZ = timezone(timedelta(hours=8))
T0 = int(datetime(2026, 11, 3, 12, 0, tzinfo=TZ).timestamp() * 1000)


def ev(type_, seq=None, t=None, **data):
    e = {"type": type_}
    if seq is not None:
        e["seq"] = seq
    if t is not None:
        e["time"] = t
    if data:
        e["data"] = data
    return e


def header(sid="session-test-0001", cwd="D:/proj", version=4, seeded=False, depth=0):
    return {"type": "session", "version": version, "id": sid,
            "createdAt": T0, "cwd": cwd, "isSeeded": seeded, "delegationDepth": depth}


def user(seq, mid, text, t=None):
    return ev("user/message", seq, t or T0 + seq * 1000,
              role="user", id=mid, source={"kind": "user"},
              content=[{"type": "text", "text": text}])


def turn(seq, n, t=None):
    return ev("turn/start", seq, t or T0 + seq * 1000, turn=n)


def assistant(seq, n, text, reasoning="", t=None):
    content = []
    if reasoning:
        content.append({"type": "reasoning", "text": reasoning})
    content.append({"type": "text", "text": text})
    return ev("assistant/message", seq, t or T0 + seq * 1000,
              turn=n, step=1, message={"role": "assistant", "content": content})


def tool_call(seq, n, name, args):
    return ev("tool/call", seq, T0 + seq * 1000, turn=n, step=1, name=name,
              arguments=json.dumps(args, ensure_ascii=False))


def end(seq, n, kind="completed", t=None):
    return ev("turn/end", seq, t or T0 + seq * 1000, turn=n, reason={"kind": kind})


def write_session(root, sid, events, version=4, workspace="--D-proj--"):
    name = sid if sid.startswith("session-") else "session-" + sid
    d = root / workspace / name
    d.mkdir(parents=True, exist_ok=True)
    raw = "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"
    (d / ("session.v%d.jsonl.zstd" % version)).write_bytes(
        zstd.ZstdCompressor().compress(raw.encode("utf-8")))
    return d


@pytest.fixture()
def env(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    data = tmp_path / "data"
    sessions.mkdir()
    data.mkdir()
    monkeypatch.setattr(collect_dsh, "SESSIONS_DIR", str(sessions))
    monkeypatch.setattr(collect_dsh, "DATA", str(data))
    monkeypatch.setattr(collect_dsh, "CURSOR_PATH", str(data / "_dsh_cursor.json"))
    monkeypatch.setattr(collect_dsh.zc, "DATA", str(data))
    monkeypatch.setattr(collect_dsh.zc, "ERR_LOG", str(data / "collector_err.log"))
    return sessions, data


def read_events(data):
    out = []
    for f in sorted(data.glob("events-dsh-*.jsonl")):
        out += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
    return out


def test_maps_prompt_and_stop(env):
    sessions, data = env
    write_session(sessions, "session-test-0001", [
        header(), user(1, "u1", "帮我写个脚本"), turn(2, 1),
        assistant(3, 1, "好的，先看目录", "我应该先列目录"),
        tool_call(4, 1, "bash", {"cmd": "ls"}),
        end(5, 1),
    ])
    stats = collect_dsh.collect()
    assert stats["appended"] == 2 and stats["prompts"] == 1 and stats["stops"] == 1
    evs = read_events(data)
    prompt = next(e for e in evs if e["type"] == "prompt")
    stop = next(e for e in evs if e["type"] == "stop")
    assert prompt["text"] == "帮我写个脚本" and len(prompt["id"]) == 12
    assert prompt["session"] == "session-test-0001" and prompt["cwd"] == "D:/proj"
    assert stop["reply"]["text"] == "好的，先看目录"
    assert stop["reply"]["reasoning"] == "我应该先列目录"
    assert stop["reply"]["finish"] == "completed"
    assert stop["reply"]["tool_calls"][0]["name"] == "bash"


def test_idempotent_rerun(env):
    sessions, data = env
    write_session(sessions, "session-test-0001", [
        header(), user(1, "u1", "第一问"), turn(2, 1), assistant(3, 1, "答一"), end(4, 1)])
    assert collect_dsh.collect()["appended"] == 2
    assert collect_dsh.collect()["appended"] == 0
    assert len(read_events(data)) == 2


def test_incremental_same_generation(env):
    sessions, data = env
    base = [header(), user(1, "u1", "第一问"), turn(2, 1), assistant(3, 1, "答一"), end(4, 1)]
    write_session(sessions, "session-test-0001", base)
    assert collect_dsh.collect()["appended"] == 2
    write_session(sessions, "session-test-0001", base + [
        user(5, "u2", "第二问"), turn(6, 2), assistant(7, 2, "答二"), end(8, 2)])
    assert collect_dsh.collect()["appended"] == 2
    evs = read_events(data)
    assert [e["type"] for e in evs] == ["prompt", "stop", "prompt", "stop"]
    assert {e["text"] for e in evs if e["type"] == "prompt"} == {"第一问", "第二问"}


def test_generation_upgrade_dedupes(env):
    sessions, data = env
    base = [header(), user(1, "u1", "第一问"), turn(2, 1), assistant(3, 1, "答一"), end(4, 1)]
    write_session(sessions, "session-test-0001", base, version=4)
    assert collect_dsh.collect()["appended"] == 2
    write_session(sessions, "session-test-0001", base + [
        user(5, "u2", "第二问"), turn(6, 2), assistant(7, 2, "答二"), end(8, 2)], version=5)
    assert collect_dsh.collect()["appended"] == 2
    ids = [e["id"] for e in read_events(data)]
    assert len(ids) == len(set(ids)) == 4


def test_secret_redacted(env):
    sessions, data = env
    write_session(sessions, "session-test-0001", [
        header(), user(1, "u1", "key sk-abcdefghijklmnopqrst"),
        turn(2, 1), assistant(3, 1, "收到"), end(4, 1)])
    collect_dsh.collect()
    prompt = next(e for e in read_events(data) if e["type"] == "prompt")
    assert "sk-abcdefghijklmnopqrst" not in prompt["text"]
    assert "sk-***" in prompt["text"]


def test_bad_line_tolerated(env):
    sessions, data = env
    d = write_session(sessions, "session-test-0001", [header()])
    # 追加一条坏行与一条正常用户消息
    raw = "not-a-json-line\n" + json.dumps(user(1, "u1", "还能读到"), ensure_ascii=False) + "\n"
    (d / "session.v4.jsonl.zstd").write_bytes(zstd.ZstdCompressor().compress(raw.encode("utf-8")))
    stats = collect_dsh.collect()
    assert stats["bad_lines"] >= 1 and stats["prompts"] == 1


def test_skips_delegated_and_seeded(env):
    sessions, data = env
    write_session(sessions, "session-sub", [
        header(sid="session-sub", depth=1), user(1, "u1", "子代理问"), turn(2, 1),
        assistant(3, 1, "子代理答"), end(4, 1)], workspace="--D-sub--")
    write_session(sessions, "session-seed", [
        header(sid="session-seed", seeded=True), user(1, "u1", "seed 问"), turn(2, 1),
        assistant(3, 1, "seed 答"), end(4, 1)], workspace="--D-seed--")
    stats = collect_dsh.collect()
    assert stats["appended"] == 0 and stats["skipped_sessions"] == 2
    assert read_events(data) == []


def test_aborted_turn_still_gets_stop(env):
    sessions, data = env
    write_session(sessions, "session-test-0001", [
        header(), user(1, "u1", "会失败的问题"), turn(2, 1), end(3, 1, kind="aborted")])
    stats = collect_dsh.collect()
    assert stats["stops"] == 1
    stop = next(e for e in read_events(data) if e["type"] == "stop")
    assert stop["reply"]["finish"] == "aborted" and stop["reply"]["text"] == ""
