#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""foldlib —— 夜间蒸馏的受控写路径库（2026-10-02 立规，MEMORY_REVIEW P0-1/P0-2）。

背景：历史上夜间折叠用临时脚本做字符串切片，产生过三类自伤缺陷——
  ① FOLD8：DIGEST 写了、线头正文没写（×2）
  ② DIGEST 替换静默未命中（replace 目标串与文件不符，改了个寂寞）
  ③ 块内切片丢行首缩进 → YAML 损坏并推坏远端
本模块把"写档案"收敛为受测函数：写前断言、写后 safe_load 回读、原子落盘、
超限报错附现有块清单（hermes 溢出报错模式），并附带折叠账本（fold ledger）与
确定性 DIGEST 再生。AUTOMATION_PROMPT 第 3 步：折叠一律走本库，禁止临时脚本。

设计对齐实际数据格式：
- threads/*.yaml 顶层字段 id/status/opened/last_seen/topic/detail/evidence，
  正文块是 detail: |- 块标量里以 `；【` 开头的行分段文本
- 折叠账本 data/fold_ledger.jsonl（gitignore，仅本地）：每次折叠动作一行
  {date, action, target, block_key, event_ids, old_summary, new_summary}，
  提供精确幂等判重（替代启发式 KPI 匹配）与 evidence id 反向索引
"""
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import date as _date

ROOT = os.environ.get("PERSONA_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSONA = os.path.join(ROOT, "persona")
THREADS_DIR = os.path.join(PERSONA, "threads")
ARCHIVE_DIR = os.path.join(THREADS_DIR, "_archive")
DATA = os.path.join(ROOT, "data")
LEDGER_PATH = os.path.join(DATA, "fold_ledger.jsonl")

EVID_RE = re.compile(r"^[0-9a-f]{12}$")
BLOCK_HEAD_RE = re.compile(r"^[；;]?\s*【")
THREAD_BODY_LIMIT = 32 * 1024   # 单线头文件字节硬上限；超限报错逼当场合并
DIGEST_MAX = 8                  # DIGEST 条目上限（FOLD_RULES §4）
DIGEST_LINE_MAX = 40            # 单条 ≤40 字（含日期后缀，与 audit 同口径）
LEDGER_ACTIONS = ("add", "update", "prune", "invalidate")
STALE_ARCHIVE_DAYS = 30         # closed 超此天数可迁移 _archive
DORMANT_DAYS = 21               # open 超此天数未动 → dormant（遗忘；再折叠自动唤醒）
DORMANT_CLOSE_DAYS = 60         # dormant 超此天数仍无活动 → 自动闭合（生命周期终局）


class FoldLibError(Exception):
    """foldlib 基类：夜间任务捕获后按 ERROR 处置，不推进游标。"""


class BlockExistsError(FoldLibError):
    pass


class YamlVerifyError(FoldLibError):
    pass


class ThreadBodyLimitExceeded(FoldLibError):
    """超 32KB 硬限。args = (blocks, )——现有块清单，逼本次折叠当场做合并。"""

    def __init__(self, path, blocks):
        self.blocks = blocks
        super().__init__(
            "%s 超过 %d 字节硬限（现 %d 块）；必须当场合并/归档最弱块后再写入，禁止静默截断"
            % (os.path.basename(path), THREAD_BODY_LIMIT, len(blocks)))


class DigestLineTooLong(FoldLibError):
    pass


class LedgerError(FoldLibError):
    pass


# ---------- 基础 ----------

def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write_atomic(path, s):
    """tmp + os.replace 原子落盘；读-改-写必须整体包在 _write_lock 里（见下）。"""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(s)
    os.replace(tmp, path)


APPEND_LOCK_TIMEOUT = 3.0  # 侧车锁等待上限；超时放弃（同 collector/collect.py 存活哲学）


@contextmanager
def _write_lock(path):
    """档案读-改-写的侧车锁：原子替换只防撕裂，不防双蒸馏实例的丢失更新
    （A 读→B 读→A 写→B 写 = A 的块静默消失）。所有 foldlib 变更器必须包此锁。
    """
    lock = path + ".lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR)
    try:
        try:
            import msvcrt
        except ImportError:
            msvcrt = None
        if msvcrt is None:
            import fcntl  # POSIX：flock 独占锁（并发追加不丢更新的语义在非 Windows 同样成立）
            deadline = time.time() + APPEND_LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.time() > deadline:
                        raise FoldLibError("写锁超时（疑似双实例并发写同一档案）：%s"
                                           % os.path.basename(path))
                    time.sleep(0.005)
            try:
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            return
        deadline = time.time() + APPEND_LOCK_TIMEOUT
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.time() > deadline:
                    raise FoldLibError("写锁超时（疑似双实例并发写同一档案）：%s"
                                       % os.path.basename(path))
                time.sleep(0.005)
        try:
            yield
        finally:
            try:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
    finally:
        os.close(fd)


def _yaml_ok(s):
    import yaml
    try:
        yaml.safe_load(s)
        return True
    except Exception:
        return False


def front_field(s, field):
    """读顶层 frontmatter 字段（status/last_seen/digest/related/paths…）。

    行内匹配用 [ \\t] 不用 \\s——`\\s*` 会跨行吞掉换行，把下一行匹配进来。
    """
    m = re.search(r"^%s:[ \t]*(.*?)[ \t]*$" % field, s, re.M)
    if not m:
        return None
    v = m.group(1).strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [x.strip().strip("'\"") for x in inner.split(",")] if inner else []
    return v.strip("'\"")


def _set_front_line(s, field, line):
    """顶层字段行整行替换（set_digest/_wake_dormant/touch_thread/sweep 共用）。

    lambda 替换是硬约束：re.sub 的字符串替换里反斜杠是转义炸弹——digest 单引号值、
    dormant 日期等含特殊字符时字面量拼接会砸坏 YAML（历史事故③同源）。
    """
    return re.sub(r"^%s:.*$" % field, lambda m: line, s, count=1, flags=re.M)


# ---------- 线头正文块 ----------

def _segments(s):
    """正文分段的共享底座：返回 (lines, ranges, ev_idx)。

    块 = `；【`/`【` 开头行起，至下一块或顶层 evidence 行止——blocks_of /
    last_blocks / invalidate_block 三处共用同一口径，防止各写一遍产生漂移。
    """
    lines = s.splitlines(keepends=True)
    starts = [i for i, ln in enumerate(lines) if BLOCK_HEAD_RE.match(ln.strip())]
    ev_idx = next((i for i, ln in enumerate(lines) if ln.startswith("evidence:")), len(lines))
    ranges = []
    for k, st in enumerate(starts):
        en = min(starts[k + 1] if k + 1 < len(starts) else len(lines), ev_idx)
        if en > st:
            ranges.append((st, en))
    return lines, ranges, ev_idx


def blocks_of(path):
    """detail 各正文块的块首行（前 50 字）——查重与超限报错附块清单用。"""
    lines, ranges, _ev = _segments(_read(path))
    return ["".join(lines[st:en]).strip().splitlines()[0][:50] for st, en in ranges]


def last_blocks(path, n=1, max_chars=600):
    """取 detail 末尾 n 个块文本（cwd 定向注入用），总长截到 max_chars。"""
    lines, ranges, _ev = _segments(_read(path))
    chunks = ["".join(lines[st:en]).strip() for st, en in ranges]
    out = ""
    for c in reversed(chunks[-n:] if n else chunks):  # 最近的块优先
        piece = c if len(c) <= max_chars else c[:max_chars] + "…"
        if out and len(out) + len(piece) > max_chars:
            break
        out = piece + ("\n" + out if out else "")
    return out


def _wake_dormant(s, day):
    """dormant 线头被折叠动作触碰时的唤醒：status→open、删 dormant 日期、last_seen 刷新。

    所有会写线头的折叠路径（append_detail/set_digest/touch_thread）共用，
    保证"被遗忘的线头一旦再被提及就回到活跃层"。
    """
    if front_field(s, "status") != "dormant":
        return s
    s = _set_front_line(s, "status", "status: open")
    s = re.sub(r"^dormant:.*$\n?", "", s, count=1, flags=re.M)
    if re.search(r"^last_seen:.*$", s, re.M):
        s = _set_front_line(s, "last_seen", 'last_seen: "%s"' % day)
    return s


def append_detail(path, block, evidence_ids, ledger_path=None):
    """向 detail 块标量追加正文块（自动 2 空格缩进）+ 合并 evidence id。

    写前：块首 50 字查重（BlockExistsError）、超限预检（ThreadBodyLimitExceeded）、
    新全文 safe_load 预检；读-改-写全程持侧车锁。返回本次新增的 evidence id 列表。
    **写盘成功后自动记一条 add 账本行**（2026-10-06 起，机械记账——提示词纪律首夜漏记 6 行
    导致幂等守卫与 backlink 指标空转，故降为代码约束）；锁超时丢行不回滚档案。
    测试注入 ledger_path=None 跳过记账。
    """
    block = block.strip()
    if not block:
        raise FoldLibError("空块拒绝写入：" + path)
    key = block.splitlines()[0].strip()[:50]
    add = []
    with _write_lock(path):
        s = _read(path)
        s = _wake_dormant(s, _date.today().isoformat())  # 折叠即唤醒（dormant → open）
        if key in s:
            raise BlockExistsError("块已存在（先查重再写）：%s @ %s" % (key, os.path.basename(path)))
        if "\nevidence:" not in s:
            raise FoldLibError("文件缺 evidence 行，不像线头档案：" + path)
        indented = "\n".join(("  " + ln if ln.strip() else "") for ln in block.splitlines())
        # 插入点 = detail 块标量结束处：其后第一个顶层字段行（evidence/paths/related 等顺序不定）
        d0 = s.index("\ndetail:")
        m_next = re.search(r"\n(?=[A-Za-z_][A-Za-z0-9_]*:)", s[d0 + 1:])
        ins = (d0 + 1 + m_next.start()) if m_next else len(s)
        new_s = s[:ins] + "\n" + indented + s[ins:]
        if len(new_s.encode("utf-8")) > THREAD_BODY_LIMIT:
            raise ThreadBodyLimitExceeded(path, blocks_of(path))
        if not _yaml_ok(new_s):
            raise YamlVerifyError("写入后 YAML 无法解析，已放弃（%s）" % os.path.basename(path))
        m = re.search(r"^evidence: \[(.*?)\]", new_s, re.M | re.S)  # 锚定顶层行，防 detail 内文本劫持
        have = re.findall(r"[0-9a-f]{12}", m.group(1)) if m else []
        add = [i for i in (evidence_ids or [])
               if isinstance(i, str) and EVID_RE.match(i) and i not in have]
        have += add
        if m:
            new_s = new_s[:m.start()] + "evidence: [" + ", ".join('"%s"' % i for i in have) + "]" + new_s[m.end():]
        _write_atomic(path, new_s)
    if ledger_path is not None and add:  # 自动记账（机械纪律；None=测试静默）
        try:
            try:
                target = os.path.relpath(path, PERSONA).replace("\\", "/")
            except ValueError:  # 跨盘符（测试 tmp 在 C:）：退化为文件名
                target = os.path.basename(path)
            ledger_append("add", target, key, add, new_summary=key[:120],
                          ledger_path=ledger_path)
        except LedgerError:
            pass
    return add


def set_digest(path, text):
    """写线程的 digest 字段（≤32 字、不带日期后缀——日期由 regen_digest 统一追加）。

    仅 open 线头可写——closed 线头不进 DIGEST，写它只会制造假信号。
    值一律单引号包裹：半角冒号/引号/反斜杠不会破坏 YAML。
    """
    text = text.strip()
    if not text or len(text) > DIGEST_LINE_MAX - 8:  # 给（MM-DD）留 8 字
        raise DigestLineTooLong("digest 须 1~%d 字（收到 %d）：%s"
                                % (DIGEST_LINE_MAX - 8, len(text), text[:30]))
    line = "digest: '%s'" % text.replace("'", "''")
    with _write_lock(path):
        s = _read(path)
        s = _wake_dormant(s, _date.today().isoformat())  # 更新摘要即唤醒（dormant → open）
        if front_field(s, "status") == "closed":
            raise FoldLibError("closed 线头不写 digest（不进 DIGEST）：" + os.path.basename(path))
        if re.search(r"^digest:.*$", s, re.M):
            s = _set_front_line(s, "digest", line)
        else:
            anchor = re.search(r"^last_seen:.*$", s, re.M)
            if anchor:
                s = s[:anchor.end()] + "\n" + line + "\n" + s[anchor.end():]
            else:
                raise FoldLibError("线头缺 last_seen 锚点，digest 无处落位：" + path)
        if not _yaml_ok(s):
            raise YamlVerifyError("digest 写入后 YAML 无法解析：" + path)
        _write_atomic(path, s)


def touch_thread(path, day=None):
    """受控刷新 last_seen（2026-10-05 起：此前 last_seen 没有受控写路径，夜间只能字符串改）。

    夜间任务动过某线头就调它；dormant 线头自动唤醒为 open（_wake_dormant）。
    closed 线头拒绝（闭合线头的"最后活动"语义归属 closed 日期）。
    返回 True=有写入，False=已是当天（幂等）。
    """
    day = day or _date.today().isoformat()
    with _write_lock(path):
        s = _read(path)
        st = front_field(s, "status")
        if st not in ("open", "dormant"):
            raise FoldLibError("只允许 touch open/dormant 线头：" + os.path.basename(path))
        new_s = _wake_dormant(s, day) if st == "dormant" else s
        if not re.search(r"^last_seen:.*$", new_s, re.M):
            raise FoldLibError("线头缺 last_seen 字段：" + os.path.basename(path))
        new_s = _set_front_line(new_s, "last_seen", 'last_seen: "%s"' % day)
        if new_s != s and not _yaml_ok(new_s):
            raise YamlVerifyError("touch 后 YAML 无法解析：" + os.path.basename(path))
        if new_s != s:
            _write_atomic(path, new_s)
    return new_s != s


def add_related(path, other_id, threads_dir=THREADS_DIR):
    """线程交叉引用（A-MEM 式受控连边）：related: [id, …]，双侧由调用方各调一次。"""
    other = os.path.join(threads_dir, other_id + ".yaml")
    if not os.path.exists(other):
        raise FoldLibError("related 目标不存在：%s" % other_id)
    with _write_lock(path):
        s = _read(path)
        s = _wake_dormant(s, _date.today().isoformat())  # 写入即触碰（dormant → open）
        me = front_field(s, "id") or os.path.basename(path)[:-5]
        if me == other_id:
            raise FoldLibError("禁止自引用 related：" + me)
        cur = front_field(s, "related") or []
        if other_id in cur:
            return False
        cur.append(other_id)
        new_s = _set_list_field(s, "related", cur)
        if not _yaml_ok(new_s):
            raise YamlVerifyError("related 写入后 YAML 无法解析：" + path)
        _write_atomic(path, new_s)
    return True


def add_paths(path, dirs):
    """线程 paths 字段（cwd 定向注入用）：只收真实存在的目录。"""
    for d in dirs:
        if not os.path.isdir(d):
            raise FoldLibError("paths 目录不存在：%s" % d)
    merged_new = []
    with _write_lock(path):
        s = _read(path)
        s = _wake_dormant(s, _date.today().isoformat())  # 写入即触碰（dormant → open）
        cur = front_field(s, "paths") or []
        merged = list(cur)
        for d in dirs:
            nd = d.rstrip("\\/")
            if nd not in merged:
                merged.append(nd)
                merged_new.append(nd)
        new_s = _set_list_field(s, "paths", merged)
        if not _yaml_ok(new_s):
            raise YamlVerifyError("paths 写入后 YAML 无法解析：" + path)
        _write_atomic(path, new_s)
    return merged_new


def _set_list_field(s, field, values):
    # 单引号标量：Windows 路径的反斜杠在双引号里是非法转义（\U → YAML 报错）；
    # lambda 替换为字面量——re.sub 的字符串替换里反斜杠是转义炸弹
    items = ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)
    line = "%s: [%s]" % (field, items)
    if re.search(r"^%s:.*$" % field, s, re.M):
        return re.sub(r"^%s:.*$" % field, lambda m: line, s, count=1, flags=re.M)
    m = re.search(r"^evidence:", s, re.M)
    if m:
        return s[:m.start()] + line + "\n" + s[m.start():]
    return s.rstrip("\n") + "\n" + line + "\n"


def invalidate_block(path, block_key, day, superseded_by=None, note=""):
    """软失效（Graphiti 移植）：结论被推翻时在对应块后加失效标记行，不删原文。

    标记行 = `；〔已失效 YYYY-MM-DD，由 <superseded_by> 取代〕note`——用〔〕不用【】，
    避免标记行被 blocks_of 当成新块；重复失效同一块报错；superseded_by 应为接替块首
    50 字（audit 抽查其存在性）。
    """
    tag = "；〔已失效 %s%s〕%s" % (day, ("，由 %s 取代" % superseded_by) if superseded_by else "", note)
    with _write_lock(path):
        s = _read(path)
        lines, ranges, _ev = _segments(s)
        if not ranges:
            raise FoldLibError("未找到任何正文块：" + path)
        hit = None
        for st, en in ranges:
            if block_key in "".join(lines[st:en]):
                hit = (st, en)
                break
        if hit is None:
            raise FoldLibError("未找到块（key=%s）：%s" % (block_key[:30], os.path.basename(path)))
        seg = "".join(lines[hit[0]:hit[1]])
        if "〔已失效" in seg:
            raise BlockExistsError("该块已有失效标记：" + os.path.basename(path))
        tail = lines[hit[1] - 1]
        if not tail.endswith("\n"):
            tail += "\n"
        lines[hit[1] - 1] = tail + "  " + tag + "\n"
        new_s = "".join(lines)
        if not _yaml_ok(new_s):
            raise YamlVerifyError("失效标记写入后 YAML 无法解析：" + path)
        _write_atomic(path, new_s)
    return True


def invalidate_rule(policy_path, when_sub, day, note=""):
    """policies 规则软失效：命中 when 子串的规则块内追加 `invalidated: "date"` 行。"""
    with _write_lock(policy_path):
        s = _read(policy_path)
        if not s.endswith("\n"):
            s += "\n"  # 规则是最后一条且文件无尾换行时，裸拼接会砸坏 YAML
        lines = s.splitlines(keepends=True)
        starts = [i for i, ln in enumerate(lines) if re.match(r"^  - ", ln)]
        hit = None
        for k, st in enumerate(starts):
            en = starts[k + 1] if k + 1 < len(starts) else len(lines)
            if when_sub in "".join(lines[st:en]):
                hit = (st, en)
                break
        if hit is None:
            raise FoldLibError("未找到规则（when 子串=%s）：%s" % (when_sub[:30], policy_path))
        seg = "".join(lines[hit[0]:hit[1]])
        if re.search(r"^\s+invalidated:", seg, re.M):
            raise BlockExistsError("该规则已失效标记过：" + policy_path)
        indent = re.match(r"^(\s+)", lines[hit[0]]).group(1) + "  "
        ins = indent + 'invalidated: "%s"%s\n' % (day, ("  # " + note) if note else "")
        pos = sum(len(x) for x in lines[:hit[1]])
        new_s = s[:pos] + ins + s[pos:]
        if not _yaml_ok(new_s):
            raise YamlVerifyError("规则失效写入后 YAML 无法解析：" + policy_path)
        _write_atomic(policy_path, new_s)
    return True


# ---------- DIGEST 确定性再生 ----------

def digest_entries(threads_dir=THREADS_DIR):
    """从线程文件收集 (last_seen, digest, thread_id)；缺 digest 的进 skipped。"""
    import glob
    got, skipped = [], []
    for f in sorted(glob.glob(os.path.join(threads_dir, "*.yaml"))):
        s = _read(f)
        if front_field(s, "status") != "open":
            continue
        tid = front_field(s, "id") or os.path.basename(f)[:-5]
        dg = front_field(s, "digest")
        ls = str(front_field(s, "last_seen") or "")
        if not dg or not ls:
            skipped.append(tid)
            continue
        got.append((ls, dg, tid))
    return got, skipped


def digest_plan(threads_dir=THREADS_DIR):
    """DIGEST 排序截断的唯一裁决：返回 (kept, skipped, dropped)。

    kept = [(last_seen, digest, tid)] 按 last_seen 降序、上限 8 条——regen_digest 与
    check_injection 都以此为准，杜绝两处口径漂移。
    """
    got, skipped = digest_entries(threads_dir)
    for ls, dg, tid in got:
        if len(dg) + 8 > DIGEST_LINE_MAX:
            raise DigestLineTooLong("%s digest %d 字，加日期后超 %d：%s"
                                    % (tid, len(dg), DIGEST_LINE_MAX, dg))
    got.sort(key=lambda x: (x[0], x[2]), reverse=True)
    dropped = [tid for _, _, tid in got[DIGEST_MAX:]]
    return got[:DIGEST_MAX], skipped, dropped


def regen_digest(threads_dir=THREADS_DIR, out=os.path.join(THREADS_DIR, "DIGEST.md")):
    """确定性再生 DIGEST.md：按 last_seen 降序、上限 8 条、单条 ≤40 字（含日期后缀）。

    返回 (written_count, skipped_ids, dropped_ids)。语义只有一处来源——线程文件的
    digest 字段；夜间 LLM 只更新该字段，拼装/排序/截断全部由本函数负责。
    """
    kept, skipped, dropped = digest_plan(threads_dir)
    lines = ["# 线头摘要（夜间任务维护，只放 open 线头，每条 ≤40 字，按 last_seen 降序）", ""]
    lines.append("共%d条 open（上限%d）" % (len(kept) + len(dropped), DIGEST_MAX))
    lines.append("")
    for ls, dg, _tid in kept:
        lines.append("- [open] %s（%s）" % (dg, ls[5:]))  # 2026-10-01 → 10-01
    _write_atomic(out, "\n".join(lines) + "\n")
    return len(kept), skipped, dropped


# ---------- 折叠账本 ----------

def _locked_append(path, line):
    """账本单行追加（_write_lock 同款侧车锁；超时放弃本条，结论仍在档案里）。"""
    line_bytes = line.encode("utf-8")
    try:
        import msvcrt
    except ImportError:  # 非 Windows 退化：单进程内 O_APPEND 足够
        with open(path, "ab") as f:
            f.write(line_bytes)
        return True
    fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR)
    try:
        deadline = time.time() + APPEND_LOCK_TIMEOUT
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.time() > deadline:
                    return False  # 宁丢一条账本行（折叠结论仍在档案里），不阻塞
                time.sleep(0.005)
        with open(path, "ab") as f:
            f.write(line_bytes)
        return True
    finally:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        os.close(fd)


def ledger_append(action, target, block_key, event_ids, old_summary=None,
                  new_summary=None, day=None, ledger_path=None):
    """追加一条折叠账本行。action ∈ add|update|prune|invalidate；event_ids 须 12-hex。"""
    if action not in LEDGER_ACTIONS:
        raise LedgerError("非法 action：%s" % action)
    ids = [i for i in (event_ids or []) if isinstance(i, str) and EVID_RE.match(i)]
    bad = [i for i in (event_ids or []) if not (isinstance(i, str) and EVID_RE.match(i))]
    if bad:
        raise LedgerError("event_ids 含非法 id（须 12-hex）：%r" % bad[:3])
    row = {"date": day or _date.today().isoformat(),
           "action": action, "target": target, "block_key": (block_key or "")[:120],
           "event_ids": ids}
    if old_summary:
        row["old_summary"] = str(old_summary)[:300]
    if new_summary:
        row["new_summary"] = str(new_summary)[:300]
    lp = ledger_path or LEDGER_PATH
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    if not _locked_append(lp, json.dumps(row, ensure_ascii=False) + "\n"):
        raise LedgerError("账本锁超时（可能双实例并发），本条未记：" + target)


def folded_event_ids(ledger_path=None):
    """账本中已折叠的全部事件 id（精确幂等的依据）。文件缺失视为空集。"""
    lp = ledger_path or LEDGER_PATH
    out = set()
    if not os.path.exists(lp):
        return out
    with open(lp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            for i in row.get("event_ids") or []:
                if isinstance(i, str) and EVID_RE.match(i):
                    out.add(i)
    return out


def iter_events(data_dir=DATA):
    """events-*.jsonl 的唯一扫描口径（2026-10-06 复用收口）：逐行产出 (name, ln, raw, obj, err)。

    obj 解析失败时为 None、err 为异常——消费方按需处置（check_events 报错、其余跳过）。
    此前 pending/metrics/check_events 各写一遍"glob→逐行→json.loads"，口径漂移风险
    与三份维护成本同源；只读不写，编码错误按 replace 解码（与采集层写入口径一致）。
    """
    import glob
    for path in sorted(glob.glob(os.path.join(data_dir, "events-*.jsonl"))):
        name = os.path.basename(path)
        with open(path, "rb") as f:
            for ln, raw in enumerate(f, 1):
                try:
                    obj = json.loads(raw.decode("utf-8", "replace"))
                except Exception as e:
                    yield name, ln, raw, None, e
                else:
                    yield name, ln, raw, obj, None


def pending(state_path=None, data_dir=DATA, ledger_path=None):
    """游标之后的事件里，多少已在账本（夜间幂等守卫的一步到位检查）。

    返回 {"total", "already", "pending"}：pending==0 且 total>0 → 上次已折叠过
    （commit 成功 push 失败的重试场景），夜间任务跳过第 3-5 步直接推送。
    """
    sp = state_path or os.path.join(data_dir, "state.json")
    try:
        state = json.load(open(sp, encoding="utf-8"))
    except Exception:
        state = {}
    cursor = state.get("files") or {}
    have = folded_event_ids(ledger_path)
    total = already = 0
    for name, ln, _raw, obj, _err in iter_events(data_dir):
        if ln <= cursor.get(name, 0):
            continue
        total += 1
        eid = obj.get("id") if isinstance(obj, dict) else None
        if isinstance(eid, str) and eid in have:
            already += 1
    return {"total": total, "already": already, "pending": total - already}


def _cli(argv):
    if argv[:1] == ["pending"]:
        print(json.dumps(pending(), ensure_ascii=False))
        return 0
    if argv[:1] == ["sweep"]:
        print(json.dumps(sweep_threads(), ensure_ascii=False))
        return 0
    print("用法：python distiller\\foldlib.py pending  # 夜间幂等守卫：游标后事件 vs 账本\n"
          "      python distiller\\foldlib.py sweep    # 遗忘清扫：open 超 %d 天未动 → dormant；\n"
          "                                           #   dormant 超 %d 天无活动 → 自动闭合"
          % (DORMANT_DAYS, DORMANT_CLOSE_DAYS))
    return 2


# ---------- 线头生命周期 ----------

def archive_thread(thread_id, threads_dir=THREADS_DIR, archive_dir=ARCHIVE_DIR,
                   day=None):
    """closed 超 30 天的线头迁移：完整文件移入 threads/_archive/，原位留桩。

    桩保留 id/status/closed/closure/topic 契约字段，正文一行指向归档位置；
    归档永不删除（同 policies/_archive 惯例），audit 对 _archive 不做 open 检查。
    """
    src = os.path.join(threads_dir, thread_id + ".yaml")
    if not os.path.exists(src):
        raise FoldLibError("线程不存在：" + thread_id)
    day = day or _date.today().isoformat()
    with _write_lock(src):
        s = _read(src)
        if front_field(s, "status") != "closed":
            raise FoldLibError("只允许归档 closed 线头：" + thread_id)
        import yaml
        obj = yaml.safe_load(s) or {}  # 结构化读取：块标量 closure / 多行字段在这里安全
        closed = str(obj.get("closed") or "")
        closure = obj.get("closure")
        if isinstance(closure, list):
            closure = "；".join(str(x) for x in closure)
        closure = " ".join(str(closure or "见归档文件").split())
        if len(closure) > 120:
            closure = closure[:117] + "…"
        dst = os.path.join(archive_dir, thread_id + ".yaml")
        if os.path.exists(dst):
            raise BlockExistsError("归档位已存在同名文件：" + dst)
        # 桩 evidence 只收顶层 evidence 字段的 id——全文 findall 会把散文里的
        # 12 位数字（学号/电话）当事件指针写进桩，污染 metrics 断链闸门（实测复现）
        m_ev = re.search(r"^evidence: \[(.*?)\]", s, re.M)
        ev_ids = re.findall(r"[0-9a-f]{12}", m_ev.group(1))[:20] if m_ev \
            else re.findall(r"[0-9a-f]{12}", s)[:20]
        stub = (
            "# 线头：{tid}（已归档）\n"
            "id: {tid}\nstatus: closed\nopened: \"{opened}\"\nclosed: \"{closed}\"\n"
            "closure: {closure}\n"
            "topic: {topic}\n"
            "detail: |-\n"
            "  本线头于 {day} 依生命周期规则归档（closed 超 30 天）：完整正文块与 evidence 指针链在\n"
            "  persona/threads/_archive/{tid}.yaml，按需读取；本桩只保留检索契约字段。\n"
            "evidence: [{ev}]\n"
        ).format(tid=thread_id, opened=str(obj.get("opened") or ""), closed=closed,
                 closure="'" + closure.replace("'", "''") + "'",
                 topic="'" + str(obj.get("topic") or thread_id).replace("\n", " ")[:120].replace("'", "''") + "'",
                 day=day,
                 ev=", ".join('"%s"' % i for i in ev_ids))
        if not _yaml_ok(stub):
            raise YamlVerifyError("归档桩 YAML 校验失败：" + thread_id)
        os.makedirs(archive_dir, exist_ok=True)
        _write_atomic(dst, s)      # 完整原文先落归档位
        try:
            _write_atomic(src, stub)  # 再原位留桩
        except Exception:
            os.remove(dst)            # 桩写失败 → 回收归档位，源文件完好
            raise
    return dst


def sweep_threads(threads_dir=THREADS_DIR, day=None, dormant_days=DORMANT_DAYS,
                  close_days=DORMANT_CLOSE_DAYS, ledger_path=None):
    """遗忘清扫（机械层，审查报告 §7③；夜间维护步骤跑 `foldlib.py sweep`）。

    两级衰减（2026-10-06 起完整生命周期 open→dormant→closed→archive）：
      ① open 超 dormant_days 天未动 → `status: dormant` + `dormant: "日期"`（降出
         DIGEST/注入，正文与 evidence 原样保留；被再折叠或 touch 触碰即自动唤醒）
      ② dormant 超 close_days 天仍无活动 → 自动闭合：`status: closed` + closure
         "dormant 超 N 天无活动自动闭合；重开即续线"（Mem0 平台版 expiration 同款终局，
         但不删除——closed 30 天后照常 archive_thread 留桩）
    动作均账本记 prune。返回 {"dormant": [tid…], "closed": [tid…]}。
    """
    import datetime
    import glob
    day = day or _date.today().isoformat()
    today = datetime.date.fromisoformat(day)
    out = {"dormant": [], "closed": []}
    for f in sorted(glob.glob(os.path.join(threads_dir, "*.yaml"))):
        try:
            s = _read(f)
        except Exception:
            continue
        st = front_field(s, "status")
        if st not in ("open", "dormant"):
            continue
        date_field = "last_seen" if st == "open" else "dormant"
        ls = str(front_field(s, date_field) or "")
        try:
            d0 = datetime.date.fromisoformat(ls[:10])
        except ValueError:
            continue  # 日期缺失/非法由 audit 报错，sweep 不猜
        limit = dormant_days if st == "open" else close_days
        if (today - d0).days <= limit:
            continue
        tid = front_field(s, "id") or os.path.basename(f)[:-5]
        with _write_lock(f):
            s = _read(f)  # 锁内重读：与夜间折叠并发时以锁内状态为准
            st = front_field(s, "status")
            if st not in ("open", "dormant"):
                continue
            old_digest = front_field(s, "digest") or ""
            if st == "open":
                new_s = _set_front_line(s, "status", "status: dormant")
                marker = 'dormant: "%s"' % day
                m = re.search(r"^last_seen:.*$", new_s, re.M)
                if re.search(r"^dormant:.*$", new_s, re.M):
                    new_s = _set_front_line(new_s, "dormant", marker)
                elif m:
                    new_s = new_s[:m.end()] + "\n" + marker + new_s[m.end():]
                else:
                    new_s = new_s.rstrip("\n") + "\n" + marker + "\n"
                summary = "dormant(%s，open 超 %d 天未动)" % (day, dormant_days)
                out["dormant"].append(tid)
            else:
                new_s = _set_front_line(s, "status", "status: closed")
                closure = "dormant 超 %d 天无活动自动闭合（%s）；重开即续线" % (close_days, day)
                cl_line = "closure: %s" % ("'" + closure.replace("'", "''") + "'")
                mc = re.search(r"^closed:.*$", new_s, re.M)
                anchor = re.search(r"^dormant:.*$", new_s, re.M) or mc  # 锚点行：新字段插其后
                if mc:
                    new_s = _set_front_line(new_s, "closed", 'closed: "%s"' % day)
                    if not re.search(r"^closure:.*$", new_s, re.M):
                        new_s = new_s[:anchor.end()] + "\n" + cl_line + new_s[anchor.end():]
                elif anchor:
                    new_s = new_s[:anchor.end()] + "\n" \
                        + 'closed: "%s"' % day + "\n" + cl_line + new_s[anchor.end():]
                else:
                    new_s = new_s.rstrip("\n") + "\n" \
                        + 'closed: "%s"' % day + "\n" + cl_line + "\n"
                summary = "closed(%s，dormant 超 %d 天自动闭合)" % (day, close_days)
                out["closed"].append(tid)
            if not _yaml_ok(new_s):
                raise YamlVerifyError("sweep 写入后 YAML 无法解析：" + tid)
            _write_atomic(f, new_s)
        try:  # 账本丢行不回滚档案（同 _locked_append 哲学：结论在档案里）
            ledger_append("prune", "threads/" + tid, summary[:60], [],
                          old_summary=old_digest[:60], new_summary=summary[:120],
                          day=day, ledger_path=ledger_path)
        except LedgerError:
            pass
    return out


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
