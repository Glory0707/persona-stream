#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原始层每周离线备份 + 原始层轻量日备（夜间任务第 6 步调用；目标目录在另一目录，不入库）。

备份内容：
  1. persona-stream 原始层：data/events-*.jsonl + state.json + questions_pool.md + style_pool.md
     （--daily 模式只备这一层，保留 3 代；全量模式保留 8 代）
  2. 跨项目不可再生数据（CROSS_TARGETS 实例表驱动，见下；"可再生"=重下载/重导出/
     重跑脚本可得——真实目标在你的本地副本里维护，模板默认空表）

布局：<backup_root>/weekly/backup-YYYYMMDD-HHMMSS/ 内逐文件复制 + manifest.json
      （sha256/字节数/行数，--verify 可离线校验；跨项目文件键名带项目前缀）
保留：默认保留最新 8 代，更旧的删除。

sqlite 目标用 SQLite backup API 出快照（源库正被写也能拿到一致副本，免去
db+wal+shm 三件套拼接的撕裂风险），≥1MB 的快照落盘为 .gz（实测存量库压缩比
~81%；恢复 gunzip 后即是完整 db）；树目标整目录复制；缺失的源跳过并记入
manifest 的 missing（项目没装在这台机不算错）。

用法：
  python eval/backup.py              # 备份并校验，然后按保留期清理
  python eval/backup.py --daily     # 原始层轻量日备（events/state/questions_pool/style_pool）
  python eval/backup.py --verify     # 只校验最新一代备份，不复制
  python eval/backup.py --keep 12    # 覆盖保留代数
"""
import glob
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

ROOT = os.environ.get("PERSONA_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
BACKUP_ROOT = os.environ.get("PERSONA_BACKUP_DIR") or os.path.join(ROOT, "backups")
WEEKLY = os.path.join(BACKUP_ROOT, "weekly")
DAILY = os.path.join(BACKUP_ROOT, "daily")

# 跨项目目标：(kind, 源路径, manifest 键名, 树排除项)
# kind: sqlite=backup API 快照；file=单文件；tree=整目录
# 跨项目不可再生数据（实例差异表：在你的本地副本里替换为真实目标）。
# 模板留空——默认没有跨项目目标，weekly 备份只覆盖本仓数据层。
# 条目形态（key=备份内相对路径；excludes 仅对 tree 生效；缺省路径记 missing 跳过）：
#   ("sqlite", "~/data/app.db", "app/app.db", None)
#   ("file",   "~/data/config.yaml", "app/config.yaml", None)
#   ("tree",   "~/projects/irreplaceable", "irreplaceable",
#    {".venv", "__pycache__", ".git", "out"})
CROSS_TARGETS = [
]


def sources():
    files = sorted(glob.glob(os.path.join(DATA, "events-*.jsonl")))
    for extra in ("state.json", "questions_pool.md", "style_pool.md"):
        p = os.path.join(DATA, extra)
        if os.path.exists(p):
            files.append(p)
    return files


def sha256_and_lines(path):
    h = hashlib.sha256()
    lines = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            lines += chunk.count(b"\n")
    return h.hexdigest(), lines


def snapshot_sqlite(src, out):
    """SQLite backup API 快照：源库正被写也能拿到一致副本。"""
    s = sqlite3.connect(src)
    try:
        d = sqlite3.connect(out)
        try:
            with d:
                s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


def _record(path, key, meta):
    sha, lines = sha256_and_lines(path)
    meta[key] = {"sha256": sha, "bytes": os.path.getsize(path), "lines": lines}


def _safe_rel(full, src):
    """relpath 的防御壳：Windows 保留名（如字面文件 nul）会让 relpath 解析成 \\\\.\\nul
    设备路径而抛 ValueError（2026-10-11 周备首炸）。返回 None 表示跳过，并向 stderr 留痕。"""
    try:
        return os.path.relpath(full, src)
    except ValueError:
        print(f"backup: 跳过无法相对化的路径（Windows 保留名？）：{full}", file=sys.stderr)
        return None


def copy_cross(dst, meta, missing):
    """把跨项目目标收进这一代备份目录。缺失的源记入 missing，不算失败。"""
    for kind, src, key, excludes in CROSS_TARGETS:
        if not os.path.exists(src):
            missing.append(key)
            continue
        out = os.path.join(dst, *key.split("/"))
        if kind == "tree":
            if not os.path.isdir(src):
                missing.append(key)
                continue
            for root, dirs, fs in os.walk(src):
                dirs[:] = [d for d in dirs
                           if d not in excludes
                           and _safe_rel(os.path.join(root, d), src) is not None
                           and os.path.relpath(os.path.join(root, d), src).replace("\\", "/") not in excludes]
                for fn in fs:
                    full = os.path.join(root, fn)
                    rel = _safe_rel(full, src)
                    if rel is None:
                        continue   # 保留名/设备路径（如字面名 nul）：relpath 会炸成 \\.\nul，跳过并留痕
                    rel = rel.replace("\\", "/")
                    fout = os.path.join(out, *rel.split("/"))
                    os.makedirs(os.path.dirname(fout), exist_ok=True)
                    try:
                        shutil.copy2(full, fout)
                    except OSError:
                        continue      # 复制途中被删/被占用：跳过该文件
                    _record(fout, f"{key}/{rel}", meta)
        elif kind == "sqlite":
            os.makedirs(os.path.dirname(out), exist_ok=True)
            try:
                snapshot_sqlite(src, out)
            except sqlite3.Error as e:
                missing.append(f"{key}（snapshot failed: {e}）")
                continue
            # 大库落盘为 .gz：token-usage 存量库只进不出，8 代全量复制涨得快
            # （实测 gzip -6 压缩比 ~81%）；恢复时 gunzip 后即是完整 db
            if os.path.getsize(out) >= (1 << 20):
                gz = out + ".gz"
                try:
                    with open(out, "rb") as fi, gzip.open(gz, "wb", compresslevel=6) as fo:
                        shutil.copyfileobj(fi, fo, 1 << 20)
                    os.remove(out)
                    _record(gz, key + ".gz", meta)
                    continue
                except OSError:
                    try:
                        os.remove(gz)     # 压缩半途失败：退回明文快照入库
                    except OSError:
                        pass
            _record(out, key, meta)
        else:   # file
            os.makedirs(os.path.dirname(out), exist_ok=True)
            try:
                shutil.copy2(src, out)
            except OSError:
                missing.append(key)
                continue
            _record(out, key, meta)


def backup_dirs():
    if not os.path.isdir(WEEKLY):
        return []
    out = []
    for n in os.listdir(WEEKLY):
        p = os.path.join(WEEKLY, n)
        if n.startswith("backup-") and os.path.isdir(p):
            out.append(p)
    return sorted(out)


def verify(d):
    mp = os.path.join(d, "manifest.json")
    if not os.path.exists(mp):
        return False, ["manifest.json 缺失"]
    manifest = json.load(open(mp, encoding="utf-8"))
    bad = []
    for name, m in manifest.get("files", {}).items():
        p = os.path.join(d, *name.split("/"))
        if not os.path.exists(p):
            bad.append(f"{name} 文件缺失")
            continue
        sha, lines = sha256_and_lines(p)
        if sha != m["sha256"] or os.path.getsize(p) != m["bytes"] or lines != m["lines"]:
            bad.append(f"{name} 校验不符（sha/大小/行数）")
    return not bad, bad


def run_daily(keep=3):
    """原始层轻量日备（夜间蒸馏每跑必带）：事件层 append-only 每天只增，
    周全量的 7 天丢失窗口压到 1 天；只复制 sources()（events/state/两池），
    保留最新 keep 代，同样带 manifest+自检。"""
    if not os.path.isdir(DATA):
        print("无 data 目录，跳过日备")
        return 1
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(DAILY, "daily-" + stamp)
    n = 2
    while os.path.exists(dst):
        dst = os.path.join(DAILY, "daily-%s-%d" % (stamp, n))
        n += 1
    os.makedirs(dst, exist_ok=False)
    manifest = {"created": datetime.now().astimezone().isoformat(timespec="seconds"),
                "project_root": ROOT, "files": {}, "missing": []}
    meta = manifest["files"]
    for src in sources():
        shutil.copy2(src, os.path.join(dst, os.path.basename(src)))
        _record(os.path.join(dst, os.path.basename(src)), os.path.basename(src), meta)
    with open(os.path.join(dst, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    ok, bad = verify(dst)
    if not ok:
        print("日备自检失败：%s" % "; ".join(bad))
        return 1
    old = [os.path.join(DAILY, x) for x in sorted(os.listdir(DAILY))
           if x.startswith("daily-") and os.path.isdir(os.path.join(DAILY, x))]
    for d in old[:-keep] if len(old) > keep else []:
        shutil.rmtree(d, ignore_errors=True)
    print("日备完成：%d 文件 → %s；校验通过；保留 %d 代（共 %d 代）"
          % (len(meta), os.path.relpath(dst, BACKUP_ROOT), keep, min(len(old), keep)))
    return 0


def run_backup(keep=8):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(WEEKLY, "backup-" + stamp)
    n = 2
    while os.path.exists(dst):  # 同一秒内重复运行：追加序号
        dst = os.path.join(WEEKLY, "backup-%s-%d" % (stamp, n))
        n += 1
    os.makedirs(dst, exist_ok=False)
    manifest = {"created": datetime.now().astimezone().isoformat(timespec="seconds"),
                "project_root": ROOT, "files": {}, "missing": []}
    meta = manifest["files"]
    for src in sources():
        shutil.copy2(src, os.path.join(dst, os.path.basename(src)))
        _record(os.path.join(dst, os.path.basename(src)), os.path.basename(src), meta)
    copy_cross(dst, meta, manifest["missing"])
    if not meta:
        print("无可备份文件（persona data/ 为空且跨项目源全部缺失）")
        shutil.rmtree(dst, ignore_errors=True)
        return 1
    with open(os.path.join(dst, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    ok, bad = verify(dst)
    if not ok:
        print("备份后自检失败：%s" % "; ".join(bad))
        return 1
    old = backup_dirs()
    for d in old[:-keep] if len(old) > keep else []:
        shutil.rmtree(d, ignore_errors=True)
    n_persona = sum(1 for k in meta if "/" not in k)
    print("备份完成：persona %d 文件 + 跨项目 %d 文件 → %s；校验通过；保留 %d 代（共 %d 代）"
          % (n_persona, len(meta) - n_persona, os.path.relpath(dst, BACKUP_ROOT), keep,
             min(len(old), keep)))
    if manifest["missing"]:
        print("缺失源（跳过）：%s" % "; ".join(manifest["missing"]))
    return 0


def run_verify():
    dirs = backup_dirs()
    if not dirs:
        print("无备份可校验")
        return 1
    latest = dirs[-1]
    ok, bad = verify(latest)
    if ok:
        n = len(json.load(open(os.path.join(latest, "manifest.json"), encoding="utf-8"))["files"])
        print("最新备份校验通过：%s（%d 个文件）" % (os.path.basename(latest), n))
        return 0
    print("最新备份损坏：%s" % "; ".join(bad))
    return 1


if __name__ == "__main__":
    if "--daily" in sys.argv:
        sys.exit(run_daily())
    if "--verify" in sys.argv:
        sys.exit(run_verify())
    keep = 8
    if "--keep" in sys.argv:
        keep = int(sys.argv[sys.argv.index("--keep") + 1])
    sys.exit(run_backup(keep))
