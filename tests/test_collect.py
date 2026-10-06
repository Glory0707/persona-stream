#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collector/collect.py 回归测试（2026-09-09 全量保真规则版）。

规则基线：
1. 全量保真——任何 prompt（含寒暄/测试/纯标点）都入事件流，启发式只打标不丢弃
2. 不截断——长文本/长列表原样落盘
3. 脱敏——密钥类内容落盘前必须替换（唯一保留的正则过滤）
4. Stop 事件携带 reply（rollout 末行解析；无 rollout 时为 null）
5. 会话级防污已废除——纯噪声会话同样记录，判断交给蒸馏 LLM

运行：pytest tests/ -q
"""
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("collect", ROOT / "collector" / "collect.py")
collect = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collect)


@pytest.fixture()
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "DATA", str(tmp_path))
    return tmp_path


@pytest.fixture()
def tmp_rollout(tmp_path, monkeypatch):
    d = tmp_path / "rollout"
    d.mkdir()
    monkeypatch.setattr(collect, "ROLLOUT_DIR", str(d))
    return d


def _prompt(session, text):
    return {"session_id": session, "cwd": "D:/chat", "prompt": text}


def _events(tmp_data):
    files = list(tmp_data.glob("events-*.jsonl"))
    if not files:
        return []
    return [json.loads(l) for l in files[0].read_text(encoding="utf-8").splitlines()]


# ---------- 启发式打标（只打标，不丢弃） ----------

@pytest.mark.parametrize("text", [
    "你好", "您好！", "hello", "在吗", "test", "测试一下", "你是谁",
    "hello world", "？？？", "。！~", "早上好",
])
def test_noise_like_tagged_and_kept(tmp_data, text):
    collect.handle_prompt(_prompt("s1", text), {
        "id": "x", "ts": "t", "hook": "UserPromptSubmit", "session": "s1", "cwd": ""})
    evs = _events(tmp_data)
    assert len(evs) == 1 and "noise_like" in evs[0]["tags"]


@pytest.mark.parametrize("text", [
    "好的", "嗯", "ok", "收到", "了解", "继续", "next", "谢谢", "辛苦了",
])
def test_skip_like_tagged_and_kept(tmp_data, text):
    collect.handle_prompt(_prompt("s2", text), {
        "id": "x", "ts": "t", "hook": "UserPromptSubmit", "session": "s2", "cwd": ""})
    evs = _events(tmp_data)
    assert len(evs) == 1 and "skip_like" in evs[0]["tags"]


def test_signal_no_tags(tmp_data):
    collect.handle_prompt(_prompt("s3", "帮我写个脚本"), {
        "id": "x", "ts": "t", "hook": "UserPromptSubmit", "session": "s3", "cwd": ""})
    evs = _events(tmp_data)
    assert len(evs) == 1 and evs[0]["tags"] == []


def test_short_answer_tagged(tmp_data):
    collect.handle_prompt(_prompt("s3", "2"), {
        "id": "x", "ts": "t", "hook": "UserPromptSubmit", "session": "s3", "cwd": ""})
    assert _events(tmp_data)[0]["tags"] == ["short_answer"]


def test_mixed_prefix_signal_untagged(tmp_data):
    # 含"你好"但非纯寒暄 → 不打 noise_like
    collect.handle_prompt(_prompt("s3", "你好，帮我看看这个仓库"), {
        "id": "x", "ts": "t", "hook": "UserPromptSubmit", "session": "s3", "cwd": ""})
    assert _events(tmp_data)[0]["tags"] == []


# ---------- 全量保真（不截断） ----------

def test_long_prompt_not_truncated(tmp_data):
    text = "关键决策点" + "细节" * 3000  # ~9000 字符，超过旧 MAX_TEXT=2000
    collect.handle_prompt(_prompt("s4", text), {
        "id": "x", "ts": "t", "hook": "UserPromptSubmit", "session": "s4", "cwd": ""})
    ev = _events(tmp_data)[0]
    assert ev["text"] == text and ev["chars"] == len(text)


def test_generic_payload_full_fidelity(tmp_data):
    big_list = [{"i": i, "pad": "x" * 50} for i in range(30)]  # 超过旧 [:10]
    data = {"tool_name": "Bash", "tool_input": {"command": "ls", "out": big_list},
            "tool_result": "y" * 5000, "transcript_path": "C:/x/t.jsonl"}
    collect.handle_generic(data, {
        "id": "x", "ts": "t", "hook": "PostToolUse", "session": "s5", "cwd": ""})
    ev = _events(tmp_data)[0]
    p = ev["payload"]
    assert len(p["tool_input"]["out"]) == 30          # 列表不砍
    assert len(p["tool_result"]) == 5000              # 字符串不截
    assert p["transcript_path"] == "C:/x/t.jsonl"     # 溯源字段保留


# ---------- 脱敏（唯一保留的正则过滤） ----------

@pytest.mark.parametrize("raw, must_not_contain", [
    ("我的token=abc123def456", "abc123def456"),
    ("key是 sk-abcdefghijklmnopqrst", "sk-abcdefghijklmnopqrst"),
    # 2026-09-10 安全复查：原夹具形态逼真（GitHub push protection 按真实令牌拦截），换成明显合成形态
    ("hf_DEMOfake0000000000000000000000000000", "DEMOfake0000000000"),
    ("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456", "ghp_ABCDEFGH"),
    ("ark-ffeeddccbbaa0011223344556677889900", "ffeeddccbbaa"),
    ("Authorization: Bearer abcdefghijklmnopqrst", "abcdefghijklmnopqrst"),
    ("密码是abcd1234别忘了", "abcd1234"),
    # 2026-09-09 补的两种历史真实泄露形态（样本已合成化）
    ("粘贴 key：0123456789abcdef0123456789abcdef.DEMOfakesecret", "DEMOfakesecret"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghij", "eyJhbGci"),
])
def test_redact(raw, must_not_contain):
    assert must_not_contain not in collect.redact(raw)


def test_redact_keeps_normal_hex_strings():
    # 40 位 git sha / 普通 hex 不能误伤；只有 32hex.secret 形态才脱敏
    assert collect.redact("commit 0123456789abcdef0123456789abcdefabcdef12 ok").count("***") == 0
    assert "bmkey-***" in collect.redact("x 0123456789abcdef0123456789abcdef.DEMOfakesecret")


def test_log_err_rotates_oversized_log(tmp_data, monkeypatch):
    logp = os.path.join(str(tmp_data), "collector_err.log")
    with open(logp, "w", encoding="utf-8") as f:
        f.write("x" * (collect.ERR_LOG_MAX + 10))
    monkeypatch.setattr(collect, "ERR_LOG", logp)
    collect.log_err("fresh failure")
    assert os.path.exists(logp + ".1")       # 旧日志轮转
    assert "fresh failure" in open(logp, encoding="utf-8").read()
    collect.log_err("again")                 # 第二次不应报错
    assert os.path.getsize(logp) < 4096


def test_secret_redacted_in_event(tmp_data):
    collect.handle_prompt(_prompt("s6", "用这个token=supersecret123部署"), {
        "id": "x", "ts": "t", "hook": "UserPromptSubmit", "session": "s6", "cwd": ""})
    ev = _events(tmp_data)[0]
    assert "supersecret123" not in ev["text"] and "***" in ev["text"]


def test_append_lock_normal_write_is_valid_jsonl(tmp_data):
    collect.handle_prompt(_prompt("s7", "正常写入"), {
        "id": "x", "ts": "t", "hook": "UserPromptSubmit", "session": "s7", "cwd": ""})
    events = _events(tmp_data)
    assert len(events) == 1 and events[0]["text"] == "正常写入"


@pytest.mark.skipif(os.name != "nt",
                    reason="侧车锁是 msvcrt 语义；POSIX 走 O_APPEND 原子追加，无锁竞争可模拟")
def test_append_lock_drops_on_contention(tmp_data, monkeypatch):
    # 锁被他人持有时：超时内放弃本条，绝不写出撕裂行、绝不抛错阻塞
    monkeypatch.setattr(collect, "APPEND_LOCK_TIMEOUT", 0.05)
    import msvcrt
    collect.handle_prompt(_prompt("s8", "第一条"), {
        "id": "a", "ts": "t", "hook": "UserPromptSubmit", "session": "s8", "cwd": ""})
    dayfile = collect.day_file()
    dayp = os.path.join(str(tmp_data), dayfile)
    # 直接对 day 文件加锁（模拟并发进程持有）
    fd = os.open(dayp + ".lock", os.O_CREAT | os.O_RDWR)
    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    try:
        before = open(dayp, "rb").read()
        collect.handle_prompt(_prompt("s8", "锁住期间应被丢弃"), {
            "id": "b", "ts": "t", "hook": "UserPromptSubmit", "session": "s8", "cwd": ""})
        after = open(dayp, "rb").read()
    finally:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)
    assert before == after  # 无撕裂、无追加
    # 锁释放后恢复正常写入
    collect.handle_prompt(_prompt("s8", "恢复后写入"), {
        "id": "c", "ts": "t", "hook": "UserPromptSubmit", "session": "s8", "cwd": ""})
    texts = [e["text"] for e in _events(tmp_data)]
    assert "锁住期间应被丢弃" not in texts and "恢复后写入" in texts


# ---------- Stop 与 reply 捕获 ----------

def test_stop_has_reply_field_without_rollout(tmp_data):
    collect.handle_prompt(_prompt("s7", "帮我写个脚本"), {
        "id": "x", "ts": "t", "hook": "UserPromptSubmit", "session": "s7", "cwd": ""})
    collect.handle_stop({"stop_response": "已完成"}, {
        "id": "y", "ts": "t", "hook": "Stop", "session": "s7", "cwd": ""})
    types = [e["type"] for e in _events(tmp_data)]
    assert types == ["prompt", "stop"]
    stop = _events(tmp_data)[1]
    assert stop["reply"] is None and stop["preview"] == "已完成"


def test_pure_noise_session_stop_also_recorded(tmp_data):
    # 2026-09-09 起废除会话级防污：寒暄会话同样全程记录
    collect.handle_prompt(_prompt("s8", "你好"), {
        "id": "x", "ts": "t", "hook": "UserPromptSubmit", "session": "s8", "cwd": ""})
    collect.handle_stop({}, {
        "id": "y", "ts": "t", "hook": "Stop", "session": "s8", "cwd": ""})
    assert [e["type"] for e in _events(tmp_data)] == ["prompt", "stop"]


def test_reply_extracted_from_rollout(tmp_data, tmp_rollout, monkeypatch):
    monkeypatch.setattr(collect, "DATA", tmp_data)
    sid = "sess_test123"
    line = json.dumps({
        "type": "model_io",
        "response": {"finishReason": "stop", "text": "这是助手的回答",
                     "reasoningText": "这是思考", "toolCalls": [{"name": "Bash", "input": {"c": "ls"}}]},
    }, ensure_ascii=False)
    (tmp_rollout / ("model-io-%s.jsonl" % sid)).write_text(line + "\n", encoding="utf-8")
    collect.handle_prompt(_prompt(sid, "问题"), {
        "id": "x", "ts": "t", "hook": "UserPromptSubmit", "session": sid, "cwd": ""})
    collect.handle_stop({}, {
        "id": "y", "ts": "t", "hook": "Stop", "session": sid, "cwd": ""})
    stop = _events(tmp_data)[1]
    r = stop["reply"]
    assert r["text"] == "这是助手的回答" and r["reasoning"] == "这是思考"
    assert r["tool_calls"][0]["name"] == "Bash" and r["finish"] == "stop"


def test_reply_redacted_from_rollout(tmp_data, tmp_rollout, monkeypatch):
    monkeypatch.setattr(collect, "DATA", tmp_data)
    sid = "sess_test456"
    line = json.dumps({"type": "model_io",
                       "response": {"finishReason": "stop", "text": "密钥是 sk-abcdefghijklmnopqrst",
                                    "reasoningText": "", "toolCalls": []}}, ensure_ascii=False)
    (tmp_rollout / ("model-io-%s.jsonl" % sid)).write_text(line + "\n", encoding="utf-8")
    collect.handle_stop({}, {"id": "y", "ts": "t", "hook": "Stop", "session": sid, "cwd": ""})
    r = _events(tmp_data)[0]["reply"]
    assert "sk-abcdefghijklmnopqrst" not in r["text"] and "***" in r["text"]


def test_oversized_rollout_deferred(tmp_data, tmp_rollout, monkeypatch):
    monkeypatch.setattr(collect, "DATA", tmp_data)
    sid = "sess_big"
    monkeypatch.setattr(collect, "TAIL_WINDOW", 64)  # 强制窗口小于文件
    (tmp_rollout / ("model-io-%s.jsonl" % sid)).write_text(
        json.dumps({"type": "model_io", "response": {"text": "x" * 500}}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    collect.handle_stop({}, {"id": "y", "ts": "t", "hook": "Stop", "session": sid, "cwd": ""})
    r = _events(tmp_data)[0]["reply"]
    assert r.get("deferred") and r.get("reason") == "deferred_line_too_large"


# ---------- 健壮性 ----------

def test_empty_payload_survives(tmp_data):
    collect.handle_prompt(_prompt("s9", ""), {
        "id": "x", "ts": "t", "hook": "UserPromptSubmit", "session": "s9", "cwd": ""})
    assert _events(tmp_data) == []


def test_rollout_path_naming():
    assert collect.rollout_path("sess_abc").endswith("model-io-sess_abc.jsonl")
    assert collect.rollout_path("abc").endswith("model-io-sess_abc.jsonl")
