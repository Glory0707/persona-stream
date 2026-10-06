#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval/backup.py 回归测试：备份/校验/保留期/损坏检出。"""
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_backup(tmp_path):
    os.environ["PERSONA_BACKUP_DIR"] = str(tmp_path / "backups")
    spec = importlib.util.spec_from_file_location("backup_p", ROOT / "eval" / "backup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DATA = str(tmp_path / "data")
    os.makedirs(mod.DATA, exist_ok=True)
    return mod, tmp_path / "backups"


def seed(mod, name="events-20260909.jsonl", text='{"id":"a"}\n'):
    p = Path(mod.DATA) / name
    with open(p, "a", encoding="utf-8") as f:
        f.write(text)


def test_backup_and_verify_roundtrip(tmp_path):
    mod, broot = load_backup(tmp_path)
    seed(mod)
    seed(mod, "events-20260908.jsonl", '{"id":"b"}\n')
    (Path(mod.DATA) / "state.json").write_text('{"updated": "t"}', encoding="utf-8")
    assert mod.run_backup() == 0
    assert mod.run_verify() == 0
    dirs = mod.backup_dirs()
    assert len(dirs) == 1
    manifest = json.load(open(os.path.join(dirs[0], "manifest.json"), encoding="utf-8"))
    # 5d78c96 起备份扩为跨项目清单（源在本机存在时 manifest 会带 eggpaper/… 等键），
    # 这里只断言本仓核心三件必在；跨项目键存在与否取决于磁盘现状
    assert {"events-20260909.jsonl", "events-20260908.jsonl", "state.json"} <= set(manifest["files"])


def test_verify_catches_tampering(tmp_path):
    mod, broot = load_backup(tmp_path)
    seed(mod)
    assert mod.run_backup() == 0
    target = next(Path(broot).glob("weekly/backup-*/events-20260909.jsonl"))
    target.write_text('{"id":"TAMPERED"}\n', encoding="utf-8")
    assert mod.run_verify() == 1


def test_retention_prunes_old_generations(tmp_path):
    mod, broot = load_backup(tmp_path)
    seed(mod)
    for _ in range(3):
        assert mod.run_backup(keep=2) == 0  # 同秒重跑靠序号后缀区分
    assert len(mod.backup_dirs()) == 2
