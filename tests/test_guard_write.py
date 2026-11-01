#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collector/guard_write.py 回归测试（档案单写者守则，2026-10-16 立）。

规则基线：
1. persona\\ 下的写操作默认 deny（journal\\ 例外）
2. 完整性面（fold_ledger / state.json / _collect_cursor）同样 deny
3. 写令牌本体永远不可直写（防自开锁）
4. 写令牌有效期内放行（夜间蒸馏/月度挖掘运行窗口）
5. 读操作与无关路径不受影响；输入异常一律放行（不阻塞正常工作）
6. deny 输出用本机已验证的 hookSpecificOutput schema

密闭性：所有夹具路径都长在 monkeypatch 出来的临时 PROJECT 下（autouse fixture），
不依赖真实部署位置；CLI 用例通过 PERSONA_HOME 指向临时目录隔离子进程。
运行：pytest tests/ -q
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("guard_write", ROOT / "collector" / "guard_write.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)
_gate_spec = importlib.util.spec_from_file_location("write_gate", ROOT / "distiller" / "write_gate.py")
gate = importlib.util.module_from_spec(_gate_spec)
_gate_spec.loader.exec_module(gate)


@pytest.fixture(autouse=True)
def _isolated_project(tmp_path, monkeypatch):
    """把守则的 PROJECT 指到临时目录：夹具路径全部临时化，部署位置无关。"""
    monkeypatch.setattr(guard, "PROJECT", str(tmp_path))
    yield tmp_path


def _token(tmp_path, age_h=0.0, ttl_h=12.0):
    p = tmp_path / "_write_token.json"
    p.write_text(json.dumps({"started_ts": __import__("time").time() - age_h * 3600,
                             "by": "test", "ttl_h": ttl_h}), encoding="utf-8")
    return str(p)


def _write_payload(path):
    return {"hook_event_name": "PreToolUse", "tool_name": "Write",
            "session_id": "sess_other", "tool_input": {"file_path": path}}


def _bash_payload(cmd):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "session_id": "sess_other", "tool_input": {"command": cmd}}


# --- 1. persona 写操作：无令牌 deny ---
def test_persona_write_denied_without_token(tmp_path):
    proj = str(tmp_path)
    for p in (str(tmp_path / "persona" / "threads" / "some-thread.yaml"),
              "persona/beliefs/some-topic.yaml",
              r"persona\policies\ops.yaml",
              str(tmp_path / "persona" / "SNAPSHOT.md").replace("\\", "/")):
        allow, reason, target = guard.evaluate(_write_payload(p), token_path=str(tmp_path / "none.json"))
        assert allow is False, p
        assert "单写者面" in reason


# --- 2. journal 例外 ---
def test_journal_allowed(tmp_path):
    for p in (str(tmp_path / "persona" / "journal" / "2026-10.md"),
              "persona/journal/2026-09.md"):
        allow, _r, _t = guard.evaluate(_write_payload(p), token_path=str(tmp_path / "none.json"))
        assert allow is True, p


# --- 3. 完整性面 deny ---
def test_integrity_surfaces_denied(tmp_path):
    for p in (str(tmp_path / "data" / "fold_ledger.jsonl"), "data/state.json",
              str(tmp_path / "data" / "_collect_cursor.json")):
        allow, reason, _t = guard.evaluate(_write_payload(p), token_path=str(tmp_path / "none.json"))
        assert allow is False, p


# --- 4. 令牌本体：即使有写权也 deny ---
def test_token_file_never_directly_writable(tmp_path):
    tk = _token(tmp_path)
    allow, reason, _t = guard.evaluate(
        _write_payload(str(tmp_path / "data" / "_write_token.json")), token_path=tk)
    assert allow is False
    assert "write_gate" in reason


# --- 5. 令牌有效 → 放行；过期 → deny ---
def test_token_allows_then_expires(tmp_path):
    tk = _token(tmp_path, age_h=0.0)
    allow, _r, _t = guard.evaluate(_write_payload("persona/threads/x.yaml"), token_path=tk)
    assert allow is True
    tk2 = _token(tmp_path, age_h=99.0)
    allow2, _r2, _t2 = guard.evaluate(_write_payload("persona/threads/x.yaml"), token_path=tk2)
    assert allow2 is False


# --- 6. Bash：读命令放行、写命令拦截 ---
def test_bash_heuristics(tmp_path):
    nt = str(tmp_path / "none.json")
    proj = str(tmp_path)
    for cmd in ("grep -rn 测试人物 " + proj + r"/persona/threads/*.yaml",
                "ls persona/threads/", "cat persona/SNAPSHOT.md",
                "python " + proj + r"/eval/search_events.py 关键词"):
        allow, _r, _t = guard.evaluate(_bash_payload(cmd), token_path=nt)
        assert allow is True, cmd
    for cmd in ("python -c \"open('persona/threads/a.yaml','w').write('x')\"",
                "echo hi >> " + proj + r"/persona/SNAPSHOT.md",
                "sed -i 's/a/b/' persona/policies/ops.yaml",
                "Remove-Item " + proj + r"/persona/threads/x.yaml"):
        allow, _r, _t = guard.evaluate(_bash_payload(cmd), token_path=nt)
        assert allow is False, cmd


# --- 7. 无关路径与异常输入放行 ---
def test_unrelated_and_malformed_allowed(tmp_path):
    nt = str(tmp_path / "none.json")
    assert guard.evaluate(_write_payload("D:/tools/some-other-repo/README.md"), token_path=nt)[0] is True
    assert guard.evaluate(_write_payload("data/questions_pool.md"), token_path=nt)[0] is True
    assert guard.evaluate(_write_payload("data/style_pool.md"), token_path=nt)[0] is True
    assert guard.evaluate(None, token_path=nt)[0] is True
    assert guard.evaluate({}, token_path=nt)[0] is True


# --- 8. 纯函数式调用与 CLI 行为一致（deny 输出 schema；子进程经 PERSONA_HOME 隔离） ---
def test_cli_deny_schema(tmp_path):
    p = subprocess.run([sys.executable, str(ROOT / "collector" / "guard_write.py")],
                       input=json.dumps(_write_payload(str(tmp_path / "data" / "_write_token.json"))),
                       capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8",
                            "PERSONA_HOME": str(tmp_path)})
    assert p.returncode == 0
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "write_gate" in out["hookSpecificOutput"]["permissionDecisionReason"]


# --- 9. write_gate：begin/end/status 生命周期（PERSONA_HOME 指向临时目录） ---
def test_write_gate_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_HOME", str(tmp_path))
    monkeypatch.setattr(gate, "PROJECT", str(tmp_path))
    monkeypatch.setattr(gate, "TOKEN_PATH", str(tmp_path / "data" / "_write_token.json"))
    monkeypatch.setattr(gate, "LOG", str(tmp_path / "data" / "guard_write.log"))
    gate.begin("nightly-test", 12)
    tok = tmp_path / "data" / "_write_token.json"
    assert tok.exists()
    assert json.loads(tok.read_text(encoding="utf-8"))["by"] == "nightly-test"
    assert guard.token_alive(str(tok)) is True
    assert gate.status()["alive"] is True
    assert gate.end() is True
    assert not tok.exists()
    assert guard.token_alive(str(tok)) is False
    assert "unlock by=nightly-test" in (tmp_path / "data" / "guard_write.log").read_text(encoding="utf-8")
