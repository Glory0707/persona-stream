#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""密钥形态单一来源：检测模式 + 合成夹具豁免名单（三方共用）。

消费方与契约（2026-10-07 收口，此前名单各写一份、新夹具要登记两处）：
  - audit_persona      入库层：git 已跟踪文件零容忍，用全量 PATTERNS
  - check_events       原始层：只用其中保守子集（在脚本内另行维护）+ 共享 SYNTHETIC
  - inject._recall_clean 召回复扫：直接用 scan_text（stdlib-only，不拖审计模块进热路径）

两层检测面刻意不同宽（66K 历史事件实测裁决）：把 Bearer/凭据串等宽口径下沉到
原始层会产生 165 处误报（"密码登录"类散文、已脱敏标记复命中）——原始层宁可保守。
新增 tests/ 合成夹具时必须在 SYNTHETIC 登记，漏登记会被闸门当真实密钥报 ERROR。
"""
import re

# 入库层检测模式（与 docs/SECURITY.md 事故史对齐：每种都真实泄露过或被外部扫描器拦过）
PATTERNS = [
    (r"sk-[A-Za-z0-9_\-]{20,}", "API key (sk-…)"),
    (r"hf_[A-Za-z0-9]{20,}", "Hugging Face token"),
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "GitHub token"),
    (r"ark-[0-9a-fA-F\-]{20,}", "火山 ark key"),
    (r"AKID[A-Za-z0-9]{13,}", "腾讯 AKID"),
    (r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}", "JWT"),
    (r"(?<![0-9a-f])[0-9a-f]{32}\.[A-Za-z0-9]{12,}(?![A-Za-z0-9])", "BigModel id.secret key"),
    (r"Bearer\s+[A-Za-z0-9._\-]{16,}", "Bearer token"),
    (r"(?i)(api[_-]?key|token|secret|password|passwd)\s*[=:]\s*[\"']?[A-Za-z0-9_.\-]{12,}",
     "明文凭据串"),
]

# 合成夹具字符串白名单（正则可命中，逐一登记豁免；含 .pytest_cache 的 nodeids 缓存——
# 同样会夹带夹具字符串）。不含在此外的疑似密钥一律按真实泄露处置。
SYNTHETIC = (
    # 经典合成夹具（tests/ 沿用）
    "sk-abcdefghijklmnopqrstuvwx", "sk-abcdefghijklmnopqrst",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
    "abc123def456abc123def456abc123de.ns2aiiHhdj50rrOR",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig1234567",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghij",
    # tests/ 其余合成值（正则可命中，须逐一登记豁免）
    "sk-Zx9Lm2Pq7Rv4Tn8Wk3Yc6BhF0Ds5Ja",
    "hf_DEMOfake0000000000000000000000000000",
    "ark-ffeeddccbbaa0011223344556677889900",
    "ark-ffeeddccbbaa0011223344556677889900ff",
    "0123456789abcdef0123456789abcdef.DEMOfakesecret",
    "0123456789abcdef0123456789abcdef.DEMOnotareal99",
    "Bearer abcdefghijklmnopqrst",
    "token=abc123def456",
    "token=supersecret123",
)


def scan_text(text):
    """返回命中的密钥说明列表（豁免名单由调用方先 strip，这里只认形态）。"""
    return [name for pat, name in PATTERNS if re.search(pat, text)]


def strip_synthetic(text):
    """移除豁免名单字符串（子串替换；登记过的夹具不再触发任何检测）。"""
    for s in SYNTHETIC:
        text = text.replace(s, "")
    return text
