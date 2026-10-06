#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""distiller/foldlib.py 回归测试（受控写路径 + 折叠账本 + DIGEST 确定性再生）。

对应 MEMORY_REVIEW P0-1/P0-2：夜间折叠唯一写入口的行为契约——
写前查重、evidence 合并去重、超限报错附块清单、写后 YAML 可解析、
digest 字段→DIGEST 再生确定性、账本行级合法性与幂等判重。
"""
import json
import os

import pytest
import yaml

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("foldlib", ROOT / "distiller" / "foldlib.py")
foldlib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(foldlib)

THREAD_TPL = (
    "# 线头：t1\nid: {tid}\nstatus: {status}\nopened: \"2026-09-01\"\n"
    "last_seen: \"{last_seen}\"\ntopic: 测试主题\ndigest: {digest}\n"
    "detail: |-\n{detail}\n"
    "evidence: [{evidence}]\n"
)


def _thread(tid, status="open", last_seen="2026-10-01", detail="  ；【9-1：初块】内容甲", digest="摘要甲"):
    return THREAD_TPL.format(tid=tid, status=status, last_seen=last_seen,
                             detail=detail, digest=digest if digest is not None else "",
                             evidence="")


@pytest.fixture(autouse=True)
def _isolate_ledger(tmp_path, monkeypatch):
    """append_detail 自 2026-10-06 起自动记账——重定向真账本，防测试污染 data/。"""
    monkeypatch.setattr(foldlib, "LEDGER_PATH", str(tmp_path / "auto-ledger.jsonl"))


def _mk(tmp_path, files):
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


# ---------- append_detail ----------

def test_append_detail_writes_and_merges_evidence(tmp_path):
    f = _mk(tmp_path, {"t1.yaml": _thread("t1", detail="  ；【9-1：初块】内容甲")}) / "t1.yaml"
    added = foldlib.append_detail(str(f), "；【10-2：新块】内容乙", ["aaaaaaaaaaaa", "bbbbbbbbbbbb", "zz!!"])
    assert added == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"]  # 非法 id 被过滤
    s = f.read_text(encoding="utf-8")
    obj = yaml.safe_load(s)
    assert "【10-2：新块】内容乙" in obj["detail"]
    assert set(obj["evidence"]) >= {"aaaaaaaaaaaa", "bbbbbbbbbbbb"}
    # 块有且仅有 2 空格缩进（缩进丢失事故的回归线）
    assert "\n  ；【10-2：新块】内容乙" in s


def test_append_detail_block_exists_raises(tmp_path):
    f = _mk(tmp_path, {"t1.yaml": _thread("t1")}) / "t1.yaml"
    foldlib.append_detail(str(f), "；【10-2：新块】内容乙", [])
    with pytest.raises(foldlib.BlockExistsError):
        foldlib.append_detail(str(f), "；【10-2：新块】内容乙", [])


def test_append_detail_requires_evidence_line(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("id: x\nstatus: open\ndetail: |-\n  文本\n", encoding="utf-8")
    with pytest.raises(foldlib.FoldLibError):
        foldlib.append_detail(str(f), "；【10-2：新块】内容", [])


def test_append_detail_body_limit_raises_with_block_list(tmp_path, monkeypatch):
    monkeypatch.setattr(foldlib, "THREAD_BODY_LIMIT", 200)
    f = _mk(tmp_path, {"t1.yaml": _thread("t1")}) / "t1.yaml"
    with pytest.raises(foldlib.ThreadBodyLimitExceeded) as ei:
        foldlib.append_detail(str(f), "；【10-2：超限块】" + "长" * 300, [])
    assert any("初块" in b for b in ei.value.blocks)  # 附现有块清单


def test_append_detail_rejects_nontext_block(tmp_path):
    f = _mk(tmp_path, {"t1.yaml": _thread("t1")}) / "t1.yaml"
    with pytest.raises(foldlib.FoldLibError):
        foldlib.append_detail(str(f), "   \n  \n", [])  # 空白块拒绝


# ---------- digest 字段与 DIGEST 再生 ----------

def test_set_digest_and_regen_deterministic(tmp_path):
    d = tmp_path / "threads"
    _mk(tmp_path, {
        "threads/a.yaml": _thread("a", last_seen="2026-10-01", digest="甲最新"),
        "threads/b.yaml": _thread("b", last_seen="2026-09-20", digest="乙最旧"),
        "threads/c.yaml": _thread("c", last_seen="2026-09-25", digest="丙居中"),
        "threads/d.yaml": _thread("d", last_seen="2026-09-24", digest=None),
    })
    n, skipped, dropped = foldlib.regen_digest(threads_dir=str(d), out=str(d / "DIGEST.md"))
    assert (n, skipped, dropped) == (3, ["d"], [])
    out = (d / "DIGEST.md").read_text(encoding="utf-8")
    assert "共3条 open（上限8）" in out
    i_a, i_b = out.index("- [open] 甲最新"), out.index("- [open] 乙最旧")
    assert i_a < i_b  # last_seen 降序
    assert "- [open] 甲最新（10-01）" in out


def test_regen_digest_caps_at_8(tmp_path):
    d = tmp_path / "threads"
    files = {}
    for i in range(10):
        files["threads/t%02d.yaml" % i] = _thread(
            "t%02d" % i, last_seen="2026-09-%02d" % (i + 1), digest="线头%d号摘要" % i)
    _mk(tmp_path, files)
    n, skipped, dropped = foldlib.regen_digest(threads_dir=str(d), out=str(d / "DIGEST.md"))
    assert n == 8 and len(dropped) == 2 and not skipped


def test_regen_digest_rejects_overlong(tmp_path):
    d = tmp_path / "threads"
    _mk(tmp_path, {"threads/a.yaml": _thread("a", digest="超" * 40)})
    with pytest.raises(foldlib.DigestLineTooLong):
        foldlib.regen_digest(threads_dir=str(d), out=str(d / "DIGEST.md"))


# ---------- related / paths / 失效 ----------

def test_add_related_two_sides(tmp_path):
    _mk(tmp_path, {"threads/a.yaml": _thread("a"), "threads/b.yaml": _thread("b")})
    fa, fb = str(tmp_path / "threads" / "a.yaml"), str(tmp_path / "threads" / "b.yaml")
    assert foldlib.add_related(fa, "b", threads_dir=str(tmp_path / "threads"))
    assert foldlib.add_related(fb, "a", threads_dir=str(tmp_path / "threads"))
    assert yaml.safe_load(open(fa, encoding="utf-8"))["related"] == ["b"]
    with pytest.raises(foldlib.FoldLibError):
        foldlib.add_related(fa, "不存在的线头", threads_dir=str(tmp_path / "threads"))
    with pytest.raises(foldlib.FoldLibError):
        foldlib.add_related(fa, "a", threads_dir=str(tmp_path / "threads"))


def test_add_paths_requires_real_dir(tmp_path):
    f = _mk(tmp_path, {"t1.yaml": _thread("t1")}) / "t1.yaml"
    real = tmp_path / "proj"
    real.mkdir()
    foldlib.add_paths(str(f), [str(real)])
    assert yaml.safe_load(f.read_text(encoding="utf-8"))["paths"] == [str(real)]
    with pytest.raises(foldlib.FoldLibError):
        foldlib.add_paths(str(f), [str(tmp_path / "不存在")])


def test_invalidate_block_marker_once(tmp_path):
    f = _mk(tmp_path, {"t1.yaml": _thread("t1")}) / "t1.yaml"
    foldlib.invalidate_block(str(f), "【9-1：初块】", "2026-10-02", superseded_by="【9-9：新结论】")
    s = f.read_text(encoding="utf-8")
    assert "〔已失效 2026-10-02，由 【9-9：新结论】 取代〕" in s
    assert yaml.safe_load(s)  # 标记行不破坏 YAML
    with pytest.raises(foldlib.BlockExistsError):
        foldlib.invalidate_block(str(f), "【9-1：初块】", "2026-10-03")


def test_invalidate_rule(tmp_path):
    pol = tmp_path / "writing.yaml"
    pol.write_text(
        "rules:\n  - when: 正式文书\n    tendency: 详略克制\n    confidence: 0.9\n"
        "  - when: 随手记\n    tendency: 更简\n    confidence: 0.7\n", encoding="utf-8")
    foldlib.invalidate_rule(str(pol), "正式文书", "2026-10-02", note="被推翻")
    obj = yaml.safe_load(pol.read_text(encoding="utf-8"))
    assert obj["rules"][0]["invalidated"].startswith("2026-10-02")
    assert "invalidated" not in obj["rules"][1]
    with pytest.raises(foldlib.BlockExistsError):
        foldlib.invalidate_rule(str(pol), "正式文书", "2026-10-03")
    with pytest.raises(foldlib.FoldLibError):
        foldlib.invalidate_rule(str(pol), "查无此规则", "2026-10-03")


# ---------- 归档 ----------

def test_archive_thread_moves_and_stubs(tmp_path):
    d = tmp_path / "threads"
    body = "  ；【8-1：块】" + "史" * 3000
    _mk(tmp_path, {"threads/old.yaml": _thread(
        "old", status="closed", last_seen="2026-08-01",
        detail=body, digest=""), "threads/keep.yaml": _thread("keep")})
    dst = foldlib.archive_thread("old", threads_dir=str(d), archive_dir=str(d / "_archive"))
    assert os.path.exists(dst)
    stub = yaml.safe_load((d / "old.yaml").read_text(encoding="utf-8"))
    assert stub["status"] == "closed" and "_archive/old.yaml" in stub["detail"]
    assert stub["closure"]
    with pytest.raises(foldlib.FoldLibError):
        foldlib.archive_thread("keep", threads_dir=str(d), archive_dir=str(d / "_archive"))


# ---------- 折叠账本 ----------

def test_ledger_roundtrip(tmp_path):
    lp = str(tmp_path / "fold_ledger.jsonl")
    foldlib.ledger_append("add", "t1", "；【10-2：新块】", ["aaaaaaaaaaaa"], ledger_path=lp)
    foldlib.ledger_append("update", "policies/writing.yaml", "正式文书",
                          ["aaaaaaaaaaaa", "bbbbbbbbbbbb"], old_summary="旧", new_summary="新", ledger_path=lp)
    assert foldlib.folded_event_ids(ledger_path=lp) == {"aaaaaaaaaaaa", "bbbbbbbbbbbb"}


def test_ledger_validation(tmp_path):
    lp = str(tmp_path / "fold_ledger.jsonl")
    with pytest.raises(foldlib.LedgerError):
        foldlib.ledger_append("explode", "t", "k", [], ledger_path=lp)
    with pytest.raises(foldlib.LedgerError):
        foldlib.ledger_append("add", "t", "k", ["bad-id"], ledger_path=lp)


# ---------- 打磨轮回归（2026-10-02） ----------

def test_set_digest_closed_raises(tmp_path):
    f = _mk(tmp_path, {"t1.yaml": _thread("t1", status="closed", last_seen="2026-09-01",
                                          detail="  ；【9-1：块】内容", digest="")}) / "t1.yaml"
    with pytest.raises(foldlib.FoldLibError):
        foldlib.set_digest(str(f), "摘要")


def test_append_detail_evidence_anchor_ignores_quoted_text(tmp_path):
    # detail 内出现 "evidence: [" 字样的块：合并必须锚定顶层行，不得劫持 detail 文本
    detail = "  ；【9-1：块】\n  引用格式示例 evidence: [\"aaaaaaaaaaaa\"]"
    f = _mk(tmp_path, {"t1.yaml": _thread("t1", detail=detail)}) / "t1.yaml"
    added = foldlib.append_detail(str(f), "；【10-2：新块】正文", ["bbbbbbbbbbbb"])
    assert added == ["bbbbbbbbbbbb"]
    s = f.read_text(encoding="utf-8")
    assert s.rstrip("\n").splitlines()[-1].startswith("evidence:")  # 顶层行在文件末尾
    assert '引用格式示例 evidence: ["aaaaaaaaaaaa"]' in s  # detail 内文本原样保留


def test_archive_thread_multiline_closure(tmp_path):
    d = tmp_path / "threads"
    _mk(tmp_path, {"threads/old.yaml": _thread(
        "old", status="closed", last_seen="2026-08-01", detail="  ；【8-1：块】史" * 3, digest="")})
    # 多行块标量 closure
    p = d / "old.yaml"
    s = p.read_text(encoding="utf-8")
    s = s.replace('digest: \n', 'digest: \nclosure: |-\n  被女友说服\n  确定换方向\n')
    p.write_text(s, encoding="utf-8")
    foldlib.archive_thread("old", threads_dir=str(d), archive_dir=str(d / "_archive"))
    stub = yaml.safe_load((d / "old.yaml").read_text(encoding="utf-8"))
    assert "被女友说服" in stub["closure"] and "\n" not in stub["closure"]


def test_invalidate_rule_last_rule_no_trailing_newline(tmp_path):
    pol = tmp_path / "learn.yaml"
    pol.write_text("rules:\n  - when: 唯一规则\n    tendency: 收敛", encoding="utf-8")  # 无尾换行
    foldlib.invalidate_rule(str(pol), "唯一规则", "2026-10-02")
    obj = yaml.safe_load(pol.read_text(encoding="utf-8"))
    assert obj["rules"][0]["invalidated"].startswith("2026-10-02")


def test_ledger_threaded_appends_no_tearing(tmp_path):
    import threading
    lp = str(tmp_path / "fold_ledger.jsonl")
    def worker(w):
        for i in range(10):
            foldlib.ledger_append("add", "t%d" % w, "块", ["%s%011d" % ("abcdef012345"[w], i)],
                                  ledger_path=lp)
    ts = [threading.Thread(target=worker, args=(w,)) for w in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    rows = [json.loads(x) for x in open(lp, encoding="utf-8").read().splitlines()]
    assert len(rows) == 80 and all(set(r) >= {"date", "action", "target", "event_ids"} for r in rows)
    assert len(foldlib.folded_event_ids(ledger_path=lp)) == 80


def test_pending_counts(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    ev = data / "events-1.jsonl"
    rows = [
        {"id": "aaaaaaaaaaaa", "ts": "t", "hook": "h", "session": "s", "text": "x"},
        {"id": "bbbbbbbbbbbb", "ts": "t", "hook": "h", "session": "s", "text": "y"},
        {"id": "cccccccccccc", "ts": "t", "hook": "h", "session": "s", "text": "z"},
    ]
    ev.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (data / "state.json").write_text(json.dumps({"files": {"events-1.jsonl": 1}}), encoding="utf-8")
    lp = str(tmp_path / "fold_ledger.jsonl")
    foldlib.ledger_append("add", "t", "k", ["bbbbbbbbbbbb"], ledger_path=lp)
    monkeypatch.setattr(foldlib, "DATA", str(data))
    r = foldlib.pending(data_dir=str(data), ledger_path=lp)
    assert r == {"total": 2, "already": 1, "pending": 1}  # 游标后 2 条，其中 1 条已折


# ---------- 测试员轮：并发与特殊字符（2026-10-02） ----------

def test_concurrent_append_detail_no_lost_update(tmp_path):
    # 双写者同文件交错追加：侧车锁保证零丢失（原子替换只防撕裂，不防丢更新）
    f = _mk(tmp_path, {"t1.yaml": _thread("t1")}) / "t1.yaml"
    import threading
    def worker(w):
        for i in range(5):
            foldlib.append_detail(str(f), "；【块-%d-%d】并发正文%d%d" % (w, i, w, i),
                                  ["%012x" % (w * 10000 + i)])
    ts = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    obj = yaml.safe_load(f.read_text(encoding="utf-8"))
    detail = obj["detail"]
    assert all(("【块-%d-%d】" % (w, i)) in detail for w in range(4) for i in range(5))
    assert len(obj["evidence"]) == 20  # 4 写者 × 5 条，一个都不能丢


def test_set_digest_special_chars_roundtrip(tmp_path):
    # 半角冒号/反斜杠/单引号都不许炸 YAML（digest 值单引号包裹 + '' 转义）
    f = _mk(tmp_path, {"t1.yaml": _thread("t1")}) / "t1.yaml"
    tricky = "路径D:\\x: 用'引号'措辞"
    foldlib.set_digest(str(f), tricky)
    obj = yaml.safe_load(f.read_text(encoding="utf-8"))
    assert obj["digest"] == tricky
    foldlib.set_digest(str(f), "二次覆盖")
    assert yaml.safe_load(f.read_text(encoding="utf-8"))["digest"] == "二次覆盖"


def test_set_digest_no_last_seen_raises(tmp_path):
    f = tmp_path / "odd.yaml"
    f.write_text("id: odd\nstatus: open\ndetail: |-\n  ；【块】x\nevidence: []\n", encoding="utf-8")
    with pytest.raises(foldlib.FoldLibError):
        foldlib.set_digest(str(f), "摘要")


def test_append_detail_inserts_before_paths_when_field_order_differs(tmp_path):
    """字段序 detail→paths→evidence 时，新块必须落进 detail 标量内（10-04 surtitre 实例）。"""
    p = tmp_path / "t.yaml"
    p.write_text(
        "# 线头：t1\nid: t1\nstatus: open\nopened: \"2026-09-01\"\nlast_seen: \"2026-10-01\"\n"
        "digest: '摘要甲'\ntopic: t\ndetail: |-\n  ；【9-1：初块】内容甲\n"
        "paths: ['D:/x']\nevidence: [\"aaaaaaaaaaaa\"]\n", encoding="utf-8")
    foldlib.append_detail(str(p), "；【9-2：新块】内容乙", ["bbbbbbbbbbbb"])
    import yaml
    obj = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert "；【9-2：新块】内容乙" in obj["detail"]
    assert obj["paths"] == ["D:/x"]                      # paths 未被吞进 detail
    assert obj["evidence"] == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"]


# ---------- 遗忘/衰减：sweep_threads + touch_thread + dormant 唤醒（2026-10-05） ----------

def test_sweep_demotes_stale_only_and_ledgers(tmp_path):
    import datetime
    old = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    today = datetime.date.today().isoformat()
    d = tmp_path / "threads"
    d.mkdir()
    (d / "old.yaml").write_text(_thread("old-thing", last_seen=old), encoding="utf-8")
    (d / "new.yaml").write_text(_thread("new-thing", last_seen=today), encoding="utf-8")
    led = tmp_path / "ledger.jsonl"
    res = foldlib.sweep_threads(threads_dir=str(d), ledger_path=str(led))
    assert res == {"dormant": ["old-thing"], "closed": []}
    s_old = (d / "old.yaml").read_text(encoding="utf-8")
    assert "status: dormant" in s_old and 'dormant: "%s"' % today in s_old
    assert "status: open" in (d / "new.yaml").read_text(encoding="utf-8")
    row = json.loads(led.read_text(encoding="utf-8").splitlines()[0])
    assert row["action"] == "prune" and row["target"] == "threads/old-thing"
    # 再跑一遍：dormant 未到 60 天自动闭合线，幂等（无新动作）
    assert foldlib.sweep_threads(threads_dir=str(d), ledger_path=str(led)) == \
        {"dormant": [], "closed": []}
    assert "status: dormant" in (d / "old.yaml").read_text(encoding="utf-8")


def test_sweep_auto_closes_long_dormant(tmp_path):
    """dormant 超 60 天无活动 → 自动闭合（完整生命周期 open→dormant→closed→archive）。"""
    import datetime
    day = datetime.date.today()
    dormant_day = (day - datetime.timedelta(days=61)).isoformat()
    today = day.isoformat()
    d = tmp_path / "threads"
    d.mkdir()
    (d / "dead.yaml").write_text(
        _thread("dead-thing", status="dormant", last_seen="2026-08-01",
                digest="早已休眠"), encoding="utf-8")
    # dormant 日期字段由测试直接写（_thread 模板没有 dormant 字段）
    p = d / "dead.yaml"
    txt = p.read_text(encoding="utf-8").replace(
        'last_seen: "2026-08-01"', 'last_seen: "2026-08-01"\ndormant: "%s"' % dormant_day)
    p.write_text(txt, encoding="utf-8")
    led = tmp_path / "ledger.jsonl"
    res = foldlib.sweep_threads(threads_dir=str(d), ledger_path=str(led))
    assert res == {"dormant": [], "closed": ["dead-thing"]}
    s = p.read_text(encoding="utf-8")
    assert "status: closed" in s and 'closed: "%s"' % today in s
    assert "重开即续线" in s and "closure:" in s
    row = json.loads(led.read_text(encoding="utf-8").splitlines()[0])
    assert row["action"] == "prune" and "closed" in row["new_summary"]


def test_sweep_closes_hand_edited_dormant_with_stale_closed_field(tmp_path):
    """手改复开的线头（status:dormant + dormant 日期在，但残留旧 closed 行且无 closure）
    再闭合：closed 日期刷新、closure 补插，全程不崩（回归：closure 插入锚点）。"""
    import datetime
    today = datetime.date.today().isoformat()
    d = tmp_path / "threads"
    d.mkdir()
    p = d / "odd.yaml"
    p.write_text(
        'id: odd-thing\nstatus: dormant\nopened: "2026-08-01"\nlast_seen: "2026-08-01"\n'
        'dormant: "2026-08-05"\nclosed: "2026-09-01"\ntopic: t\n'
        'detail: |-\n  【8-1：x】y\nevidence: []\n',
        encoding="utf-8")
    res = foldlib.sweep_threads(threads_dir=str(d), ledger_path=str(tmp_path / "led.jsonl"))
    assert res == {"dormant": [], "closed": ["odd-thing"]}
    s = p.read_text(encoding="utf-8")
    assert 'closed: "%s"' % today in s and 'closed: "2026-09-01"' not in s
    assert "closure:" in s and "重开即续线" in s
    # 闭合后不在 digest_plan 里（closed 契约），audit 口径也兼容
    kept, _sk, _dr = foldlib.digest_plan(str(d))
    assert kept == []


def test_append_detail_auto_ledgers(tmp_path):
    """写盘成功自动记 add 账本行（机械纪律，2026-10-06 起）——查重失败不记。"""
    p = tmp_path / "t.yaml"
    p.write_text(_thread("t1"), encoding="utf-8")
    led = tmp_path / "ledger.jsonl"
    added = foldlib.append_detail(str(p), "；【10-6：新块】自动记账内容", ["aaaaaaaaaaaa"],
                                  ledger_path=str(led))
    assert added == ["aaaaaaaaaaaa"]
    row = json.loads(led.read_text(encoding="utf-8").splitlines()[0])
    assert row["action"] == "add" and row["event_ids"] == ["aaaaaaaaaaaa"]
    assert row["target"].endswith("t.yaml") and "自动记账内容" in row["block_key"]
    # 查重失败路径：块已存在 → BlockExistsError，不新增账本行
    import pytest
    with pytest.raises(foldlib.BlockExistsError):
        foldlib.append_detail(str(p), "；【10-6：新块】自动记账内容", [], ledger_path=str(led))
    assert len(led.read_text(encoding="utf-8").splitlines()) == 1


def test_add_related_and_paths_wake_dormant(tmp_path):
    import datetime
    today = datetime.date.today().isoformat()
    d = tmp_path / "threads"
    d.mkdir()
    a, b = d / "aaa.yaml", d / "bbb.yaml"
    a.write_text(_thread("aaa", status="dormant", last_seen="2026-08-01"), encoding="utf-8")
    b.write_text(_thread("bbb", status="dormant", last_seen="2026-08-01"), encoding="utf-8")
    (tmp_path / "proj").mkdir()
    foldlib.add_related(str(a), "bbb", threads_dir=str(d))
    foldlib.add_paths(str(b), [str(tmp_path / "proj")])
    for p in (a, b):
        s = p.read_text(encoding="utf-8")
        assert "status: open" in s and "dormant:" not in s
        assert 'last_seen: "%s"' % today in s


def test_dormant_thread_wakes_on_append_detail(tmp_path):
    import datetime
    today = datetime.date.today().isoformat()
    p = tmp_path / "old.yaml"
    p.write_text(_thread("old-thing", status="dormant", last_seen="2026-08-01"),
                 encoding="utf-8")
    foldlib.append_detail(str(p), "；【10-5：新块】唤醒内容", [])
    s = p.read_text(encoding="utf-8")
    assert "status: open" in s and "dormant:" not in s
    assert 'last_seen: "%s"' % today in s


def test_set_digest_wakes_dormant(tmp_path):
    import datetime
    today = datetime.date.today().isoformat()
    p = tmp_path / "old.yaml"
    p.write_text(_thread("old-thing", status="dormant", last_seen="2026-08-01"),
                 encoding="utf-8")
    foldlib.set_digest(str(p), "唤醒后的新摘要")
    s = p.read_text(encoding="utf-8")
    assert "status: open" in s and "digest: '唤醒后的新摘要'" in s
    assert 'last_seen: "%s"' % today in s


def test_touch_thread_updates_idempotent_wakes(tmp_path):
    import datetime
    today = datetime.date.today().isoformat()
    p = tmp_path / "t.yaml"
    p.write_text(_thread("t1", last_seen="2026-10-01"), encoding="utf-8")
    assert foldlib.touch_thread(str(p)) is True
    assert 'last_seen: "%s"' % today in p.read_text(encoding="utf-8")
    assert foldlib.touch_thread(str(p)) is False  # 已是当天，幂等
    p2 = tmp_path / "d.yaml"
    p2.write_text(_thread("t2", status="dormant", last_seen="2026-08-01"), encoding="utf-8")
    assert foldlib.touch_thread(str(p2)) is True
    s2 = p2.read_text(encoding="utf-8")
    assert "status: open" in s2 and "dormant:" not in s2


def test_touch_thread_closed_raises(tmp_path):
    import datetime
    p = tmp_path / "c.yaml"
    p.write_text(_thread("t1", status="closed", last_seen="2026-10-01"), encoding="utf-8")
    with pytest.raises(foldlib.FoldLibError):
        foldlib.touch_thread(str(p))


def test_dormant_thread_excluded_from_digest_plan(tmp_path):
    d = tmp_path / "threads"
    d.mkdir()
    (d / "a.yaml").write_text(_thread("aaa"), encoding="utf-8")
    (d / "b.yaml").write_text(_thread("bbb", status="dormant", last_seen="2026-10-02",
                                      digest="休眠线头摘要"), encoding="utf-8")
    kept, skipped, dropped = foldlib.digest_plan(str(d))
    assert [tid for _ls, _dg, tid in kept] == ["aaa"]
    assert "bbb" not in [t for _ls, _dg, t in kept + dropped]


def test_archive_stub_evidence_ignores_prose_numbers(tmp_path):
    """回归（测试员 2026-10-06）：桩 evidence 曾全文 findall 12-hex，散文里的学号
    （如 202510487008）会被当事件指针写进桩，污染 metrics 断链闸门。只收顶层 evidence 字段。"""
    d = tmp_path / "threads"
    d.mkdir()
    arc = tmp_path / "_arc"
    (d / "t2.yaml").write_text(
        "# 线头\nid: t2\nstatus: closed\nopened: \"2026-08-01\"\nclosed: \"2026-09-01\"\n"
        "closure: 测试\ntopic: 学号污染测试\n"
        "detail: |-\n  ；【8-1：x】本人学号 202510487008 与固话 02758868737 混在散文里\n"
        "evidence: [\"aaaaaaaaaaaa\"]\n", encoding="utf-8")
    foldlib.archive_thread("t2", threads_dir=str(d), archive_dir=str(arc))
    stub = (d / "t2.yaml").read_text(encoding="utf-8")
    import re
    ev = re.search(r"^evidence: \[(.*?)\]", stub, re.M).group(1)
    assert "aaaaaaaaaaaa" in ev and "202510487008" not in ev
