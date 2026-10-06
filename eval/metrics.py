#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""蒸馏质量自动指标（审查报告 §7②）：fold 覆盖率 + 证据回链率。

三个机械口径（不依赖 LLM 判断，可长期追趋势、可进 KPI）：
  coverage  fold 覆盖率 = 游标已消费事件中被证据链覆盖的比例。
              证据链 = persona 全部 evidence/trigger 字段里的 12-hex id ∪ 折叠账本 event_ids。
              分母含 posttooluse 等系统事件（本就不该全折），绝对值天然偏低——看趋势不看绝对值，
              骤降 = 夜间折叠漏做/游标推进了但没折。
  backlink  证据回链率 = 账本记录的折叠事件 id 中真实出现在 persona 证据链里的比例。
              账本记了 add/update 但档案没写 evidence = 回链断裂（账本起建于 2026-10-02，
              此前折叠无账本记录，backlink 只反映账本时代）。
  broken    断链 id = persona 证据链引用了、但事件全库中不存在的 id。契约（FOLD_RULES §-1）：
              evidence 指针链保证任何结论可回溯原始事件——broken 必须为 0，非 0 退出码 1。

输出：人读行 + 末尾一行 JSON。夜间第 5 步跑，coverage/backlink 抄进 KPI 行同名字段。
用法：python eval/metrics.py [--quiet]
"""
import glob
import json
import os
import re
import sys

ROOT = os.environ.get("PERSONA_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PERSONA = os.path.join(ROOT, "persona")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "distiller"))  # 代码根
import foldlib  # noqa: E402

EVID12 = re.compile(r"[0-9a-f]{12}")
EVFIELD = re.compile(r"^\s*(evidence|trigger):")


def persona_evidence_ids(persona=PERSONA):
    """persona 层 yaml 里 evidence/trigger 字段引用的全部 12-hex 事件 id。

    返回 (ids, where)：where[id] = 首个引用位置 "相对路径:字段"（断链定位，免夜间排障再 grep）。
    """
    out, where = set(), {}
    for f in glob.glob(os.path.join(persona, "**", "*.yaml"), recursive=True):
        try:
            s = open(f, encoding="utf-8").read()
        except Exception:
            continue
        try:
            rel = os.path.relpath(f, ROOT).replace("\\", "/")
        except ValueError:  # 跨盘符（测试 tmp 在 C:）：退化为文件名
            rel = os.path.basename(f)
        for line in s.splitlines():
            m = EVFIELD.match(line)
            if not m:
                continue
            field = m.group(1).strip().rstrip(":")
            for i in EVID12.findall(line):
                out.add(i)
                where.setdefault(i, "%s:%s" % (rel, field))
    return out, where


def corpus_and_consumed(data_dir=DATA):
    """一次遍历返回 (全库 id 集, 游标已消费 id 集, 已消费可折事件数[prompt/stop])。

    游标缺失/损坏按 0 处理（只影响 coverage）。coverage 分母 2026-10-06 起只算
    prompt/stop（可折类；posttooluse 等系统事件本就不是折叠目标，计入分母会让
    coverage 随事件量自然衰减成反指标——首夜 0.0186 的教训）。扫描走 foldlib.iter_events。
    """
    try:
        cursor = (json.load(open(os.path.join(data_dir, "state.json"), encoding="utf-8"))
                  .get("files") or {})
    except Exception:
        cursor = {}
    all_ids, consumed, consumed_foldable = set(), set(), 0
    for name, ln, _raw, obj, _err in foldlib.iter_events(data_dir):
        if not isinstance(obj, dict):
            continue
        eid = obj.get("id")
        if not (isinstance(eid, str) and foldlib.EVID_RE.match(eid)):
            continue
        all_ids.add(eid)
        if ln <= cursor.get(name, 0):
            consumed.add(eid)
            if (obj.get("type") or "") in ("prompt", "stop"):
                consumed_foldable += 1
    return all_ids, consumed, consumed_foldable


def compute(data_dir=DATA, persona=PERSONA, ledger_path=None):
    ev_ids, consumed, consumed_foldable = corpus_and_consumed(data_dir)
    pev, where = persona_evidence_ids(persona)
    ledger = foldlib.folded_event_ids(ledger_path)
    cited = pev | ledger
    broken = sorted(pev - ev_ids)
    m = {
        "events_total": len(ev_ids),
        "consumed": len(consumed),
        "consumed_foldable": consumed_foldable,
        "coverage": round(len(consumed & cited) / consumed_foldable, 4) if consumed_foldable else None,
        "ledger_ids": len(ledger),
        "evidence_ids": len(pev),
        "backlink": round(len(ledger & pev) / len(ledger), 4) if ledger else None,
        "broken": broken,
        "broken_where": {i: where[i] for i in broken},
    }
    return m


def main():
    quiet = "--quiet" in sys.argv
    m = compute()
    if not quiet:
        print("蒸馏质量指标：coverage=%s（fold 覆盖率，分母=已消费 prompt/stop，看趋势）  "
              "backlink=%s（证据回链率，账本时代）" % (m["coverage"], m["backlink"]))
        print("  证据链 %d id（账本 %d id 并集）；全库事件 %d、游标已消费 %d（可折 %d）"
              % (m["evidence_ids"], m["ledger_ids"], m["events_total"], m["consumed"],
                 m["consumed_foldable"]))
        if m["broken"]:
            print("  ✗ 断链 id %d 个（persona 引用但事件库不存在；若为学号/电话类散文数字，"
                  "说明 evidence 行混入非指针，需清档案）：" % len(m["broken"]))
            for i in m["broken"][:8]:
                print("      %s @ %s" % (i, m["broken_where"].get(i, "?")))
    print(json.dumps(m, ensure_ascii=False))
    return 1 if m["broken"] else 0


if __name__ == "__main__":
    sys.exit(main())
