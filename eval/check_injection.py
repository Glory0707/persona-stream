#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""接收方读链验证（MEMORY_REVIEW P0-4；hermes Channel Fracture 教训的落地）。

夜间双闸门（check_events + audit_persona）通过后追加：本地重建 SessionStart 注入全文
（inject.build_context），断言档案产物的关键词真实出现在接收方文本里——
**写侧成功 ≠ 接收方可见**（cron 委托写到达率 0% 的教训，验证必须在读链上做）。

检查面（存量全量，每晚复核整条链路）：
  1. DIGEST 全文完整出现在注入里（未被截断/换路径丢失）
  2. regen 口径下进入 DIGEST 的 open 线头：digest 摘要在注入中可见
  3. protocols.yaml 每条协议 when 值可见（注入已剥离 evidence）
  4. people.yaml 每个人名（含 / 复合名逐段）可见
  5. assets.yaml 每个资料路径可见

退出码：0=全可见，1=有断链（夜间按 ERROR 处置，不推进游标）。
用法：python eval/check_injection.py [--quiet]
"""
import os
import re
import sys

ROOT = os.environ.get("PERSONA_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CODE_HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 代码根（PERSONA_HOME 只指数据）
sys.path.insert(0, os.path.join(_CODE_HOME, "collector"))
sys.path.insert(0, os.path.join(_CODE_HOME, "distiller"))
import inject  # noqa: E402
import foldlib  # noqa: E402

PERSONA = os.path.join(ROOT, "persona")


def _digest_kept_ids(threads_dir):
    """与 regen_digest 完全同源的排序截断口径（foldlib.digest_plan，杜绝两处漂移）。"""
    kept, _skipped, _dropped = foldlib.digest_plan(threads_dir)
    return {tid for _ls, _dg, tid in kept}


def verify(ctx, persona=PERSONA):
    """返回 (errors, warns, counts)；ctx 为注入全文，供 pytest 用 tmp 目录复用。"""
    errors, warns = [], []
    counts = {}
    threads_dir = os.path.join(persona, "threads")
    # 剥离按需加载尾注：尾注里的示例词（"对抗性审查"等）会让协议检查假命中
    footer = getattr(inject, "FOOTER", "")
    ctx_main = ctx.replace(footer, "") if footer else ctx

    # 1. DIGEST 全文在注入中
    dig_path = os.path.join(threads_dir, "DIGEST.md")
    dig_lines = []
    if os.path.exists(dig_path):
        dig = open(dig_path, encoding="utf-8").read()
        dig_lines = [l.strip() for l in dig.splitlines() if l.strip()]
        missing = [l for l in dig_lines if l not in ctx]
        counts["digest_lines"] = len(dig_lines)
        if missing:
            errors.append("DIGEST 有 %d 行未出现在注入文本（截断/通道断）：%s"
                          % (len(missing), missing[0][:40]))
    else:
        warns.append("DIGEST.md 不存在，跳过线头断链检查")

    # 2. regen 口径保留的 open 线头：digest 可见
    try:
        kept = _digest_kept_ids(threads_dir)
    except foldlib.DigestLineTooLong as e:
        errors.append("digest 超长导致 DIGEST 计划不可算：%s" % e)
        kept = set()
    counts["threads_kept"] = len(kept)
    for tid in sorted(kept):
        f = os.path.join(threads_dir, tid + ".yaml")
        if not os.path.exists(f):
            errors.append("regen 保留的线程文件缺失：%s" % tid)
            continue
        s = open(f, encoding="utf-8").read()
        dg = foldlib.front_field(s, "digest")
        if dg and dg[:24] not in ctx:
            errors.append("线头 %s 的 digest 未出现在注入：%s" % (tid, dg[:30]))

    # 3. 协议 when 可见
    proto = os.path.join(persona, "protocols.yaml")
    n_proto = 0
    if os.path.exists(proto):
        raw = open(proto, encoding="utf-8").read()
        for m in re.finditer(r"^\s+when:\s*(.+)$", raw, re.M):
            val = m.group(1).strip().strip("'\"")
            if not val or val.startswith("|"):
                continue
            n_proto += 1
            probe = val if len(val) <= 30 else val[:30]
            if probe not in ctx_main:
                errors.append("协议 when 未出现在注入：%s" % val[:40])
    counts["protocols"] = n_proto

    # 4. 人名可见（/ 复合名逐段）
    people = os.path.join(persona, "people.yaml")
    n_people = 0
    if os.path.exists(people):
        raw = open(people, encoding="utf-8").read()
        for m in re.finditer(r"^  - name: (.+)$", raw, re.M):
            n_people += 1
            for part in re.split(r"\s*/\s*", m.group(1)):
                key = part.strip()
                keys = [key, re.sub(r"[（(].*?[)）]", "", key).strip()]
                if not any(k and k in ctx_main for k in keys):
                    errors.append("人名未出现在人物索引：%s" % key)
    counts["people"] = n_people

    # 5. 资料路径可见
    assets = os.path.join(persona, "assets.yaml")
    n_assets = 0
    if os.path.exists(assets):
        raw = open(assets, encoding="utf-8").read()
        for m in re.finditer(r"^\s+path:\s*(.+)$", raw, re.M):
            n_assets += 1
            if m.group(1).strip() not in ctx:
                errors.append("资料路径未出现在资料地图：%s" % m.group(1).strip())
    counts["assets"] = n_assets
    return errors, warns, counts


def main():
    quiet = "--quiet" in sys.argv
    try:
        ctx = inject.build_context(None)
    except Exception as e:
        print("ERROR：注入构建失败（core 缺失？）：%r" % e)
        return 1
    errors, warns, counts = verify(ctx)
    if errors:
        print("ERROR (%d)：" % len(errors))
        for e in errors:
            print("  ✗", e)
    if warns and not quiet:
        for w in warns:
            print("  ·", w)
    if not errors:
        print("注入读链验证通过：%s" % counts)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
