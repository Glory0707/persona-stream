#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backfill_rollout.py — 历史 AI 侧回补：遍历 ~/.zcode/cli/rollout/*.jsonl，
逐会话提取模型 I/O 统计（请求数/回复字数/思考字数/工具调用/模型/时间跨度）。
只打印聚合报告（供蒸馏折叠），不落盘不改任何文件。
"""
import collections
import glob
import json
import os

ROLLOUT = os.path.join(os.path.expanduser("~"), ".zcode", "cli", "rollout")
files = sorted(glob.glob(os.path.join(ROLLOUT, "*.jsonl")))
print(f"rollout 文件数: {len(files)}")

sessions = []
for p in files:
    sid = os.path.basename(p)[len("model-io-"):-len(".jsonl")]
    n_req = 0
    text_chars = reasoning_chars = 0
    n_tools = 0
    models = collections.Counter()
    first = last = None
    tool_names = collections.Counter()
    with open(p, encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                e = json.loads(ln)
            except Exception:
                continue
            resp = e.get("response") or {}
            if not isinstance(resp, dict):
                continue
            n_req += 1
            ts = e.get("completedAt") or e.get("startedAt")
            if ts:
                first = first or ts[:10]
                last = ts[:10]
            m = e.get("model") or {}
            models[m.get("modelId", "?")] += 1
            text_chars += len(resp.get("text") or "")
            reasoning_chars += len(resp.get("reasoningText") or "")
            for tc in resp.get("toolCalls") or []:
                n_tools += 1
                if isinstance(tc, dict):
                    tool_names[tc.get("name", "?")] += 1
    if n_req:
        sessions.append({"sid": sid, "n_req": n_req, "text": text_chars,
                         "reason": reasoning_chars, "tools": n_tools,
                         "models": models, "first": first, "last": last,
                         "tool_names": tool_names})

sessions.sort(key=lambda s: -s["n_req"])
tot_req = sum(s["n_req"] for s in sessions)
tot_text = sum(s["text"] for s in sessions)
tot_reason = sum(s["reason"] for s in sessions)
tot_tools = sum(s["tools"] for s in sessions)
print(f"会话数: {len(sessions)} | 总请求轮: {tot_req} | AI 回复总字数: {tot_text} | 思考总字数: {tot_reason} | 工具调用: {tot_tools}")

mm = collections.Counter()
for s in sessions:
    for m, c in s["models"].items():
        mm[m] += c
print("模型分布:", dict(mm.most_common()))

tn = collections.Counter()
for s in sessions:
    tn.update(s["tool_names"])
print("工具调用 TOP15:", tn.most_common(15))

print("\nTOP20 会话（按请求轮数）:")
for s in sessions[:20]:
    print(f"  {s['sid'][:34]:34s} {s['first']}~{s['last']} 轮={s['n_req']:4d} 回复={s['text']:6d}字 思考={s['reason']:7d}字 工具={s['tools']:3d}")

print("\n全部会话清单（sid / 轮数 / 跨度）:")
for s in sessions:
    print(f"  {s['sid'][:40]:40s} {s['n_req']:4d} {s['first']}~{s['last']}")
