#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""零引用会话扫描器（2026-10-11 起，HPMC 漏折事故的完整性网）。

背景：夜间折叠是 LLM 对裸事件流的自由裁量，三道闸门只验完整/一致/可读，
从不验"已消费的可折事件是否都进过档案"——漏折是静默的（HPMC 12 事件在
夜间自打清单里仍被丢掉）。本工具把漏折从静默变成显式裁决：

  按会话聚合已消费的"有内容事件"（prompt 非空且非斜杠命令 / stop 有回复
  文本；id 去重），凡整个会话零引用（折叠账本 ∪ persona evidence 不含其
  任何事件 id）即列出。夜间任务必须逐会话二选一：
    - 漏了 → 当场补折（走 foldlib + 账本，会话变为被引用，下次不再列）
    - 确认无可折 → 写一行 data/fold_exclusions.jsonl 显式排除（带理由）

分纪元（不搞一刀切，避免把既定领地当漏报）：
  ledger-era  2026-10-02 起（账本时代，默认逐会话列出——本工具的主战场）
  pre-ledger  2026-09-07~10-02（折叠早期无账本；--all 才列，多为噪音/已覆盖话题）
  pre-distiller 2026-09-07 前（蒸馏器未诞生，月度挖掘领地，只报计数）

会话粒度是已知盲区：多会话长弧里被引用过的会话整段隐身（eggpaper 尾部
3 stop 即此），尾部完整性由游标竞态修复（foldlib collect/advance）与
KPI signals 纪律兜底，不在此重复扫事件级。

用法：python eval/missed_sessions.py [--all] [--json]
退出码恒 0（列表工具非闸门；audit_persona 以 WARN 接力强制逐条判断）。
"""
import glob
import json
import os
import sys

ROOT = os.environ.get("PERSONA_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PERSONA = os.path.join(ROOT, "persona")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)  # metrics 同目录（从 audit_persona 导入时也成立）
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "distiller"))
import foldlib  # noqa: E402
import metrics  # noqa: E402  evidence id 集合单一来源

LEDGER_ERA = "2026-10-02"   # 账本起建日（metrics.py 同口径）
DISTILLER_ERA = "2026-09-07"  # 项目启动首折
SLASH_CMDS = ("/clear", "/compact", "/help")
EXCLUSIONS_FILE = "fold_exclusions.jsonl"


def load_exclusions(data_dir=DATA):
    """data/fold_exclusions.jsonl → 会话前缀集合（显式排除的会话不再列出）。"""
    path = os.path.join(data_dir, EXCLUSIONS_FILE)
    out = set()
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        sid = row.get("session")
        if sid:
            out.add(sid)
    return out


def _content_of(obj):
    """有内容事件判定：prompt 非空非斜杠；stop 有回复文本。返回 (is_content, snippet)。"""
    t = obj.get("type")
    if t == "prompt":
        txt = (obj.get("text") or "").strip()
        if txt and not txt.startswith(SLASH_CMDS):
            return True, txt
        return False, ""
    if t == "stop":
        r = obj.get("reply")
        txt = ""
        if isinstance(r, dict):
            txt = (r.get("text") or "").strip()
        if not txt:
            txt = (obj.get("preview") or "").strip()
        if txt:
            return True, txt
    return False, ""


def scan(data_dir=DATA, persona=PERSONA, cursor=None, cited=None, exclusions=None):
    """扫已消费事件，返回按纪元分桶的零引用会话。

    返回 {"ledger": [...], "pre_ledger": [...], "pre_distiller": {"sessions", "events"},
          "excluded": int, "sessions_total": int}；
    每行 {session, first, last, n, cwd, snippet, cited_events}。
    """
    if cursor is None:
        try:
            cursor = (json.load(open(os.path.join(data_dir, "state.json"), encoding="utf-8"))
                      .get("files") or {})
        except Exception:
            cursor = {}
    if cited is None:
        pev, _ = metrics.persona_evidence_ids(persona)
        cited = foldlib.folded_event_ids(os.path.join(data_dir, "fold_ledger.jsonl")) | pev
    if exclusions is None:
        exclusions = load_exclusions(data_dir)

    S = {}
    seen = set()
    for name, ln, _raw, obj, _err in foldlib.iter_events(data_dir):
        if ln > cursor.get(name, 0):
            continue
        if not isinstance(obj, dict) or obj.get("type") not in ("prompt", "stop"):
            continue
        eid = obj.get("id")
        if not (isinstance(eid, str) and foldlib.EVID_RE.match(eid)) or eid in seen:
            continue
        seen.add(eid)
        is_c, snippet = _content_of(obj)
        if not is_c:
            continue
        sid = obj.get("session") or "?"
        g = S.setdefault(sid, {"session": sid, "first": "", "last": "", "n": 0,
                               "cwds": set(), "snippet": "", "cited_events": 0})
        g["n"] += 1
        g["cwds"].add((obj.get("cwd") or "").replace("\\", "/"))
        ts = obj.get("ts") or ""
        if not g["first"] or ts < g["first"]:
            g["first"] = ts
        if ts > g["last"]:
            g["last"] = ts
        if obj["type"] == "prompt" and not g["snippet"]:
            g["snippet"] = snippet[:60].split("\n")[0]
        if eid in cited:
            g["cited_events"] += 1

    def _row(g):
        return {"session": g["session"], "first": g["first"], "last": g["last"], "n": g["n"],
                "cwd": ",".join(sorted(g["cwds"]))[:60], "snippet": g["snippet"],
                "cited_events": g["cited_events"]}

    buckets = {"ledger": [], "pre_ledger": [], "pre_distiller": []}
    excluded = 0
    for sid, g in S.items():
        if g["cited_events"]:
            continue
        if any(sid.startswith(p) for p in exclusions):
            excluded += 1
            continue
        day = (g["first"] or "")[:10]
        if day >= LEDGER_ERA:
            buckets["ledger"].append(_row(g))
        elif day >= DISTILLER_ERA:
            buckets["pre_ledger"].append(_row(g))
        else:
            buckets["pre_distiller"].append(_row(g))
    for b in buckets.values():
        b.sort(key=lambda r: r["first"])
    return {"ledger": buckets["ledger"], "pre_ledger": buckets["pre_ledger"],
            "pre_distiller": {"sessions": len(buckets["pre_distiller"]),
                              "events": sum(r["n"] for r in buckets["pre_distiller"])},
            "excluded": excluded, "sessions_total": len(S)}


def _fmt(rows):
    return ["  %s %s n=%3d %s | %s" % (r["first"][:16], r["session"][:14], r["n"],
                                       r["cwd"][:40], r["snippet"][:44]) for r in rows]


def main():
    all_flag = "--all" in sys.argv
    json_flag = "--json" in sys.argv
    m = scan()
    if not json_flag:
        print("零引用会话扫描（账本时代 %s 起，已显式排除 %d 个）" % (LEDGER_ERA, m["excluded"]))
        print("  ledger-era: %d 个未裁决" % len(m["ledger"]))
        for line in _fmt(m["ledger"]):
            print(line)
        print("  pre-ledger: %d 个（--all 列出；多为早期噪音/已覆盖话题）" % len(m["pre_ledger"]))
        if all_flag:
            for line in _fmt(m["pre_ledger"]):
                print(line)
        print("  pre-distiller: %d 会话 / %d 事件（月度挖掘领地，不列）"
              % (m["pre_distiller"]["sessions"], m["pre_distiller"]["events"]))
    print(json.dumps(m, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
