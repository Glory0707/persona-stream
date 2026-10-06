#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检索本地事件索引（data/index/events.db，先跑 eval/build_index.py）。

用法：
  python eval/search_events.py "缓存命中" --limit 10
  python eval/search_events.py "光遗传" --json
夜间折叠查重/查历史矛盾、离线深挖、会话内追溯"上次聊过 X"都走这里；
纯本地词法检索（FTS5 trigram），无网络、无嵌入。
--desc 最近优先、--raw 取正文前 180 字（SessionStart 召回内部同款口径）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_index  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--desc", action="store_true", help="按时间降序（最近优先）")
    ap.add_argument("--raw", action="store_true", help="输出正文前 180 字而非匹配窗口")
    ap.add_argument("--db", default=build_index.DB_PATH)
    args = ap.parse_args()
    if args.limit < 1:
        # SQLite 的 LIMIT 负数是"无限制"语义——负值会倾倒全库，宁报错不放大
        ap.error("--limit 须 ≥1")
    if not os.path.exists(args.db):
        print("索引不存在，先运行：python eval/build_index.py", file=sys.stderr)
        return 2
    rows = build_index.search(args.query, limit=args.limit, db=args.db,
                              order="DESC" if args.desc else "ASC", raw=args.raw)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        if not rows:
            print("无命中（索引可能滞后，先跑 build_index.py；trigram 分词下中文查询需 ≥3 字）")
        for r in rows:
            print("%s:%s  %s  [%s]  %s" % (r["file"], r["line"], r["ts"], r["id"], r["typ"]))
            print("    " + r["snippet"].replace("\n", " ")[:260])
    return 0


if __name__ == "__main__":
    sys.exit(main())
