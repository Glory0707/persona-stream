#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SessionStart hook：向新会话注入人格内核（additionalContext）。

- persona/core.md 缺失或为空 → 输出为空、静默退出（冷启动优雅退化）
- 输出 ensure_ascii=True（\\u 转义合法 JSON），规避 Windows 控制台编码问题
- SessionStart 在 startup/resume/clear/compact 时都会触发，压缩后内核自动补回
- 注入是会话启动时的冻结快照（hermes 同款语义）：会话中途写盘不更新本会话提示词，
  既保语义一致也保 LLM prefix cache
- 2026-10-02：各节尾部标注实际用量（Letta chars_current/limit 模式）；stdin 带 cwd 时
  追加【本项目线头】定向小节（线程 paths: 字段匹配，≤600 字，foldlib.last_blocks 取末块）
"""
import json
import os
import re
import sys

ROOT = os.environ.get("PERSONA_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "persona", "core.md")
SNAPSHOT = os.path.join(ROOT, "persona", "SNAPSHOT.md")
DIGEST = os.path.join(ROOT, "persona", "threads", "DIGEST.md")
THREADS_DIR = os.path.join(ROOT, "persona", "threads")
PEOPLE = os.path.join(ROOT, "persona", "people.yaml")
ASSETS = os.path.join(ROOT, "persona", "assets.yaml")
PROTOCOLS = os.path.join(ROOT, "persona", "protocols.yaml")

_CODE_HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 代码随仓走：distiller/eval 从代码根找，不随 PERSONA_HOME 数据目录漂移
try:
    sys.path.insert(0, os.path.join(_CODE_HOME, "distiller"))
    sys.path.insert(0, os.path.join(_CODE_HOME, "eval"))
    import foldlib  # noqa: E402  # 顶层仅 stdlib，hook 内安全
except Exception:  # pragma: no cover
    foldlib = None

PROJECT_BUDGET = 600  # 本项目线头小节总预算（字符）
RECALL_BUDGET = 700   # 【记忆召回】小节总预算（字符；审查报告 §7① 主动召回）

# 召回噪声过滤（2026-10-06 加固）：自动化任务的样板文不是记忆——夜间/日记任务 prompt、
# goal-runner 的 system-reminder 块、含密钥形态的残留（双保险，采集层落盘前已 redact）
RECALL_NOISE = ("PERSONA_STREAM_DISTILLER", "<system-reminder>", "无人值守夜间蒸馏任务",
                "每日一问任务", "周日五问任务", "Continue working toward")

# 按需加载尾注（固定文本；check_injection 验证时先剥离，避免示例词造成假命中）
FOOTER = ("【按需加载】完整人格档案位于 %s/persona/。"
          "任务涉及对应情境时，先读相关分片再行动："
          "各域策略 → policies/<域>.yaml；人物关系与人名 → people.yaml；"
          "用户用口令指事 → 先读 protocols.yaml 按既定流程执行；"
          "重大取舍与方向演变看 beliefs/；进行中的事看 threads/DIGEST.md。"
          "近期相关事件已由【记忆召回】按当前目录自动注入；追溯更早历史/换关键词 → "
          "`python %s/eval/search_events.py 「关键词」`（本地事件全文索引，FTS5）。"
          "档案未覆盖的方面按常规处理，不要臆测画像。") % (ROOT, ROOT)


def read_all(path):
    """全量读取，不截断（2026-09-09 规则）"""
    with open(path, "r", encoding="utf-8") as f:
        s = f.read().strip()
    if not s:
        raise ValueError("empty")
    return s


def _front_field(s, field):
    if foldlib is not None:
        return foldlib.front_field(s, field)
    m = re.search(r"^%s:\s*(.*?)\s*$" % field, s, re.M)
    return m.group(1).strip() if m else None


def people_index():
    """从 people.yaml 提取 name+brief 速览索引（2026-09-10 规则）。

    注入只带索引（人全、一行一人），完整 relation 证据原话留在文件里按需读取；
    brief 缺失的条目降级为只出名字（audit 会报 ERROR 督促补全）。
    """
    s = read_all(PEOPLE)
    entries = []  # [name, brief]
    for raw in s.splitlines():
        m = re.match(r"^  - name:[ \t]*(.+?)[ \t]*$", raw)  # [ \t] 防跨行：\s 会吞换行匹配到下一行
        if m:
            entries.append([m.group(1), None])
            continue
        if entries and entries[-1][1] is None:
            mb = re.match(r"^\s+brief:[ \t]*(.+?)[ \t]*$", raw)
            if mb and not mb.group(1).startswith("|"):
                entries[-1][1] = mb.group(1)
    out = []
    for name, brief in entries:
        out.append("- %s：%s" % (name, brief) if brief else "- %s（详细见文件）" % name)
    return "\n".join(out)


def assets_index():
    """从 assets.yaml 提取 path+brief 速览（2026-09-24 规则）。

    资料地图：会话填表/找材料先查此索引直接定位查证，不再反复问本人；
    完整 evidence 指针留在文件里。归档条目在 assets_archive.yaml，不随注入。
    条目内键序无关：`  - ` 起始行开新条目，随后任意位置抓 path/brief。
    """
    s = read_all(ASSETS)
    entries = []  # [path, brief]
    for raw in s.splitlines():
        if re.match(r"^  - \S", raw):
            entries.append([None, None])
            continue
        if not entries:
            continue
        mp = re.match(r"^\s+path:[ \t]*(.+?)[ \t]*$", raw)
        if mp and entries[-1][0] is None:
            entries[-1][0] = mp.group(1)
            continue
        mb = re.match(r"^\s+brief:[ \t]*(.+?)[ \t]*$", raw)
        if mb and entries[-1][1] is None and not mb.group(1).startswith("|"):
            entries[-1][1] = mb.group(1)
    out = []
    for path, brief in entries:
        if not path:
            continue
        out.append("- %s：%s" % (path, brief) if brief else "- %s（详见文件）" % path)
    return "\n".join(out)


def project_threads(cwd):
    """cwd → paths: 字段命中的 open 线头（最多 3 个，last_seen 降序）。"""
    if not cwd or foldlib is None or not os.path.isdir(THREADS_DIR):
        return []
    norm = cwd.replace("/", "\\").rstrip("\\/").lower()  # 分隔符归一：paths 里 / 与 \ 等价
    import glob as _glob
    hits = []
    for f in sorted(_glob.glob(os.path.join(THREADS_DIR, "*.yaml"))):
        try:
            s = read_all(f)
        except Exception:
            continue
        if _front_field(s, "status") != "open":
            continue
        paths = _front_field(s, "paths") or []
        for p in paths:
            pn = str(p).replace("/", "\\").rstrip("\\/").lower()
            if norm == pn or norm.startswith(pn + "\\"):
                hits.append((str(_front_field(s, "last_seen") or ""),
                             os.path.basename(f)[:-5], s, f))
                break
    hits.sort(reverse=True)
    return hits[:3]


def _project_section(hits):
    """【本项目线头】小节：digest 行 + 最近块摘录，总预算 600 字（hits 由调用方传入，
    与【记忆召回】共用一次 project_threads 扫描——线程目录读两遍是纯浪费）。"""
    if not hits:
        return None
    out, budget = [], PROJECT_BUDGET
    for _ls, tid, _s, f in hits:
        dg = _front_field(_s, "digest") or _front_field(_s, "topic") or tid
        line = "- %s：%s" % (tid, str(dg)[:60])
        if len(line) > budget:
            break
        out.append(line)
        budget -= len(line)
        try:
            excerpt = foldlib.last_blocks(f, n=1, max_chars=budget)
        except Exception:
            excerpt = ""
        if excerpt:
            piece = excerpt[:budget]
            out.append("  " + piece.replace("\n", "\n  "))
            budget -= len(piece)
        if budget <= 40:
            break
    return "\n".join(out)


def _recall_terms(hits, cwd):
    """线头命中 + cwd → FTS5 检索词：id 分词 + 目录名词元（≥3 字，trigram 下限）。"""
    terms = []
    for _ls, tid, _s, _f in hits:
        for t in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", tid):
            if len(t) >= 3 and t not in terms:
                terms.append(t)
    if cwd:
        base = os.path.basename(str(cwd).rstrip("\\/"))
        for t in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", base):
            if len(t) >= 3 and t not in terms:
                terms.append(t)
    return terms[:4]


_SCAN = None  # 密钥扫描单一来源（eval/secretscan，stdlib-only），进程内只加载一次


def _recall_clean(text):
    """召回条目双保险：自动化样板文与密钥形态残留（正常不该有）都不进注入。"""
    global _SCAN
    if not text or any(mk in text for mk in RECALL_NOISE):
        return False
    if _SCAN is None:
        try:
            sys.path.insert(0, os.path.join(_CODE_HOME, "eval"))
            import secretscan  # noqa: E402  直连单一来源（此前绕道 audit_persona，白付 ~30ms import）
            _SCAN = secretscan
        except Exception:
            _SCAN = False  # 扫描器不可用：放行（采集层已 redact，此处只是兜底）
    if _SCAN is False:
        return True
    return not _SCAN.scan_text(text)


def _index_fresh(db):
    """索引是否已覆盖全部事件（2026-10-06 打磨：mtime 比对替代盲目增量构建）。

    SessionStart 是热路径——此前每次都跑增量构建，即使无新行也要全量扫 192 个
    事件文件（实测 ~430ms）。mtime：任一事件文件比索引新 → stale 需要构建；
    meta 表为空 = 索引从未真正建过 → 恒 stale。"""
    import glob as _g
    try:
        db_t = os.path.getmtime(db)
        data_dir = os.path.dirname(os.path.dirname(os.path.abspath(db)))
        newest = 0.0
        for p in _g.glob(os.path.join(data_dir, "events-*.jsonl")):
            m = os.path.getmtime(p)
            if m > newest:
                newest = m
        if newest > db_t:
            return False
        import sqlite3
        conn = sqlite3.connect(db, timeout=5)
        try:
            n = conn.execute("SELECT count(*) FROM meta").fetchone()[0]
        finally:
            conn.close()
        return n > 0
    except Exception:
        return False  # 判不了就当 stale，宁可多构建一次


def recall_section(cwd, hits=None):
    """【记忆召回】（2026-10-05 起，审查报告 §7①）：FTS5 检索近期相关事件自动注入。

    检索词 = cwd 匹配线头 id 分词 + 目录 basename 词元（hits 由 build_context 传入，
    与【本项目线头】共用一次扫描）；只取 prompt/stop 事件（posttooluse payload 是
    JSON 转储，注入可读性差），按时间降序最多 6 条、预算 700 字。
    索引存在时先增量构建（只索引新行；mtime 判新鲜则跳过；PERSONA_NO_INDEX_BUILD=1 可关）。
    索引缺失/查询失败/零命中一律静默省略本节。
    """
    if not cwd:
        return None
    try:
        import build_index  # eval/ 已在 sys.path（模块头部注册）
    except Exception:
        return None
    db = build_index.DB_PATH
    if not os.path.exists(db):
        return None
    terms = _recall_terms(hits if hits is not None else project_threads(cwd), cwd)
    if not terms:
        return None
    if not _index_fresh(db) and not os.environ.get("PERSONA_NO_INDEX_BUILD"):
        try:
            # 增量：只补游标后新行。data_dir 从 db 路径推导（<data>/index/events.db），
            # 不用 build() 的默认参——默认参定义时绑定，测试 monkeypatch 不生效。
            data_dir = os.path.dirname(os.path.dirname(os.path.abspath(db)))
            build_index.build(data_dir=data_dir, db=db, quiet=True)
        except Exception:
            pass  # 夜间重建持锁等并发场景：召回是增益，失败不阻塞
    try:
        rows = build_index.search(" OR ".join('"%s"' % t for t in terms),
                                  limit=40, db=db, order="DESC", raw=True)
    except Exception:
        return None
    out, budget, seen = [], RECALL_BUDGET, set()
    for r in rows:
        if r.get("typ") not in ("prompt", "stop"):
            continue
        snip = (r.get("snippet") or "").replace("≫", "").replace("≪", "")
        snip = " ".join(snip.split())
        key = snip[:30]
        if not snip or key in seen or not _recall_clean(snip):
            continue
        seen.add(key)
        line = "- %s %s" % (str(r.get("ts") or "")[5:16].replace("T", " "), snip[:110])
        if len(line) + 1 > budget:
            break
        out.append(line)
        budget -= len(line) + 1
        if len(out) >= 6:
            break
    return "\n".join(out) if out else None


def build_context(cwd=None):
    """拼接注入全文（check_injection 与 main 共用；cwd 定向小节见 _project_section）。"""
    parts = []
    core = read_all(CORE)  # core 缺失/为空 → 上层冷启动优雅退化
    parts.append("【人格内核】以下是长期协作中蒸馏出的用户画像，供协作参考（≈%d字）：" % len(core) + "\n" + core)
    try:
        snap = read_all(SNAPSHOT)
        parts.append("【人格速览】（性格/人物/关系协议速览，被问\"我是怎样的人\"\"TA 是怎样的人\"时优先引用并给出证据，≈%d字）\n"
                     % len(snap) + snap)
    except Exception:
        pass
    try:
        dig = read_all(DIGEST)
        n_open = len(re.findall(r"^\s*-\s*\[open\]", dig, re.M))
        parts.append("【未闭合线头】（最近蒸馏快照，行动前以磁盘/仓库现状为准；共%d条 open/上限8）\n"
                     % n_open + dig)
    except Exception:
        pass
    try:
        idx = people_index()
        parts.append("【人物索引】（名单全、一行一人，≈%d字/预算4500；完整证据原话与细节在 persona\\people.yaml，"
                     "涉及具体的人需要引用原话时先读它）\n" % len(idx) + idx)
    except Exception:
        pass
    try:
        idx = assets_index()
        parts.append("【资料地图】（path → brief，≈%d字；填表/找材料先查此索引定位，"
                     "使用前可 ls 核实路径，失效请回写 persona\\assets.yaml）\n" % len(idx) + idx)
    except Exception:
        pass
    try:
        raw = read_all(PROTOCOLS)
        # 注入口令协议时剥离 evidence 行（剩 when/spec true，行为价值不变）
        # 2026-10-06 修复：strip() 已吃掉缩进，旧条件 startswith("  evidence:") 恒 False，
        # evidence 12-hex 实际一直跟着注入文本出去
        cleaned = "\n".join(
            line for line in raw.splitlines()
            if not line.strip().startswith("evidence:"))
        parts.append("【口令协议】（用户说以下口令时按 spec 执行，不要求重复说明，≈%d字）\n" % len(cleaned) + cleaned)
    except Exception:
        pass
    hits = project_threads(cwd)  # 单次扫描，线头小节与召回共用
    proj = _project_section(hits)
    if proj:
        parts.append("【本项目线头】（按当前工作目录定向注入，≤%d字；更多细节读线程文件）\n%s" % (PROJECT_BUDGET, proj))
    rec = recall_section(cwd, hits=hits)
    if rec:
        parts.append("【记忆召回】（按当前目录自动检索的近期相关事件，索引可能滞后；"
                     "换关键词/查更早用 FOOTER 的 search_events.py，≤%d字）\n%s" % (RECALL_BUDGET, rec))
    parts.append(FOOTER)
    return "\n\n".join(parts)


def _stdin_cwd():
    """从 stdin JSON 取 cwd（payload 缺失/非 JSON/非 tty 管道一律优雅降级为 None）。"""
    try:
        raw = sys.stdin.read() if sys.stdin and not sys.stdin.isatty() else ""
        cwd = json.loads(raw).get("cwd") if raw.strip() else None
        return str(cwd) if cwd else None
    except Exception:
        return None


def main():
    try:
        ctx = build_context(cwd=_stdin_cwd())
        print(json.dumps(
            {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}},
            ensure_ascii=True,
        ))
    except Exception:
        pass  # core 缺失 → 冷启动优雅退化：输出为空、静默退出，绝不阻塞会话


if __name__ == "__main__":
    main()
    sys.exit(0)
