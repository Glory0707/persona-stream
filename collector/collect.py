#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""persona-stream 采集器：ZCode hook → 本地事件流（append-only JSONL，仅本地不进 git）。

硬约束（照抄 notify-stop.py 的存活哲学）：
- 顶层 try/except 包裹一切，永远 exit 0，绝不影响 ZCode
- stdout 恒为空（hook 输出按严格 JSON 解析，空输出零风险）
- 无网络，预算 <100ms；无 LLM——全量保真采集（2026-09-09 起：不截断、
  不预丢弃，寒暄/测试类打 noise_like/skip_like 标签照记），
  噪声与信号的判断移交夜间蒸馏 LLM；正则仅用于秘密脱敏
- 失败静默写 data/collector_err.log

config.json 中按事件各挂一条，载荷经 stdin 传入 JSON：
  python.exe collect.py UserPromptSubmit
  python.exe collect.py PermissionRequest
  python.exe collect.py PostToolUse
  python.exe collect.py PostToolUseFailure
  python.exe collect.py Stop
"""
import io
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime

ROOT = os.environ.get("PERSONA_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
ERR_LOG = os.path.join(DATA, "collector_err.log")

# ---------- 启发式标签（只打标不丢弃，供蒸馏 LLM 参考） ----------
SKIP_PAT = [
    r"^(好的?|好呀|好吧|好的呢|行|行吧|可以|嗯+|哦+|噢+|ok|okay|收到|了解|明白|知道"
    r"|知道了|继续|接着来|next|go on|continue|谢谢|感谢|辛苦了?|麻烦了?|thx|thanks)[\s!！。.~，,]*$",
]
NOISE_PAT = [
    r"^(你好+|您好+|哈喽+|嗨+|hello+|hi+|hey+|yo+|在吗|在么|在不"
    r"|早上好|早安|中午好|下午好|晚上好|晚安|你好呀|你好啊|你好你好|hello world)[\s!！。.~，,？?]*$",
    r"^(test|testing|ping|测试|测一下|测试一下|试一下|试试|试一试|连通测试|连通性测试"
    r"|在线吗|能收到吗|能听到吗|听得见吗|你是谁|你叫什么|你是什么模型|你是什么)[\s!！。.~？?]*$",
]
NOISE_PUNCT = r"^[\s?？。.~!！,，、;；:：]+$"
SHORT_ANSWER = r"^\d{1,2}$"

# 子串命中 → 纠正线索（L1 预打标，夜间蒸馏时语义复核）
CORRECTION_CUES = [
    "不对", "不是这样", "不是的", "不是这个意思", "不是我要的", "别这样", "不要这样",
    "谁让你", "我说过", "跟你说过", "重新", "重来", "搞错了", "弄错了", "为什么你要",
    "你怎么又", "不应该", "退回", "撤销", "回滚", "错了", "别再", "以后不要",
]

# ---------- 脱敏 ----------
SECRET_SUBS = [
    (r"sk-[A-Za-z0-9_\-]{16,}", "sk-***"),
    (r"hf_[A-Za-z0-9]{20,}", "hf-***"),
    (r"gh[pousr]_[A-Za-z0-9]{20,}", "gh***"),
    (r"ark\-[0-9a-fA-F\-]{16,}", "ark-***"),
    (r"AKID[A-Za-z0-9]{10,}", "AKID***"),
    (r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}(?:\.[A-Za-z0-9_\-]+)?", "JWT***"),
    # BigModel id.secret 形态：32 位 hex + "." + 12+ 位（历史上用户直接粘贴裸 key 进对话框，2026-09-09 补）
    (r"(?<![0-9a-f])[0-9a-f]{32}\.[A-Za-z0-9]{12,}(?![A-Za-z0-9])", "bmkey-***"),
    (r"Bearer\s+[A-Za-z0-9._\-]{16,}", "Bearer ***"),
    # 兜底凭据串模式带前瞻守卫：值若已是前面具体模式留下的脱敏标记则跳过，
    # 否则会把 "密码 bmkey-***" 二次吞成 "密码***"，丢失密钥类型信息（2026-09-10 修复）
    (r"(?i)(api[_-]?key|token|secret|password|passwd|authorization)\s*[=:：]\s*"
     r"(?!bmkey-|JWT\*|gh\*|sk-\*|hf-\*|AKID\*|ark-\*|Bearer\*)\S{4,}", r"\1=***"),
    (r"密码[均是:为：\s]{0,3}(?!bmkey-|JWT\*|gh\*|sk-\*|hf-\*|AKID\*|ark-\*|Bearer\*)\S{4,}", "密码***"),
    (r"(?i)(账号|帐号|用户名)[\s:：为是]{0,3}\S{6,}\s*[，,。；;和与跟]\s*密码", r"\1=*** 密码"),
]

# ---------- 预编译（2026-10-06 打磨：hot path 每个 prompt/reply 都跑，勿依赖 re 内部缓存） ----------
_SKIP_RES = [re.compile(p) for p in SKIP_PAT]
_NOISE_RES = [re.compile(p) for p in NOISE_PAT]
_NOISE_PUNCT_RE = re.compile(NOISE_PUNCT)
_SHORT_ANSWER_RE = re.compile(SHORT_ANSWER)
_SECRET_RES = [(re.compile(p), rep) for p, rep in SECRET_SUBS]

ERR_LOG_MAX = 256 * 1024  # 错误日志超过 256KB 先轮转，防止故障循环写爆磁盘


def log_err(msg):
    try:
        os.makedirs(DATA, exist_ok=True)
        try:
            if os.path.getsize(ERR_LOG) > ERR_LOG_MAX:
                rot = ERR_LOG + ".1"
                if os.path.exists(rot):
                    os.remove(rot)  # 只保留一代历史
                os.replace(ERR_LOG, rot)
        except OSError:
            pass
        with open(ERR_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def redact(s):
    for pat, rep in _SECRET_RES:
        try:
            s = pat.sub(rep, s)
        except Exception:
            pass
    return s


def classify(prompt):
    """返回标签列表（只打标不丢弃；噪声/信号的最终判断由蒸馏 LLM 做）"""
    low = prompt.strip().lower()
    tags = []
    if any(r.fullmatch(low) for r in _SKIP_RES):
        tags.append("skip_like")
    if any(r.fullmatch(low) for r in _NOISE_RES) or _NOISE_PUNCT_RE.fullmatch(low):
        tags.append("noise_like")
    if _SHORT_ANSWER_RE.fullmatch(low):
        tags.append("short_answer")
    return tags


APPEND_LOCK_TIMEOUT = 3.0  # 锁等待上限（秒）；超时丢事件，绝不阻塞 ZCode


def append_jsonl(relpath, obj):
    """追加一条事件为一行 JSON。持侧车 .lock 文件锁防并发写撕裂行
    （2026-09-12 事故：两个 hook 进程并发 append 把一行撕成两半）。
    锁超时则放弃本条事件——存活哲学优先，宁丢一条不拖会话。"""
    path = os.path.join(DATA, relpath)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        import msvcrt
    except ImportError:
        with open(path, "ab") as f:  # 非 Windows 退化
            f.write(line)
        return
    fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR)
    try:
        deadline = time.time() + APPEND_LOCK_TIMEOUT
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.time() > deadline:
                    return  # 放弃本条，绝不阻塞
                time.sleep(0.005)
        with open(path, "ab") as f:
            f.write(line)
    finally:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        os.close(fd)


def day_file():
    return "events-%s.jsonl" % datetime.now().strftime("%Y%m%d")


def extract_preview(data):
    for key in ("stop_response", "response", "preview", "text", "content"):
        v = data.get(key)
        if isinstance(v, str) and v:
            return v
    for key in ("stop_hook_active", "stop"):
        sub = data.get(key)
        if isinstance(sub, dict):
            for k2 in ("response", "preview", "text"):
                v2 = sub.get(k2)
                if isinstance(v2, str) and v2:
                    return v2
    return ""


# ---------- AI 回复捕获（2026-09-09：读 rollout 末行，提取助手回复/思考/工具调用） ----------
ROLLOUT_DIR = os.path.join(os.path.expanduser("~"), ".zcode", "cli", "rollout")
TAIL_WINDOW = 4 * 1024 * 1024  # 末行超过此窗则标记 deferred（钩子不做大文件解析，路径留档夜间补读）


def rollout_path(sid):
    # rollout 文件命名 = "model-io-" + session_id（session_id 已含 sess_ 前缀）
    return os.path.join(ROLLOUT_DIR, ("model-io-%s.jsonl" % sid) if sid.startswith("sess_")
                        else ("model-io-sess_%s.jsonl" % sid))


def tail_last_obj(path, window=None):
    """读文件末尾 window 字节，返回最后一个完整 JSON 行；解析不了返回 (None, reason)"""
    if window is None:
        window = TAIL_WINDOW
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - window))
            buf = f.read()
        lines = [x for x in buf.split(b"\n") if x.strip()]
        if not lines:
            return None, "empty"
        raw = lines[-1]
        try:
            return json.loads(raw.decode("utf-8", "replace")), None
        except Exception:
            if size > window:
                return None, "deferred_line_too_large"
            return None, "parse_fail"
    except OSError as e:
        return None, "read_fail:%s" % e.errno


def extract_reply(sid):
    """从 rollout 末行提取最后一次 AI 回复（text/reasoning/tool_calls 全量，脱敏）"""
    p = rollout_path(sid)
    if not os.path.exists(p):
        return None
    obj, why = tail_last_obj(p)
    if obj is None:
        return {"deferred": p, "reason": why}
    resp = obj.get("response") or {}
    return {
        "finish": resp.get("finishReason"),
        "text": redact(resp.get("text") or ""),
        "reasoning": redact(resp.get("reasoningText") or ""),
        "tool_calls": trunc_any(resp.get("toolCalls")) if resp.get("toolCalls") else [],
        "rollout": p,
    }


def handle_prompt(data, base):
    prompt = (data.get("prompt") or data.get("content") or "").strip()
    if not prompt:
        return
    tags = classify(prompt)
    cues = [c for c in CORRECTION_CUES if c in prompt]
    append_jsonl(day_file(), {
        **base,
        "type": "prompt",
        "tags": tags,
        "correction_cues": cues,
        "chars": len(prompt),
        "text": redact(prompt),  # 全量保真，不截断
    })


def handle_stop(data, base):
    preview = extract_preview(data)
    append_jsonl(day_file(), {**base, "type": "stop",
                              "preview": redact(preview),
                              "reply": extract_reply(base["session"])})  # AI 回复全量（text/reasoning/tool_calls）


GENERIC_KEYS = ("tool_name", "tool_input", "tool_result", "decision",
                "permissionDecision", "error", "message", "reason")


def trunc_any(v):
    """全量保真：仅做秘密脱敏，不截断字符串、不砍列表"""
    if isinstance(v, str):
        return redact(v)
    if isinstance(v, dict):
        return {k: trunc_any(x) for k, x in v.items()}
    if isinstance(v, list):
        return [trunc_any(x) for x in v]
    return v


def handle_generic(data, base):
    payload = {}
    for k in GENERIC_KEYS:
        if k in data:
            payload[k] = trunc_any(data[k])
    if "transcript_path" in data:
        payload["transcript_path"] = trunc_any(data["transcript_path"])  # 溯源字段，全量保真
    if not payload:
        payload = {k: trunc_any(v) for k, v in data.items()
                   if k != "hook_event_name"}
    p = rollout_path(base["session"])
    if os.path.exists(p):
        payload["rollout"] = p
    append_jsonl(day_file(), {**base, "type": base["hook"].lower(), "payload": payload})


def main():
    try:
        raw = sys.stdin.read() or "{}"
        data = json.loads(raw)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    hook = sys.argv[1] if len(sys.argv) > 1 else str(data.get("hook_event_name") or "Unknown")
    base = {
        "id": uuid.uuid4().hex[:12],
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "hook": hook,
        "session": str(data.get("session_id") or data.get("sessionId") or "unknown")[:64],
        "cwd": str(data.get("cwd") or ""),
    }
    if hook == "UserPromptSubmit":
        handle_prompt(data, base)
    elif hook == "Stop":
        handle_stop(data, base)
    else:
        # PermissionRequest / PostToolUse / PostToolUseFailure：通用观察记录，输出恒为空
        handle_generic(data, base)


if __name__ == "__main__":
    try:
        try:
            sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass
        main()
    except Exception as e:
        log_err("fatal: %r" % e)
    sys.exit(0)
