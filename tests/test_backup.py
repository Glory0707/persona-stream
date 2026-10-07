#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval/backup.py 回归测试：备份/校验/保留期/损坏检出/跨项目快照。

跨项目目标必须用合成源（monkeypatch CROSS_TARGETS）：真实源含 9.3G surtitre 树，
曾经被原样复制进 C 盘 pytest 临时目录（单轮 27s+、多 GB 写 C 盘）。
"""
import importlib.util
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_backup(tmp_path, monkeypatch):
    os.environ["PERSONA_BACKUP_DIR"] = str(tmp_path / "backups")
    spec = importlib.util.spec_from_file_location("backup_p", ROOT / "eval" / "backup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DATA = str(tmp_path / "data")
    os.makedirs(mod.DATA, exist_ok=True)
    monkeypatch.setattr(mod, "CROSS_TARGETS", synth_cross(tmp_path))
    return mod, tmp_path / "backups"


def synth_cross(tmp_path):
    """小型合成跨项目源：sqlite 库 + 单文件 + 小树，覆盖三种 kind 的复制路径。"""
    src = tmp_path / "src"
    (src / "tree" / "sub").mkdir(parents=True)
    db = src / "fake.db"
    con = sqlite3.connect(db)
    con.execute("create table t(x)")
    con.execute("insert into t values (42)")
    con.commit()
    con.close()
    (src / "cfg.yaml").write_text("a: 1\n", encoding="utf-8")
    (src / "tree" / "note.md").write_text("hi\n", encoding="utf-8")
    (src / "tree" / "sub" / "deep.txt").write_text("deep\n", encoding="utf-8")
    return [
        ("sqlite", str(db), "fake/fake.db", None),
        ("file", str(src / "cfg.yaml"), "fake/cfg.yaml", None),
        ("tree", str(src / "tree"), "fakertree", {"__pycache__"}),
        ("sqlite", str(src / "missing.db"), "fake/missing.db", None),  # 缺失源 → 记 missing
    ]


def seed(mod, name="events-20260909.jsonl", text='{"id":"a"}\n'):
    p = Path(mod.DATA) / name
    with open(p, "a", encoding="utf-8") as f:
        f.write(text)


def test_backup_and_verify_roundtrip(tmp_path, monkeypatch):
    mod, broot = load_backup(tmp_path, monkeypatch)
    seed(mod)
    seed(mod, "events-20260908.jsonl", '{"id":"b"}\n')
    (Path(mod.DATA) / "state.json").write_text('{"updated": "t"}', encoding="utf-8")
    assert mod.run_backup() == 0
    assert mod.run_verify() == 0
    dirs = mod.backup_dirs()
    assert len(dirs) == 1
    manifest = json.load(open(os.path.join(dirs[0], "manifest.json"), encoding="utf-8"))
    assert {"events-20260909.jsonl", "events-20260908.jsonl", "state.json"} <= set(manifest["files"])
    # 跨项目三种 kind 全部到位；缺失源进 missing 不算失败
    assert {"fake/fake.db", "fake/cfg.yaml", "fakertree/note.md", "fakertree/sub/deep.txt"} \
        <= set(manifest["files"])
    assert manifest["missing"] == ["fake/missing.db"]
    con = sqlite3.connect(os.path.join(dirs[0], "fake", "fake.db"))
    assert con.execute("select x from t").fetchone()[0] == 42  # backup API 快照可读
    con.close()


def test_verify_catches_tampering(tmp_path, monkeypatch):
    mod, broot = load_backup(tmp_path, monkeypatch)
    seed(mod)
    assert mod.run_backup() == 0
    target = next(Path(broot).glob("weekly/backup-*/events-20260909.jsonl"))
    target.write_text('{"id":"TAMPERED"}\n', encoding="utf-8")
    assert mod.run_verify() == 1


def test_retention_prunes_old_generations(tmp_path, monkeypatch):
    mod, broot = load_backup(tmp_path, monkeypatch)
    seed(mod)
    for _ in range(3):
        assert mod.run_backup(keep=2) == 0  # 同秒重跑靠序号后缀区分
    assert len(mod.backup_dirs()) == 2


def test_daily_light_backup_roundtrip(tmp_path, monkeypatch):
    """--daily 日备：只备原始层、不碰跨项目目标；带 manifest 可校验；保留 3 代。"""
    mod, broot = load_backup(tmp_path, monkeypatch)
    seed(mod)
    (Path(mod.DATA) / "state.json").write_text('{"updated": "t"}', encoding="utf-8")
    for _ in range(4):
        assert mod.run_daily() == 0
    daily = broot / "daily"
    gens = sorted(x for x in daily.iterdir() if x.name.startswith("daily-"))
    assert len(gens) == 3                                   # 保留 3 代
    manifest = json.load(open(gens[-1] / "manifest.json", encoding="utf-8"))
    assert {"events-20260909.jsonl", "state.json"} <= set(manifest["files"])
    assert "fake/fake.db" not in manifest["files"]          # 日备不含跨项目目标
    assert mod.run_verify() == 1                            # 日备不生成 weekly 代，全量校验仍报"无备份"
