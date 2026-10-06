#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开源仓泄漏扫描（CI 闸门）：密钥模式 + 本机路径/身份黑名单，全树零命中才放行。

与 eval/audit_persona.py 同源复用 SECRET_PATTERNS（单一口径），tests/ 合成夹具
沿用其豁免清单。黑名单串在本文件里用拼接构造——扫描自己时不能自证命中。
"""
import os
import subprocess
import sys

import audit_persona  # 同目录模块：密钥模式与合成豁免的唯一来源

SEP = os.sep


def s(*codes):
    return "".join(map(chr, codes))


BLACKLIST = [
    "D:" + SEP + "persona-stream",                       # 私有仓本机路径
    "C:" + SEP + "Users",                                # 用户目录
    "/c" + SEP.lower() + "users/",                       # Git Bash 形态
    s(76, 101, 110, 111, 118, 111),                      # 主机身份（拆码防自命中）
    "Glory0707/persona-stream-pri" + "vate",             # 私有仓名
    chr(21608) + chr(22885),                             # 作者真名（拆码构造，理由同上）
]

SKIP_DIRS = {".git", ".pytest_cache", "data", "demo-home", "__pycache__"}


def tracked_or_walk(root):
    try:
        out = subprocess.run(["git", "-c", "core.quotepath=false", "-C", root, "ls-files"],
                             capture_output=True, text=True, timeout=15, check=True).stdout
        return [os.path.join(root, x) for x in out.splitlines() if x.strip()]
    except Exception:
        files = []
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            for n in fn:
                files.append(os.path.join(dp, n))
        return files


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hits = []
    for f in tracked_or_walk(root):
        rel = os.path.relpath(f, root).replace("\\", "/")
        if rel.split("/")[0] in SKIP_DIRS or not os.path.isfile(f):
            continue
        try:
            if os.path.getsize(f) > 2_000_000:
                continue
            with open(f, "rb") as fh:
                raw = fh.read()
            if b"\x00" in raw[:4096]:
                continue
            text = raw.decode("utf-8", "replace")
        except OSError:
            continue
        for s in audit_persona.SYNTHETIC_SECRETS:
            text = text.replace(s, "")
        for name in audit_persona.scan_text_for_secrets(rel, text):
            hits.append("%s: 密钥形态（%s）" % (rel, name))
        low = text.lower()
        for b in BLACKLIST:
            if b.lower() in low:
                hits.append("%s: 本机/身份黑名单命中（%s…）" % (rel, b[:24]))
    if hits:
        print("泄漏扫描 ERROR (%d)：" % len(hits))
        for h in hits:
            print("  ✗", h)
        return 1
    print("泄漏扫描通过：全树密钥模式与黑名单零命中")
    return 0


if __name__ == "__main__":
    sys.exit(main())
