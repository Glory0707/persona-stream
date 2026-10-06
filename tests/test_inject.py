#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collector/inject.py 回归测试（SessionStart 注入契约）。

基线（2026-09-10 规则）：
1. core/SNAPSHOT/DIGEST/protocols 全文不截断注入；people.yaml 只注入 name+brief 索引
   （完整 relation 证据原话留在文件里按需读取）
2. 输出为合法 JSON，hookSpecificOutput.hookEventName == "SessionStart"
3. 任一档案缺失/为空时优雅降级（缺的部分跳过，不炸整个注入）
"""
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("inject", ROOT / "collector" / "inject.py")
inject = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inject)


@pytest.fixture(autouse=True)
def _isolated_persona(tmp_path, monkeypatch):
    """召回/定向类用例直调 build_context/recall_section，不经过 _run——
    此前隐性依赖本仓真实 persona/ 档案，抽仓后一律隔离到 tmp 最小档案。
    用例内再 setattr 同名路径会覆盖本夹具（测试缺档降级场景不受影响）。"""
    persona = tmp_path / "persona"
    (persona / "threads").mkdir(parents=True)
    core = persona / "core.md"
    core.write_text("# demo core\n- 测试内核\n", encoding="utf-8")
    monkeypatch.setattr(inject, "CORE", str(core))
    monkeypatch.setattr(inject, "SNAPSHOT", str(persona / "SNAPSHOT.md"))
    monkeypatch.setattr(inject, "DIGEST", str(persona / "threads" / "DIGEST.md"))
    monkeypatch.setattr(inject, "PEOPLE", str(persona / "people.yaml"))
    monkeypatch.setattr(inject, "ASSETS", str(persona / "assets.yaml"))
    monkeypatch.setattr(inject, "PROTOCOLS", str(persona / "protocols.yaml"))


def _make_persona(tmp_path, files):
    persona = tmp_path / "persona" / "threads"
    persona.mkdir(parents=True, exist_ok=True)
    (tmp_path / "persona" / "beliefs").mkdir(exist_ok=True)
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def _run(tmp_path, monkeypatch):
    monkeypatch.setattr(inject, "CORE", str(tmp_path / "persona" / "core.md"))
    monkeypatch.setattr(inject, "SNAPSHOT", str(tmp_path / "persona" / "SNAPSHOT.md"))
    monkeypatch.setattr(inject, "DIGEST", str(tmp_path / "persona" / "threads" / "DIGEST.md"))
    monkeypatch.setattr(inject, "PEOPLE", str(tmp_path / "persona" / "people.yaml"))
    monkeypatch.setattr(inject, "ASSETS", str(tmp_path / "persona" / "assets.yaml"))
    monkeypatch.setattr(inject, "PROTOCOLS", str(tmp_path / "persona" / "protocols.yaml"))
    inject.main()


LONG_RELATION = "这是一段只应存在于文件里、不应被注入的超长关系细节" * 20


def _persona_files():
    return {
        "persona/core.md": "# 内核\n- 做减法",
        "persona/SNAPSHOT.md": "# 速览\n- 深夜型",
        "persona/threads/DIGEST.md": "# 线头\n- [open] 毕设",
        "persona/people.yaml": (
            "people:\n"
            "  - name: 李念念\n"
            "    brief: 女友·化拔学姐——危机副驾+申请参谋\n"
            "    relation: " + LONG_RELATION + "\n"
            "  - name: 妈 / 爸\n"
            "    brief: 家人——妈为主频道\n"
            "    relation: r2\n"),
        "persona/protocols.yaml": "protocols:\n  - id: adversarial-review",
        "persona/assets.yaml": (
            "assets:\n"
            "  - id: zhengming\n"
            "    path: D:\\科研\\其他\\证明材料\n"
            "    brief: 证书证明材料库——填表要证明件先来这里\n"
            "    evidence: [5af088ee99a4]\n"),
    }


def test_full_injection_contains_all_layers(tmp_path, monkeypatch, capsys):
    _make_persona(tmp_path, _persona_files())
    _run(tmp_path, monkeypatch)
    out = json.loads(capsys.readouterr().out)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    for marker in ("人格内核", "人格速览", "未闭合线头", "人物索引", "资料地图", "口令协议"):
        assert marker in ctx
    assert "做减法" in ctx and "adversarial-review" in ctx


def test_protocols_evidence_stripped_from_injection(tmp_path, monkeypatch, capsys):
    """口令协议只注入 when/spec，evidence 的 12-hex 事件 id 不进注入文本。

    回归（2026-10-06）：strip() 已吃掉缩进，旧剥离条件 startswith("  evidence:")
    恒 False，evidence id 实际一直跟着注入出去。"""
    files = _persona_files()
    files["persona/protocols.yaml"] = (
        "protocols:\n"
        "  - id: kickoff\n"
        "    when: 开工\n"
        "    spec: 过一遍今日三件事再动手\n"
        "    evidence: [5af088ee99a4]\n")
    _make_persona(tmp_path, files)
    _run(tmp_path, monkeypatch)
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "开工" in ctx and "过一遍今日三件事再动手" in ctx
    assert "5af088ee99a4" not in ctx


def test_people_injected_as_brief_index_only(tmp_path, monkeypatch, capsys):
    _make_persona(tmp_path, _persona_files())
    _run(tmp_path, monkeypatch)
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    # 名单全、brief 在
    assert "李念念" in ctx and "危机副驾+申请参谋" in ctx and "妈 / 爸" in ctx
    # 完整 relation 不进注入，但注入文本要指引按需读取完整文件
    assert LONG_RELATION not in ctx and "people.yaml" in ctx


def test_people_index_missing_brief_degrades(tmp_path, monkeypatch, capsys):
    files = _persona_files()
    files["persona/people.yaml"] = "people:\n  - name: 无简介者\n    relation: r\n"
    _make_persona(tmp_path, files)
    _run(tmp_path, monkeypatch)
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "无简介者" in ctx  # 缺 brief 时只出名字，不炸


def test_assets_index_path_and_brief(tmp_path, monkeypatch, capsys):
    files = _persona_files()
    files["persona/assets.yaml"] = (
        "assets:\n"
        "  - id: a1\n"
        "    path: D:\\x\\材料\n"
        "    evidence: [e1]\n"  # 缺 brief → 降级出 path，不炸
    )
    _make_persona(tmp_path, files)
    _run(tmp_path, monkeypatch)
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "资料地图" in ctx and "D:\\x\\材料" in ctx and "（详见文件）" in ctx


def test_no_truncation(tmp_path, monkeypatch, capsys):
    big = "长" * 50000  # 超过任何旧截断限制
    files = _persona_files()
    files["persona/core.md"] = "# 内核\n" + big
    _make_persona(tmp_path, files)
    _run(tmp_path, monkeypatch)
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert big in ctx and "已截断" not in ctx


def test_graceful_when_files_missing(tmp_path, monkeypatch, capsys):
    (tmp_path / "persona" / "threads").mkdir(parents=True, exist_ok=True)
    (tmp_path / "persona" / "core.md").write_text("# 内核\n- 底线", encoding="utf-8")
    _run(tmp_path, monkeypatch)
    out = json.loads(capsys.readouterr().out)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "底线" in ctx and "人格速览" not in ctx


def test_empty_core_outputs_empty(tmp_path, monkeypatch, capsys):
    (tmp_path / "persona").mkdir(exist_ok=True)
    (tmp_path / "persona" / "core.md").write_text("", encoding="utf-8")
    _run(tmp_path, monkeypatch)
    # core 为空 → main 内 read_all 抛 ValueError 被 main 的调用方（__main__ 包装）吞掉
    # 但 main() 本身不捕获 → 这里期望异常或空输出均可，只要不是半截 JSON
    captured = capsys.readouterr().out
    if captured.strip():
        json.loads(captured)  # 若有输出必须是合法 JSON


# ---------- 【记忆召回】（2026-10-05，审查报告 §7①） ----------

import sys as _sys
_eval_dir = str(ROOT / "eval")
if _eval_dir not in _sys.path:
    _sys.path.insert(0, _eval_dir)
import build_index as _build_index  # noqa: E402

def _patch_build_index(monkeypatch):
    """inject 懒加载 `import build_index`——把补丁实例注册进 sys.modules 才会被它拿到
    （两侧是不同 module 对象，直接 setattr(_build_index,...) patch 不到 inject 用的那份）。"""
    monkeypatch.setitem(_sys.modules, "build_index", _build_index)


def _thread_yaml(cwd):
    return (
        "# 线头：demo-app\nid: demo-app\nstatus: open\nopened: \"2026-10-01\"\n"
        "last_seen: \"2026-10-05\"\ndigest: 'demo 线头'\ntopic: demo\n"
        "detail: |-\n  【10-1：x】y\n"
        "evidence: []\npaths: ['%s']\n" % str(cwd)  # YAML 单引号标量：反斜杠是字面量，不转义
    )


def _recall_env(tmp_path, monkeypatch):
    """tmp 事件库（2 prompt + 1 posttooluse 含 demo 词）+ 匹配 cwd 的 open 线头。"""
    cwd = tmp_path / "lab"
    cwd.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    lines = [
        json.dumps({"id": "aaaaaaaaaaaa", "ts": "2026-10-05T10:00:00+08:00",
                    "type": "prompt", "text": "demo 项目第一步：搭骨架"}, ensure_ascii=False),
        json.dumps({"id": "bbbbbbbbbbbb", "ts": "2026-10-04T10:00:00+08:00",
                    "type": "posttooluse", "payload": {"tool_name": "Bash",
                                                       "tool_input": {"command": "demo build"}}},
                   ensure_ascii=False),
        json.dumps({"id": "cccccccccccc", "ts": "2026-10-03T10:00:00+08:00",
                    "type": "prompt", "text": "demo 第二步：写注入"}, ensure_ascii=False),
    ]
    (data / "events-20261005.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    db = str(tmp_path / "data" / "index" / "events.db")  # 生产布局 <data>/index/，召回增量构建靠 dirname² 推导 data
    _build_index.build(data_dir=str(data), db=db, quiet=True)
    monkeypatch.setattr(_build_index, "DB_PATH", db)
    _patch_build_index(monkeypatch)
    threads = tmp_path / "threads"
    threads.mkdir()
    (threads / "demo-app.yaml").write_text(_thread_yaml(cwd), encoding="utf-8")
    monkeypatch.setattr(inject, "THREADS_DIR", str(threads))
    return cwd


def test_recall_injects_recent_prompts_not_tool_payloads(tmp_path, monkeypatch):
    cwd = _recall_env(tmp_path, monkeypatch)
    ctx = inject.build_context(cwd=str(cwd))
    assert "【记忆召回】" in ctx
    assert "demo 项目第一步：搭骨架" in ctx
    rec = [p for p in ctx.split("\n\n") if p.startswith("【记忆召回】")][0]
    assert "Bash" not in rec  # posttooluse payload 不进召回
    lines = [l for l in rec.splitlines() if l.startswith("- ")]
    assert lines and lines[0].startswith("- 10-05")  # 最近优先（DESC）


def test_recall_silent_when_db_missing(tmp_path, monkeypatch):
    cwd = tmp_path / "lab"
    cwd.mkdir()
    monkeypatch.setattr(_build_index, "DB_PATH", str(tmp_path / "none.db"))
    _patch_build_index(monkeypatch)
    ctx = inject.build_context(cwd=str(cwd))
    # 索引缺失 → 小节整体省略（FOOTER 提及名字不算，认完整小节头）
    assert "【记忆召回】（按当前目录自动检索" not in ctx  # 索引缺失 → 小节整体省略，不炸注入


def test_recall_silent_when_no_terms(tmp_path, monkeypatch):
    cwd = tmp_path / "ab"  # basename 2 字 < trigram 下限，无线头命中 → 无词可查
    cwd.mkdir()
    monkeypatch.setattr(_build_index, "DB_PATH", str(tmp_path / "x" / "none.db"))
    _patch_build_index(monkeypatch)
    assert inject.build_context(cwd=str(cwd)) is not None


def test_recall_filters_automation_noise_and_secrets(tmp_path, monkeypatch):
    """样板文（自动化任务 prompt/system-reminder/夜间任务）与密钥残留不进召回。"""
    cwd = tmp_path / "lab"
    cwd.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    lines = [
        json.dumps({"id": "aaaaaaaaaaaa", "ts": "2026-10-06T10:00:00+08:00", "type": "prompt",
                    "text": "demo 正常记忆：跑通管线"}, ensure_ascii=False),
        json.dumps({"id": "bbbbbbbbbbbb", "ts": "2026-10-06T10:01:00+08:00", "type": "prompt",
                    "text": "这是 persona-stream 无人值守夜间蒸馏任务，工作目录 /home/demo/persona-home"},
                   ensure_ascii=False),
        json.dumps({"id": "cccccccccccc", "ts": "2026-10-06T10:02:00+08:00", "type": "prompt",
                    "text": "demo 泄漏测试 sk-Zx9Lm2Pq7Rv4Tn8Wk3Yc6BhF0Ds5Ja"}, ensure_ascii=False),
        json.dumps({"id": "dddddddddddd", "ts": "2026-10-06T10:03:00+08:00", "type": "prompt",
                    "text": "<system-reminder>Continue working toward the active session goal.</system-reminder>"},
                   ensure_ascii=False),
    ]
    (data / "events-20261006.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    db = str(tmp_path / "data" / "index" / "events.db")  # 生产布局 <data>/index/，召回增量构建靠 dirname² 推导 data
    _build_index.build(data_dir=str(data), db=db, quiet=True)
    monkeypatch.setattr(_build_index, "DB_PATH", db)
    _patch_build_index(monkeypatch)
    threads = tmp_path / "threads"
    threads.mkdir()
    (threads / "demo-app.yaml").write_text(_thread_yaml(cwd), encoding="utf-8")
    monkeypatch.setattr(inject, "THREADS_DIR", str(threads))
    ctx = inject.build_context(cwd=str(cwd))
    assert "【记忆召回】（按当前目录自动检索" in ctx
    assert "正常记忆：跑通管线" in ctx
    for bad in ("夜间蒸馏任务", "sk-Zx9Lm2Pq7Rv4Tn8Wk3Yc6BhF0Ds5Ja", "system-reminder",
                "Continue working toward"):
        assert bad not in ctx


def test_recall_builds_incrementally_when_index_stale(tmp_path, monkeypatch):
    """索引滞后于事件文件时，召回前先增量构建（否则当天事件要等夜间索引）。"""
    cwd = tmp_path / "lab"
    cwd.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    ev = tmp_path / "data" / "events-20261006.jsonl"
    ev.parent.mkdir(parents=True, exist_ok=True)
    ev.write_text(json.dumps({"id": "aaaaaaaaaaaa", "ts": "2026-10-06T09:00:00+08:00",
                              "type": "prompt", "text": "demo 旧事件"},
                             ensure_ascii=False) + "\n", encoding="utf-8")
    db = str(tmp_path / "data" / "index" / "events.db")  # 生产布局 <data>/index/，召回增量构建靠 dirname² 推导 data
    _build_index.build(data_dir=str(data), db=db, quiet=True)
    _past = __import__("time").time() - 30
    os.utime(db, (_past, _past))  # 各平台 mtime 粒度不一，连跑时建索引与追加事件可能落进同一时钟刻度被判"新鲜"；显式把索引拨回过去
    with open(ev, "a", encoding="utf-8") as f:  # 索引之后新增的事件（夜间索引前不可见）
        f.write(json.dumps({"id": "bbbbbbbbbbbb", "ts": "2026-10-06T10:00:00+08:00",
                            "type": "prompt", "text": "demo 新事件刚发生"},
                           ensure_ascii=False) + "\n")
    monkeypatch.setattr(_build_index, "DB_PATH", db)
    _patch_build_index(monkeypatch)
    threads = tmp_path / "threads"
    threads.mkdir()
    (threads / "demo-app.yaml").write_text(_thread_yaml(cwd), encoding="utf-8")
    monkeypatch.setattr(inject, "THREADS_DIR", str(threads))
    ctx = inject.build_context(cwd=str(cwd))
    assert "demo 新事件刚发生" in ctx  # 召回内增量构建补上了


def test_recall_skips_build_when_index_fresh(tmp_path, monkeypatch):
    """索引已覆盖全部事件（mtime 新于所有事件文件）→ 不再跑增量构建（热路径打磨）。"""
    cwd = tmp_path / "lab"
    cwd.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    (data / "events-20261006.jsonl").write_text(
        json.dumps({"id": "aaaaaaaaaaaa", "ts": "2026-10-06T09:00:00+08:00",
                    "type": "prompt", "text": "demo 新鲜度"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    db = str(tmp_path / "data" / "index" / "events.db")
    _build_index.build(data_dir=str(data), db=db, quiet=True)
    _t = __import__("time").time()
    os.utime(str(data / "events-20261006.jsonl"), (_t - 5, _t - 5))  # 事件显式拨回过去
    os.utime(db, (_t - 2, _t - 2))  # 索引居中：事件 < 索引（新鲜）< 追加事件（触发构建）——
    # 9p/挂载盘 mtime 粒度粗，靠真实写入次序会撞进同一时钟刻度
    monkeypatch.setattr(_build_index, "DB_PATH", db)
    _patch_build_index(monkeypatch)
    threads = tmp_path / "threads"
    threads.mkdir()
    (threads / "demo-app.yaml").write_text(_thread_yaml(cwd), encoding="utf-8")
    monkeypatch.setattr(inject, "THREADS_DIR", str(threads))

    calls = []
    real_build = _build_index.build
    monkeypatch.setattr(_build_index, "build",
                        lambda *a, **kw: calls.append(1) or real_build(*a, **kw))
    inject.recall_section(str(cwd))  # 先跑一次把索引 mtime 刷到事件之后
    calls.clear()
    inject.recall_section(str(cwd))
    assert calls == []  # 索引新鲜 → 零构建
    with open(data / "events-20261006.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": "bbbbbbbbbbbb", "ts": "2026-10-06T10:00:00+08:00",
                            "type": "prompt", "text": "demo 追加事件"}, ensure_ascii=False) + "\n")
    inject.recall_section(str(cwd))
    assert calls == [1]  # 事件比索引新 → 恰好构建一次
