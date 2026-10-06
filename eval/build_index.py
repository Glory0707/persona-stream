#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原始事件流全文索引（MEMORY_REVIEW P0-3）——data/index/events.db（gitignore，仅本地）。

SQLite FTS5，trigram 分词（中文 ≥3 字可查；sqlite 过旧无 trigram 时退回 unicode61 并提示
中文查全受限，不装任何外部依赖——hermes session_search 同款纯词法路线，无嵌入无 LLM）。
增量构建：meta 表记每文件已索引行数与字节数（append-only，字节未变整文件跳过），
夜间维护步骤随游标增量跑；新索引 ≥500 行时跑 FTS5 optimize 控膨胀；原始层永不改动。

用法：python eval/build_index.py [--quiet] [--rebuild]
查询：python eval/search_events.py "关键词" [--limit 10] [--json]
"""
import glob
import json
import os
import sqlite3
import sys
import time

ROOT = os.environ.get("PERSONA_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
INDEX_DIR = os.path.join(DATA, "index")
DB_PATH = os.path.join(INDEX_DIR, "events.db")


def _connect(db=DB_PATH):
    os.makedirs(os.path.dirname(db), exist_ok=True)
    return sqlite3.connect(db, timeout=30)  # 长超时：夜间重建与会话内检索并发时不互踢


def _has_trigram(conn):
    try:
        conn.execute("CREATE VIRTUAL TABLE _t USING fts5(x, tokenize='trigram')")
        conn.execute("DROP TABLE _t")
        return True
    except sqlite3.OperationalError:
        return False


def _schema(conn, tokenize):
    conn.execute("CREATE TABLE IF NOT EXISTS meta(file TEXT PRIMARY KEY, lines INTEGER)")
    try:
        # 2026-10-06 打磨：记录文件字节数——事件文件 append-only，字节数未变即可整文件
        # 跳过（此前即使无新行也要全量读 65K 行，434ms/次）
        conn.execute("ALTER TABLE meta ADD COLUMN bytes INTEGER DEFAULT -1")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 列已存在
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS events USING fts5("
        "text, id UNINDEXED, ts UNINDEXED, file UNINDEXED, line UNINDEXED, "
        "typ UNINDEXED, tokenize='%s')" % tokenize)
    conn.commit()


def _event_text(obj):
    """抽取可检文本：prompt 全文 / stop 回复+思考 / 工具 payload 紧凑 JSON。"""
    typ = obj.get("type") or (obj.get("hook") or "").lower()
    parts = []
    if typ == "prompt":
        parts.append(obj.get("text") or "")
    elif typ == "stop":
        rep = obj.get("reply") or {}
        parts.append(obj.get("preview") or "")
        parts.append(rep.get("text") or "")
        parts.append(rep.get("reasoning") or "")
    else:
        p = obj.get("payload")
        if isinstance(p, dict):
            parts.append(json.dumps(p, ensure_ascii=False))
    return "\n".join(x for x in parts if x)


def build(data_dir=DATA, db=DB_PATH, rebuild=False, quiet=False, optimize_at=500):
    """增量构建。optimize_at：新索引行数达到阈值才跑 FTS5 optimize——大索引上
    optimize 耗秒级（实测 65K 行 ~10s），只配夜间批量；会话启动的小增量构建跳过。"""
    t0 = time.time()
    conn = _connect(db)
    tokenize = "trigram" if _has_trigram(conn) else "unicode61"
    if rebuild:
        conn.execute("DROP TABLE IF EXISTS events")
        conn.execute("DROP TABLE IF EXISTS meta")
        conn.commit()
    _schema(conn, tokenize)
    try:
        done = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT file, lines, bytes FROM meta")}
    except sqlite3.OperationalError:
        done = {r[0]: (r[1], -1) for r in conn.execute("SELECT file, lines FROM meta")}
    total_new = 0
    for path in sorted(glob.glob(os.path.join(data_dir, "events-*.jsonl"))):
        name = os.path.basename(path)
        start, known_bytes = done.get(name, (0, -1))
        try:
            fsize = os.path.getsize(path)
        except OSError:
            continue
        rows, total = [], start  # 单遍扫描：行号即新游标，不再回头数第二遍
        if not rebuild and known_bytes == fsize and start > 0:
            continue  # append-only：字节数未变 → 无新行，整文件跳过
        with open(path, "rb") as f:
            for ln, raw in enumerate(f, 1):
                total = ln
                if ln <= start:
                    continue
                try:
                    obj = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    continue
                txt = _event_text(obj)
                if txt:
                    rows.append((txt, obj.get("id") or "", obj.get("ts") or "",
                                 name, ln, obj.get("type") or ""))
        if rows:
            conn.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)", rows)
        conn.execute("INSERT INTO meta(file, lines, bytes) VALUES(?,?,?) "
                     "ON CONFLICT(file) DO UPDATE SET lines=excluded.lines, bytes=excluded.bytes",
                     (name, total, fsize))
        conn.commit()
        total_new += len(rows)
    if total_new and total_new >= optimize_at:
        try:
            conn.execute("INSERT INTO events(events) VALUES('optimize')")  # FTS5 段合并，控索引膨胀
            conn.commit()
        except sqlite3.OperationalError:
            pass
    n = conn.execute("SELECT count(*) FROM events").fetchone()[0]
    conn.close()
    if not quiet:
        print("索引完成：%s 分词，本次新增 %d 条，总量 %d 条，耗时 %.1fs"
              % (tokenize, total_new, n, time.time() - t0))
    return total_new


def search(query, limit=10, db=DB_PATH, order="ASC", raw=False):
    """返回 [{file,line,id,ts,typ,snippet}]；查询语法错误退化为纯子串 LIKE。

    order 只收 ASC/DESC（注入层召回用 DESC 取最近；白名单防拼接进 SQL）。
    raw=True 时 snippet 换成正文前 180 字（注入层用：完整句比匹配窗口可读）。
    """
    order = "DESC" if str(order).upper() == "DESC" else "ASC"
    col = ("substr(text,1,180)" if raw
           else "snippet(events, 0, '≫', '≪', '…', 14)")
    conn = sqlite3.connect(db, timeout=30)
    try:
        cur = conn.execute(
            "SELECT file, line, id, ts, typ, %s "
            "FROM events WHERE events MATCH ? ORDER BY ts %s LIMIT ?" % (col, order),
            (query, limit))
        rows = [dict(zip(("file", "line", "id", "ts", "typ", "snippet"), r)) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        cur = conn.execute(
            "SELECT file, line, id, ts, typ, substr(text,1,200) FROM events "
            "WHERE text LIKE ? ORDER BY ts %s LIMIT ?" % order, ("%" + query + "%", limit))
        rows = [dict(zip(("file", "line", "id", "ts", "typ", "snippet"), r)) for r in cur.fetchall()]
    conn.close()
    return rows


def main():
    build(quiet="--quiet" in sys.argv, rebuild="--rebuild" in sys.argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
