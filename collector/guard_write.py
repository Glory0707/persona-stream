#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZCode PreToolUse 守则：人格档案单写者——档案只由夜间蒸馏任务写。

背景（2026-10-16 立，本人令「档案只由夜间任务写」）：此前出现过其他会话自行改写
persona\\ 档案（未记折叠账本、未写 evidence id、digest 超限），需要机械约束
而不是只靠纪律。本 hook 是"机械闸"，配套的说明层见 inject.py FOOTER 与 FOLD_RULES §11。

受保护面
  - persona\\**（**journal\\ 例外**：每日一问/周日五问按各自任务书写日记）
  - data\\fold_ledger.jsonl、data\\state.json、data\\_collect_cursor.json（完整性面）
  - data\\_write_token.json（写令牌本体——只许 distiller\\write_gate.py 创建，防自开锁）

放行条件
  - 本次调用不是写操作；或
  - data\\_write_token.json 存在且未超时（夜间蒸馏/月度挖掘运行开始时用
    `python distiller\\write_gate.py begin` 创建，结束/失败时 `end` 撤销）。

失败哲学：输入读不到、自身异常、拿不准 → 放行（守则只拦明确违规，不阻塞正常工作）。
输出：deny 时打印 {"hookSpecificOutput": {"hookEventName": "PreToolUse",
"permissionDecision": "deny", "permissionDecisionReason": ...}}（本机已验证的 schema），
并把一行事实写进 data\\guard_write.log 供夜间巡检。
"""
import json
import os
import re
import sys
import time

# 数据家：PERSONA_HOME 优先，未设置时默认代码仓根目录（与 collect/inject/foldlib 同约定）
PROJECT = os.environ.get("PERSONA_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(PROJECT, "data")
TOKEN_PATH = os.path.join(DATA, "_write_token.json")
LOG = os.path.join(DATA, "guard_write.log")
TOKEN_TTL_H = 12.0

WRITE_TOOLS = {"write", "edit", "multiedit", "notebookedit", "applypatch"}
PATH_KEYS = ("file_path", "notebook_path", "path", "target_file")
TEXT_KEYS = ("patch", "diff", "input", "content", "new_string", "old_string")

# Bash 侧：命令里同时出现"受保护路径"和"写动作提示词"才拦（读命令如 grep/ls/cat 放行）
_PERSONA_TOKENS = re.compile(
    r"(persona[\\/](threads|beliefs|policies|episodes|protocols|core|SNAPSHOT|people|assets)"
    r"|fold_ledger|_collect_cursor|_write_token|data[\\/]state\.json)", re.I)
_BASH_WRITE_HINTS = re.compile(
    r"(>>?\s|sed\s+-i|\btee\b|set-content|add-content|out-file|new-item|remove-item|"
    r"\bdel\s|\brm\s|rmdir|robocopy|\bcopy\b|\bmove\b|\bpython\b|\bpy\b|\bnode\b|"
    r"\bpwsh\b|powershell|\bperl\b|\bruby\b|git\s+(checkout|restore|clean|apply|stash)|"
    r"\bmv\b|\btruncate\b|write_text|write_bytes|open\([^)]*['\"]w)", re.I)

GUIDE = (
    "【本机守则】人格档案是单写者面：persona\\ 只由夜间蒸馏任务（本机自动化）写入，"
    "其他会话一律不得改动——请勿用 Write/Edit/脚本改档案，也不要自己开写权（write_gate.py）。"
    "该归档的结论/发现→写进 data\\questions_pool.md，或在本轮回复里说明，留给夜间任务折叠；"
    "若确有紧急的档案修正，请在回复中告知本人，由本人转达夜间任务。"
    "（例外：persona\\journal\\ 日记与 data\\questions_pool.md 属问答任务的面，不受本守则限制。）"
)


def _norm(p):
    return str(p or "").replace("\\", "/").strip().lower()


def _persona_seg(path):
    """返回 persona\\ 内相对段落（journal 层会再判定），非 persona 路径返回 None。"""
    p = _norm(path)
    if not p:
        return None
    proj = _norm(PROJECT) + "/"          # 数据家绝对路径（小写正斜杠形）
    if p.startswith(proj):
        p = p[len(proj):]
    else:
        # 数据家目录名相对形（payload 有时以"仓目录/…"开头引用）：随部署自适应，不写死
        home_seg = _norm(os.path.basename(os.path.normpath(PROJECT))) + "/"
        if home_seg != "/" and p.startswith(home_seg):
            p = p[len(home_seg):]
    if p.startswith("persona/"):
        return p[len("persona/"):]
    return None


def protected_reason(path):
    """受保护 → 返回原因字符串；不受保护 → None。"""
    seg = _persona_seg(path)
    if seg is not None:
        if seg.startswith("journal/"):
            return None                   # 日记例外：每日一问/周日五问按任务书写
        return "档案 persona/" + seg
    p = _norm(path)
    if p.endswith("data/_write_token.json"):
        return "写令牌本体（只许 distiller\\write_gate.py 创建）"
    for name in ("data/fold_ledger.jsonl", "data/state.json", "data/_collect_cursor.json"):
        if p == name or p.endswith("/" + name):
            return "完整性面 " + name
    return None


def token_alive(token_path=TOKEN_PATH, now=None, ttl_h=TOKEN_TTL_H):
    """写令牌是否有效（存在且未超时）。"""
    now = time.time() if now is None else now
    try:
        with open(token_path, encoding="utf-8") as f:
            d = json.load(f)
        started = float(d.get("started_ts") or 0)
        ttl = float(d.get("ttl_h") or ttl_h)
    except Exception:
        return False
    if not started:
        return False
    age = now - started
    return 0 <= age < ttl * 3600


def evaluate(payload, token_path=TOKEN_PATH, now=None):
    """纯判定：返回 (allow: bool, reason: str|None, target: str|None)。"""
    if not isinstance(payload, dict):
        return True, None, None
    tool = str(payload.get("tool_name") or "").lower()
    ti = payload.get("tool_input")
    ti = ti if isinstance(ti, dict) else {}

    targets = []
    if tool in WRITE_TOOLS:
        for k in PATH_KEYS:
            if ti.get(k):
                targets.append(str(ti[k]))
        if not targets:                   # 无路径字段（如补丁文本）→ 扫文本里的受保护路径
            blob = "\n".join(str(ti.get(k) or "") for k in TEXT_KEYS)
            for m in _PERSONA_TOKENS.finditer(blob):
                targets.append(m.group(0))
    elif tool == "bash":
        cmd = str(ti.get("command") or "")
        if _PERSONA_TOKENS.search(cmd) and _BASH_WRITE_HINTS.search(cmd):
            targets.append(_PERSONA_TOKENS.search(cmd).group(0))

    reasons = [protected_reason(t) for t in targets]
    reasons = [r for r in reasons if r]
    if not reasons:
        return True, None, None

    # 令牌本体永远不可直写（连夜间也要走 write_gate.py）
    if all(r.startswith("写令牌本体") for r in reasons):
        return False, "写令牌只能由 `python distiller\\write_gate.py begin` 创建/刷新。" + GUIDE, targets[0]

    if token_alive(token_path, now):
        return True, None, targets[0]
    return False, "本次调用会写" + reasons[0] + "。" + GUIDE, targets[0]


def _log(line):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
    except Exception:
        pass


def main():
    raw = ""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw or "{}")
    except Exception:
        return 0                          # 读不到输入 → 放行
    try:
        allow, reason, target = evaluate(payload)
    except Exception:
        return 0                          # 自身异常 → 放行（不阻塞工作）
    if allow:
        return 0
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    sess = str(payload.get("session_id") or "?")[:24]
    tool = str(payload.get("tool_name") or "?")
    try:
        _log("%s deny session=%s tool=%s target=%s" % (ts, sess, tool, target))
    except Exception:
        pass
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
