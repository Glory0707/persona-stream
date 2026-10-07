#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原始事件流全文索引（MEMORY_REVIEW P0-3）——data/index/events.db（gitignore，仅本地）。

SQLite FTS5，trigram 分词（中文 ≥3 字可查；sqlite 过旧无 trigram 时退回 unicode61 并提示
中文查全受限，不装任何外部依赖——hermes session_search 同款纯词法路线，无嵌入无 LLM）。
增量构建：meta 表记每文件已索引行数、字节数与前缀内容哈希（append-only：字节未变
整文件跳过；字节变了则比对前缀哈希，"原地改写后续写"这一型由哈希抓——(bytes,lines)
二元组对它数学不可分；等字节的恶意改写仍不可检，属信任边界内的已知极限），
夜间维护步骤随游标增量跑；新索引 ≥500 行时跑 FTS5 optimize 控膨胀；原始层永不改动。

用法：python eval/build_index.py [--quiet] [--rebuild]
查询：python eval/search_events.py "关键词" [--limit 10] [--json]
"""
import glob
import hashlib
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
    for col in ("bytes", "fhash"):   # bytes=2026-10-06 打磨；fhash=2026-10-07 前缀内容哈希
        try:
            conn.execute("ALTER TABLE meta ADD COLUMN %s INTEGER DEFAULT -1" % col
                         if col == "bytes" else
                         "ALTER TABLE meta ADD COLUMN %s TEXT DEFAULT ''" % col)
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


def _scan(path, start, name):
    """单遍扫描：行号即新游标。返回 (rows, total, prefix_hash, file_hash)——
    prefix_hash=已索引前缀（前 start 行）的内容摘要，用于识别"行数增长的原地
    改写"（截断/等长改写由 (bytes,lines) 抓，这一型此前数学不可分，幽灵行
    永久存留）；file_hash=本次读到的全文摘要，存 meta 供下轮当比对基准。"""
    rows, total = [], start
    h_pre, h_all = hashlib.sha256(), hashlib.sha256()
    with open(path, "rb") as f:
        for ln, raw in enumerate(f, 1):
            total = ln
            h_all.update(raw)
            if ln <= start:
                h_pre.update(raw)
                continue
            try:
                obj = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                continue
            txt = _event_text(obj)
            if txt:
                rows.append((txt, obj.get("id") or "", obj.get("ts") or "",
                             name, ln, obj.get("type") or ""))
    return rows, total, h_pre.hexdigest() if start else "", h_all.hexdigest()


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
        done = {r[0]: (r[1], r[2], r[3])
                for r in conn.execute("SELECT file, lines, bytes, fhash FROM meta")}
    except sqlite3.OperationalError:
        try:
            done = {r[0]: (r[1], r[2], "")
                    for r in conn.execute("SELECT file, lines, bytes FROM meta")}
        except sqlite3.OperationalError:
            done = {r[0]: (r[1], -1, "") for r in conn.execute("SELECT file, lines FROM meta")}
    total_new = 0
    for path in sorted(glob.glob(os.path.join(data_dir, "events-*.jsonl"))):
        name = os.path.basename(path)
        start, known_bytes, known_hash = done.get(name, (0, -1, ""))
        try:
            fsize = os.path.getsize(path)
        except OSError:
            continue
        if not rebuild and known_bytes == fsize and start > 0:
            continue  # append-only：字节数未变 → 无新行，整文件跳过
        rows, total, pre_hash, file_hash = _scan(path, start, name)
        truncated = known_bytes not in (-1, fsize) and total <= start
        rewritten = (start > 0 and known_hash and pre_hash
                     and known_hash != pre_hash)
        if truncated or rewritten:
            # append-only 被破坏（截断/原地改写/改写后续写，2026-10-07 测试员轮实测：
            # 旧行变幽灵且 meta 已标"最新"会永久存留）→ 删该文件全部旧行整文件重索引
            conn.execute("DELETE FROM events WHERE file=?", (name,))
            rows, total, pre_hash, file_hash = _scan(path, 0, name)
        if rows:
            conn.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)", rows)
        conn.execute("INSERT INTO meta(file, lines, bytes, fhash) VALUES(?,?,?,?) "
                     "ON CONFLICT(file) DO UPDATE SET lines=excluded.lines, "
                     "bytes=excluded.bytes, fhash=excluded.fhash",
                     (name, total, fsize, file_hash))
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
