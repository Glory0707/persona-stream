#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原始事件流完整性检查（手动/夜间维护调用；只看 data/，data 不入库）。

检查：
  1. 每个 events-*.jsonl 每行都是合法 JSON、公共字段齐全（id/ts/hook/session）
  2. 全局 id 唯一（重复 id = 采集/回填 bug，会污染折叠的证据指针）
  3. 游标一致性：state.json 记录行数不得超过文件实际行数（事件永不删，超了必有损坏）
  4. 秘密扫描：原始层脱敏应在采集时完成，这里做纵深兜底（tests 合成形态不算）

退出码：0=无 error（warn 允许），1=有 error。
用法：python eval/check_events.py [--quiet]
"""
import glob
import json
import os
import re
import sys

ROOT = os.environ.get("PERSONA_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "distiller"))  # 代码根
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))  # eval/（secretscan）
import foldlib  # noqa: E402  事件流唯一扫描口径（iter_events）
import secretscan  # noqa: E402  合成夹具豁免名单单一来源

# 原始层保守检测面（原始层专用，比 secretscan.PATTERNS 入库层窄）：本层兜底的是
# "采集层应脱敏而漏脱敏"的形态；把 Bearer/凭据串等宽口径下沉到这里会在历史语料上
# 产生大量散文误报（"密码登录"类），66K 事件实测裁决过，勿"对齐"。
# 采集层脱敏面另见 collector/collect.py SECRET_SUBS（第三份契约：激进替换）。
SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9_\-]{20,}", "API key"),
    (r"hf_[A-Za-z0-9]{20,}", "Hugging Face token"),
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"ark-[0-9a-fA-F\-]{20,}", "火山 ark key"),
    (r"AKID[A-Za-z0-9]{13,}", "腾讯 AKID"),
    (r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}", "JWT"),
    (r"(?<![0-9a-f])[0-9a-f]{32}\.[A-Za-z0-9]{12,}(?![A-Za-z0-9])", "BigModel id.secret"),
]
REQUIRED = ("id", "ts", "hook", "session")
SYNTHETIC = secretscan.SYNTHETIC


def scan_events(data_dir=DATA):
    """返回 (errors, warns, stats)；供单测直接调用。扫描走 foldlib.iter_events。"""
    errors, warns = [], []
    ids = {}
    files = sorted(glob.glob(os.path.join(data_dir, "events-*.jsonl")))
    total = 0
    for name, ln, raw, obj, err in foldlib.iter_events(data_dir):
        total += 1
        if obj is None:
            errors.append(f"{name}:{ln} JSON 解析失败：{str(err)[:60]}")
            continue
        if not isinstance(obj, dict):
            errors.append(f"{name}:{ln} 不是 JSON 对象")
            continue
        for k in REQUIRED:
            if not obj.get(k):
                errors.append(f"{name}:{ln} 缺公共字段 {k}")
        eid = obj.get("id")
        if eid:
            if eid in ids:
                errors.append(f"{name}:{ln} id 重复：{eid}（首见于 {ids[eid]}）")
            else:
                ids[eid] = f"{name}:{ln}"
        blob = raw.decode("utf-8", "replace")
        if any(s in blob for s in SYNTHETIC):
            continue
        for pat, label in SECRET_PATTERNS:
            m = re.search(pat, blob)
            if m:
                errors.append(f"{name}:{ln} 原始层出现未脱敏{label}：{m.group(0)[:12]}…")
    # 游标一致性
    sp = os.path.join(data_dir, "state.json")
    if os.path.exists(sp):
        try:
            state = json.load(open(sp, encoding="utf-8"))
            for fn, n in (state.get("files") or {}).items():
                p = os.path.join(data_dir, fn)
                if not os.path.exists(p):
                    warns.append(f"游标记录了已不存在的 {fn}")
                    continue
                actual = sum(1 for _ in open(p, "rb"))
                if n > actual:
                    errors.append(f"游标 {fn}={n} 超过实际行数 {actual}（事件流疑似被截断）")
        except Exception as e:
            errors.append(f"state.json 解析失败：{e}")
    return errors, warns, {"files": len(files), "events": total, "ids": len(ids)}


def main():
    quiet = "--quiet" in sys.argv
    errors, warns, stats = scan_events()
    if errors:
        print("ERROR (%d)：" % len(errors))
        for e in errors[:50]:
            print("  ✗", e)
        if len(errors) > 50:
            print("  …另有 %d 条" % (len(errors) - 50))
    if warns and not quiet:
        print("WARN (%d)：" % len(warns))
        for w in warns:
            print("  ·", w)
    if not errors:
        print("事件流完整：%d 个文件 / %d 条事件 / %d 个唯一 id%s"
              % (stats["files"], stats["events"], stats["ids"],
                 "" if not warns else "（%d 条 warn）" % len(warns)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
