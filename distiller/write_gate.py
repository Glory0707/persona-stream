#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""档案写权开关（单写者守则的钥匙）。

夜间蒸馏（distiller\\AUTOMATION_PROMPT.md 第 1 步）与月度挖掘（MINING_PROMPT.md）
运行开始时 `begin` 开写权、结束或失败时 `end` 撤销；本人手工维护档案时同理。
守卫在 collector\\guard_write.py（ZCode PreToolUse hook），读 data\\_write_token.json。

用法：
  python distiller\\write_gate.py begin [--by nightly] [--ttl 12]   # 开写权（记录到 guard_write.log）
  python distiller\\write_gate.py end                              # 撤销
  python distiller\\write_gate.py status                           # 查看当前状态

注意：`begin` 是"我确实要写档案"的显式声明——每次调用都会在 data\\guard_write.log
留下痕迹，夜间任务巡检该日志，非预期开锁会在报告里点名。
"""
import argparse
import json
import os
import sys
import time

# 数据家：PERSONA_HOME 优先，未设置时默认代码仓根目录（与 foldlib/guard_write 同约定）
PROJECT = os.environ.get("PERSONA_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_PATH = os.path.join(PROJECT, "data", "_write_token.json")
LOG = os.path.join(PROJECT, "data", "guard_write.log")


def _log(line):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line.rstrip() + "\n")
    except Exception:
        pass


def begin(by="manual", ttl_h=12.0):
    d = {"started_ts": time.time(), "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
         "by": str(by), "ttl_h": float(ttl_h), "pid": os.getpid()}
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    tmp = TOKEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, TOKEN_PATH)
    _log("unlock by=%s ttl=%.1fh pid=%d" % (by, ttl_h, os.getpid()))
    return d


def end():
    existed = os.path.exists(TOKEN_PATH)
    try:
        os.remove(TOKEN_PATH)
    except OSError:
        pass
    if existed:
        _log("lock (write token removed)")
    return existed


def status():
    try:
        with open(TOKEN_PATH, encoding="utf-8") as f:
            d = json.load(f)
        age_h = (time.time() - float(d.get("started_ts") or 0)) / 3600.0
        alive = 0 <= age_h < float(d.get("ttl_h") or 12.0)
        return {"token": True, "alive": alive, "age_h": round(age_h, 2),
                "by": d.get("by"), "ttl_h": d.get("ttl_h")}
    except Exception:
        return {"token": False, "alive": False}


def main(argv=None):
    ap = argparse.ArgumentParser(description="档案写权开关（单写者守则）")
    ap.add_argument("action", choices=["begin", "end", "status"])
    ap.add_argument("--by", default="manual", help="谁开的写权（写进 guard_write.log）")
    ap.add_argument("--ttl", type=float, default=12.0, help="写权有效小时数（默认 12）")
    a = ap.parse_args(argv)
    if a.action == "begin":
        print(json.dumps(begin(a.by, a.ttl), ensure_ascii=False))
    elif a.action == "end":
        print(json.dumps({"removed": end()}, ensure_ascii=False))
    else:
        print(json.dumps(status(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
