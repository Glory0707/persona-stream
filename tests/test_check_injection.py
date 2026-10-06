#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval/check_injection.py 回归测试（P0-4 接收方读链验证）。

Channel Fracture 教训：写侧成功 ≠ 接收方可见。本测试保证 verify() 能抓住
DIGEST 行丢失 / digest 断链 / 协议 when 丢失 / 人名丢失 / 资料路径丢失。
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


inject = _load("inject_ci", "collector/inject.py")
check_injection = _load("check_injection_ci", "eval/check_injection.py")

THREAD = (
    "id: t1\nstatus: open\nopened: \"2026-09-01\"\nlast_seen: \"2026-10-01\"\n"
    "digest: 甲线头摘要\ntopic: 甲主题\ndetail: |-\n  ；【9-1：块】内容\n"
    "evidence: []\n"
)

FILES = {
    "persona/core.md": "# 内核\n- 做减法",
    "persona/threads/DIGEST.md": "共1条 open（上限8）\n\n- [open] 甲线头摘要（10-01）\n",
    "persona/threads/t1.yaml": THREAD,
    "persona/people.yaml": "people:\n  - name: 李念念\n    brief: 女友\n    relation: r\n",
    "persona/protocols.yaml": "protocols:\n  - id: adversarial-review\n    when: 对抗性审查\n    spec: 全链审查\n",
    "persona/assets.yaml": "assets:\n  - id: a1\n    path: D:\\科研\\证明材料\n    brief: 证书库\n    evidence: []\n",
}


@pytest.fixture(autouse=True)
def _isolated_persona(tmp_path, monkeypatch):
    """定向注入类用例只 patch THREADS_DIR 就直调 build_context——此前隐性依赖
    本仓真实 persona/ 档案，抽仓后一律先隔离到 tmp 最小档案（core 必须存在）。"""
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


@pytest.fixture()
def persona(tmp_path, monkeypatch):
    for rel, content in FILES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    monkeypatch.setattr(inject, "CORE", str(tmp_path / "persona" / "core.md"))
    monkeypatch.setattr(inject, "SNAPSHOT", str(tmp_path / "persona" / "SNAPSHOT.md"))
    monkeypatch.setattr(inject, "DIGEST", str(tmp_path / "persona" / "threads" / "DIGEST.md"))
    monkeypatch.setattr(inject, "PEOPLE", str(tmp_path / "persona" / "people.yaml"))
    monkeypatch.setattr(inject, "ASSETS", str(tmp_path / "persona" / "assets.yaml"))
    monkeypatch.setattr(inject, "PROTOCOLS", str(tmp_path / "persona" / "protocols.yaml"))
    return tmp_path / "persona"


def test_all_visible_passes(persona):
    ctx = inject.build_context(None)
    errors, warns, counts = check_injection.verify(ctx, persona=str(persona))
    assert errors == [], errors
    assert counts["threads_kept"] == 1 and counts["protocols"] == 1 and counts["people"] == 1


def test_digest_channel_lost_is_caught(persona, monkeypatch):
    # 注入通道断了（DIGEST 节整个丢失）：写侧文件完好，接收方看不见 → 必须报错
    monkeypatch.setattr(inject, "DIGEST", str(persona / "threads" / "不存在.md"))
    ctx = inject.build_context(None)
    errors, _w, _c = check_injection.verify(ctx, persona=str(persona))
    assert any("DIGEST" in e for e in errors)


def test_protocol_channel_lost_is_caught(persona, monkeypatch):
    monkeypatch.setattr(inject, "PROTOCOLS", str(persona / "protocols_丢失.yaml"))
    ctx = inject.build_context(None)
    errors, _w, _c = check_injection.verify(ctx, persona=str(persona))
    assert any("when" in e for e in errors)


def test_people_channel_lost_is_caught(persona, monkeypatch):
    monkeypatch.setattr(inject, "PEOPLE", str(persona / "people_丢失.yaml"))
    ctx = inject.build_context(None)
    errors, _w, _c = check_injection.verify(ctx, persona=str(persona))
    assert any("人名" in e for e in errors)


def test_assets_channel_lost_is_caught(persona, monkeypatch):
    monkeypatch.setattr(inject, "ASSETS", str(persona / "assets_丢失.yaml"))
    ctx = inject.build_context(None)
    errors, _w, _c = check_injection.verify(ctx, persona=str(persona))
    assert any("资料路径" in e for e in errors)


def test_project_section_cwd_targeting(tmp_path, monkeypatch):
    # P1-1 定向注入：paths 命中 cwd（含子目录）才出现【本项目线头】小节
    threads = tmp_path / "threads"
    threads.mkdir(parents=True)
    (threads / "proj.yaml").write_text(
        "id: proj\nstatus: open\nopened: \"2026-09-01\"\nlast_seen: \"2026-10-01\"\n"
        "digest: 项目线头摘要\npaths: ['D:\projX']\n"
        "detail: |-\n  ；【9-1：块】项目最近进展正文\n"
        "evidence: []\n", encoding="utf-8")
    monkeypatch.setattr(inject, "THREADS_DIR", str(threads))
    ctx = inject.build_context("D:\projX\sub")
    assert "本项目线头" in ctx and "proj" in ctx and "项目最近进展正文" in ctx
    ctx2 = inject.build_context("D:\别的目录")
    assert "本项目线头" not in ctx2


def test_paths_forward_slash_matches_windows_cwd(tmp_path, monkeypatch):
    # paths 里写正斜杠也要能命中 Windows 反斜杠 cwd（分隔符归一）
    threads = tmp_path / "threads"
    threads.mkdir(parents=True)
    (threads / "proj.yaml").write_text(
        "id: proj\nstatus: open\nopened: \"2026-09-01\"\nlast_seen: \"2026-10-01\"\n"
        "digest: 斜杠路径摘要\npaths: ['D:/projX']\n"
        "detail: |-\n  ；【9-1：块】正文\n"
        "evidence: []\n", encoding="utf-8")
    monkeypatch.setattr(inject, "THREADS_DIR", str(threads))
    ctx = inject.build_context("D:\projX")
    assert "本项目线头" in ctx and "proj" in ctx


def test_people_index_empty_name_no_cross_line(tmp_path, monkeypatch):
    # 空 name 行不许让 \s 跨行把 brief 抓成 name（正则跨行回归）
    people = tmp_path / "people.yaml"
    people.write_text(
        "people:\n  - name:\n    brief: 孤儿brief\n  - name: 王五\n    brief: 正常人\n    relation: r\n",
        encoding="utf-8")
    monkeypatch.setattr(inject, "PEOPLE", str(people))
    idx = inject.people_index()
    assert "王五" in idx and "正常人" in idx
    assert "孤儿brief" not in idx.split("王五")[0]  # 空 name 条目不产生"brief 被当成名字"的行


def test_verify_overlong_digest_reports_not_crashes(persona):
    t = persona / "threads" / "t1.yaml"
    long_digest = "超" * 40
    s = t.read_text(encoding="utf-8").replace("甲线头摘要", long_digest)
    t.write_text(s, encoding="utf-8")
    ctx = inject.build_context(None)
    errors, _w, _c = check_injection.verify(ctx, persona=str(persona))  # 不抛异常
    assert any("digest" in e for e in errors)
