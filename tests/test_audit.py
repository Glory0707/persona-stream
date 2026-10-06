#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval/audit_persona.py 回归测试：确保自检器能真正抓出各类档案损坏。

用临时 persona 目录构造已知缺陷，验证 audit 报对应 ERROR/WARN。
运行：pytest tests/ -q
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_audit(tmp_root):
    """以 tmp_root 为项目根加载 audit 模块（隔离真实档案）"""
    spec = importlib.util.spec_from_file_location("audit_p", ROOT / "eval" / "audit_persona.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = str(tmp_root)
    mod.PERSONA = str(tmp_root / "persona")
    mod.errors, mod.warns = [], []
    return mod


@pytest.fixture()
def proj(tmp_path):
    (tmp_path / "persona" / "threads").mkdir(parents=True)
    (tmp_path / "persona" / "beliefs").mkdir()
    (tmp_path / "eval").mkdir()
    (tmp_path / "persona" / "people.yaml").write_text(
        "people:\n  - name: 甲\n    brief: 测试人物\n    relation: r\n    source: s\n    confidence: confirmed\n",
        encoding="utf-8")
    (tmp_path / "persona" / "threads" / "DIGEST.md").write_text(
        "# 线头摘要\n\n- [open] 短线头（09-09）\n", encoding="utf-8")
    (tmp_path / "persona" / "core.md").write_text(
        "# 内核\n\n<!-- AUTO:BEGIN -->\n自动区\n<!-- AUTO:END -->\n", encoding="utf-8")
    (tmp_path / "eval" / "kpi.jsonl").write_text(
        json.dumps({"date": "2026-09-09", "kind": "nightly", "corrections": 1,
                    "signals": 10, "rate": 0.1, "pruned": 2}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return tmp_path


def test_detects_duplicate_person(proj):
    (proj / "persona" / "people.yaml").write_text(
        "people:\n  - name: 甲\n    brief: b\n    relation: a\n  - name: 甲 / 乙\n    brief: b\n    relation: b\n",
        encoding="utf-8")
    a = load_audit(proj)
    a.check_people()
    assert any("人名重复" in e and "甲" in e for e in a.errors)


def test_detects_pointer_only_entry(proj):
    (proj / "persona" / "people.yaml").write_text(
        "people:\n  - name: 丙群像\n    brief: b\n    relation: QQ 朋友圈——已各自独立建档（见上方条目）\n",
        encoding="utf-8")
    a = load_audit(proj)
    a.check_people()
    assert any("冗余指针条目" in e for e in a.errors)


def test_detects_missing_brief_and_oversize(proj):
    (proj / "persona" / "people.yaml").write_text(
        "people:\n  - name: 甲\n    relation: r\n  - name: 乙\n    brief: " + "长" * 170 + "\n    relation: r\n",
        encoding="utf-8")
    a = load_audit(proj)
    a.check_people()
    assert any("缺 brief" in e and "甲" in e for e in a.errors)
    assert any("brief 过长" in w and "乙" in w for w in a.warns)


def test_detects_index_budget_overrun(proj):
    rows = "".join(
        "  - name: 人%d\n    brief: %s\n    relation: r\n" % (i, "字" * 150) for i in range(31))
    (proj / "persona" / "people.yaml").write_text("people:\n" + rows, encoding="utf-8")
    a = load_audit(proj)
    a.check_people()
    assert any("注入索引" in w and "4500" in w for w in a.warns)


def test_detects_thread_contract_violations(proj):
    (proj / "persona" / "threads" / "t_open.yaml").write_text(
        'id: t_open\nstatus: open\nopened: "2026-09-01"\ntopic: x\n', encoding="utf-8")
    (proj / "persona" / "threads" / "t_closed.yaml").write_text(
        'id: t_closed\nstatus: closed\nopened: "2026-09-01"\ntopic: y\n', encoding="utf-8")
    (proj / "persona" / "threads" / "t_closed2.yaml").write_text(
        'id: t_closed2\nstatus: closed\nopened: "2026-09-01"\nclosed: "2026-09-02"\ntopic: z\n',
        encoding="utf-8")
    a = load_audit(proj)
    a.check_threads()
    assert any("缺 last_seen" in e for e in a.errors)
    assert any("缺 closed 日期" in e and "t_closed.yaml" in e for e in a.errors)
    assert any("缺 closure" in e and "t_closed2.yaml" in e for e in a.errors)


def test_detects_belief_version_mismatch(proj):
    (proj / "persona" / "beliefs" / "b.yaml").write_text(
        'topic: t\nversions:\n  - v: 1\n    date: "2026-09-01"\n    claim: c\ncurrent: 3\n',
        encoding="utf-8")
    a = load_audit(proj)
    a.check_beliefs()
    assert any("current=3" in e for e in a.errors)


def test_detects_broken_yaml(proj):
    (proj / "persona" / "beliefs" / "bad.yaml").write_text(
        'topic: t\ntension: ["含"引号"导致提前闭合", "第二条"]\n', encoding="utf-8")
    a = load_audit(proj)
    a.check_yaml()
    assert any("YAML 解析失败" in e for e in a.errors)


def test_detects_pruned_stagnation(proj):
    rows = [{"date": "2026-09-%02d" % (i + 1), "kind": "nightly", "corrections": 0,
             "signals": 10, "rate": 0.0, "pruned": 0} for i in range(8)]
    (proj / "eval" / "kpi.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    a = load_audit(proj)
    a.check_kpi()
    assert any("减法失效" in e for e in a.errors)


def test_detects_hard_secret(proj):
    (proj / "persona" / "leak.md").write_text(
        "key: sk-abcdefghijklmnopqrstuvwx\n", encoding="utf-8")
    a = load_audit(proj)
    a.check_privacy()
    assert any("API key" in e for e in a.errors)


def test_detects_kpi_malformed_lines(proj):
    (proj / "eval" / "kpi.jsonl").write_text(
        '{"date": "2026-09-08", "kind": "nightly", "signals": 5, "pruned": 1}\n'
        '{not json at all\n'
        '{"date": "2026-09-09", "signals": 5, "pruned": 1}\n',
        encoding="utf-8")
    a = load_audit(proj)
    a.check_kpi()
    assert any("第 2 行不是合法 JSON" in e for e in a.errors)
    assert any("缺字段 kind" in e for e in a.errors)


def test_detects_kpi_rate_mismatch_and_duplicate(proj):
    rows = [
        {"date": "2026-09-08", "kind": "nightly", "corrections": 5, "signals": 10,
         "rate": 0.01, "pruned": 1},
        {"date": "2026-09-08", "kind": "nightly", "corrections": 0, "signals": 10,
         "rate": 0.0, "pruned": 1},
    ]
    (proj / "eval" / "kpi.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    a = load_audit(proj)
    a.check_kpi()
    assert any("rate=" in w and "不符" in w for w in a.warns)
    assert any("重复折叠" in e for e in a.errors)


def test_secret_scan_helper_catches_real_shapes():
    a = load_audit(ROOT)
    assert a.scan_text_for_secrets("x", '"apiKey": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig1234567")')
    assert a.scan_text_for_secrets("x", '"apiKey": "abc123def456abc123def456abc123de.ns2aiiHhdj50rrOR")')
    # 2026-09-10：原夹具 ark-9f196e8e-… 被 GitHub push protection 按真实火山 key 拦截，换合成形态
    assert a.scan_text_for_secrets("x", "ark-ffeeddccbbaa0011223344556677889900ff")
    assert a.scan_text_for_secrets("x", "nothing secret here") == []


def test_repo_secret_scan_flags_tracked_file(proj, monkeypatch):
    # 泄漏样本动态拼串：本文件自身也要过入库扫描，字符串不能以完整字面量出现、不能进豁免名单
    leak_token = "sk-" + "Qq7Wm3Rt8Yu5Xn2Bk9Ce0Dh4Fg6Jj"
    leak = proj / ".zcode" / "config.json"
    leak.parent.mkdir()
    leak.write_text('{"apiKey": "%s"}' % leak_token, encoding="utf-8")
    fixture = proj / "tests" / "f.py"
    fixture.parent.mkdir()
    fixture.write_text('x = "sk-Zx9Lm2Pq7Rv4Tn8Wk3Yc6BhF0Ds5Ja"', encoding="utf-8")
    a = load_audit(proj)
    monkeypatch.setattr(a, "iter_git_tracked", lambda: [str(leak), str(fixture)])
    a.check_repo_secrets()
    assert any(".zcode/config.json" in e for e in a.errors)
    assert not any("tests" in e for e in a.errors)  # tests/ 不再整体跳过，但其合成夹具被豁免


def test_repo_secret_scan_ignores_synthetic_fixtures(proj, monkeypatch):
    # eval/ 下的自检器自身携带合成夹具字符串（sk-abc…），不得误报
    f = proj / "eval" / "check_events.py"
    f.parent.mkdir(exist_ok=True)
    f.write_text('SYNTH = ("sk-abcdefghijklmnopqrstuvwx",\n'
                 '          "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig1234567")\n'
                 'PAT = r"eyJ[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}"\n', encoding="utf-8")
    a = load_audit(proj)
    monkeypatch.setattr(a, "iter_git_tracked", lambda: [str(f)])
    a.check_repo_secrets()
    assert a.errors == [], a.errors


def test_template_paths_not_flagged_as_broken(proj):
    (proj / "README.md").write_text("见 persona/journal/YYYY-MM.md 模板\n", encoding="utf-8")
    a = load_audit(proj)
    a.check_links()  # 模板占位路径（YYYY-MM）不应报断链
    assert not any("YYYY" in w for w in a.warns)


def test_digest_contract(proj):
    (proj / "persona" / "threads" / "DIGEST.md").write_text(
        "# x\n\n- [open] " + "甲" * 41 + "\n", encoding="utf-8")
    a = load_audit(proj)
    a.check_digest_and_core()
    assert any("超 40 字" in e for e in a.errors)

    (proj / "persona" / "threads" / "DIGEST.md").write_text(
        "\n".join("- [open] 线头%d" % i for i in range(9)), encoding="utf-8")
    a2 = load_audit(proj)
    a2.check_digest_and_core()
    assert any("上限 8" in e for e in a2.errors)

    (proj / "persona" / "threads" / "DIGEST.md").write_text(
        "- [closed] 已闭合的旧线头\n", encoding="utf-8")
    a3 = load_audit(proj)
    a3.check_digest_and_core()
    assert any("closed 条目" in w for w in a3.warns)

    (proj / "persona" / "threads" / "DIGEST.md").unlink()
    a4 = load_audit(proj)
    a4.check_digest_and_core()
    assert any("DIGEST.md 缺失" in w for w in a4.warns)


def test_clean_project_passes(proj):
    (proj / "persona" / "threads" / "ok.yaml").write_text(
        'id: ok\nstatus: closed\nopened: "2026-09-01"\nclosed: "2026-09-02"\nclosure: 已完成\ntopic: t\n',
        encoding="utf-8")
    (proj / "persona" / "beliefs" / "ok.yaml").write_text(
        'topic: t\nversions:\n  - v: 1\n    date: "2026-09-01"\n    claim: c\ncurrent: 1\n',
        encoding="utf-8")
    a = load_audit(proj)
    for fn in (a.check_people, a.check_threads, a.check_beliefs, a.check_yaml,
               a.check_privacy, a.check_kpi, a.check_repo_secrets,
               a.check_digest_and_core):
        fn()
    assert a.errors == [], a.errors


def test_assets_contract(proj):
    (proj / "persona" / "assets.yaml").write_text(
        "assets:\n"
        "  - id: a1\n"
        "    path: D:/x/证明材料\n"
        "    brief: 证书库——填表先来\n"
        "    evidence: [e1]\n"
        "  - id: a1\n"
        "    path: D:/y\n"
        "    brief: 重复 id 且缺 evidence\n",
        encoding="utf-8")
    a = load_audit(proj)
    a.check_assets()
    assert any("id 重复" in e for e in a.errors)
    assert any("缺 evidence" in e for e in a.errors)
    # 干净条目不再追加错误
    assert not any("D:" in e for e in a.errors), a.errors


def test_assets_missing_and_block_scalar_brief(proj):
    m = load_audit(proj)
    m.check_assets()
    assert any("assets.yaml 缺失" in w for w in m.warns)

    (proj / "persona" / "assets.yaml").write_text(
        "assets:\n  - id: a1\n    path: D:/x\n"
        "    brief: |\n      块标量 brief 不允许\n    evidence: [e1]\n",
        encoding="utf-8")
    a2 = load_audit(proj)
    a2.check_assets()
    assert any("块标量" in e for e in a2.errors)


# ---------- dormant 线头契约（2026-10-05，审查报告 §7③ 遗忘/衰减） ----------

def test_dormant_thread_contract(proj):
    good = ("# 线头：old\nid: old-thing\nstatus: dormant\nopened: \"2026-08-01\"\n"
            "last_seen: \"2026-09-01\"\ndormant: \"2026-10-05\"\ndigest: '休眠摘要'\n"
            "topic: t\ndetail: |-\n  【9-1：x】y\nevidence: []\n")
    (proj / "persona" / "threads" / "old.yaml").write_text(good, encoding="utf-8")
    a = load_audit(proj)
    a.check_threads()
    assert not any("old.yaml" in e for e in a.errors)  # dormant 不按 closed 契约检查、不进 open 计数
    bad = good.replace('dormant: "2026-10-05"\n', "")
    (proj / "persona" / "threads" / "old.yaml").write_text(bad, encoding="utf-8")
    a = load_audit(proj)
    a.check_threads()
    assert any("dormant 线头缺 dormant 日期" in e for e in a.errors)


def test_kpi_missing_coverage_warns(proj):
    """2026-10-06 起 nightly 行必须带 coverage/backlink（metrics.py 抄入）。"""
    good = json.dumps({"date": "2026-10-06", "kind": "nightly", "corrections": 0,
                       "signals": 10, "rate": 0, "pruned": 1,
                       "coverage": 0.05, "backlink": 1.0}) + "\n"
    bad = json.dumps({"date": "2026-10-07", "kind": "nightly", "corrections": 0,
                      "signals": 10, "rate": 0, "pruned": 1}) + "\n"
    (proj / "eval" / "kpi.jsonl").write_text(good + bad, encoding="utf-8")
    a = load_audit(proj)
    a.check_kpi()
    assert any("2026-10-07" in w and "coverage/backlink" in w for w in a.warns)
    assert not any("2026-10-06" in w and "coverage/backlink" in w for w in a.warns)
