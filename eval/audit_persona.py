#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""persona-stream 一致性自检（夜间任务第 5 步调用；也可手动随时跑）。

检查项（只报告不改写——修复由蒸馏 LLM 语义判断后动手）：
  1. people.yaml 重复人名（跨条目同名 → 应合并）
  2. 冗余指针条目（relation 里写"已各自独立建档/见上方条目"却仍占一条）
  3. threads 契约：open 缺 last_seen / closed 缺 closed 或 closure / last_seen 超 14 天未动 / dormant 缺日期
  4. beliefs 契约：current 指向的版本号不存在 / versions 断号
  5. 断链：档案里引用的 persona 内部路径不存在
  6. YAML 可解析性
  7. 隐私：入库档案中的密钥/身份证/银行卡模式（邮箱与手机号仅提示）
  8. KPI 健康：行级合法性、nightly 连续 pruned=0、kind 缺失、rate 对不上、重复折叠
  9. 仓库安全：所有 git 已跟踪文件的明文密钥扫描（tests/ 合成夹具除外）
 10. DIGEST 契约：≤8 条、每条 ≤40 字、只放 open 线头；core.md AUTO 区存在
 11. 线头扩展契约：related 死链/自引用、paths 目录存在性、open 缺 digest、closed 未压缩、open 总数超顶
 12. policies 规则 invalidated 日期格式；fold_ledger.jsonl 行级合法性

退出码：0=无阻断问题（warn 允许存在），1=有 error 级问题。
用法：python eval/audit_persona.py [--quiet]
"""
import collections
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import secretscan  # noqa: E402  密钥模式/豁免名单单一来源（eval/secretscan.py）

ROOT = os.environ.get("PERSONA_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSONA = os.path.join(ROOT, "persona")
errors, warns = [], []

# 别名保留：inject._recall_clean 与单测经此引用密钥扫描（实现已收口到 secretscan）
SECRET_PATTERNS = secretscan.PATTERNS
SYNTHETIC_SECRETS = secretscan.SYNTHETIC
scan_text_for_secrets = secretscan.scan_text
SECRET_SCAN_SKIP_DIRS = (".git", ".pytest_cache", "data")


def rel(p):
    return os.path.relpath(p, ROOT).replace("\\", "/")


def check_people():
    p = os.path.join(PERSONA, "people.yaml")
    if not os.path.exists(p):
        errors.append("people.yaml 缺失")
        return
    s = open(p, encoding="utf-8").read()
    names = re.findall(r"^  - name: (.+)$", s, re.M)
    flat = []
    for n in names:
        for part in re.split(r"\s*/\s*", n):
            key = re.sub(r"[（(].*?[)）]", "", part).strip()
            if key:
                flat.append(key)
    for k, v in collections.Counter(flat).items():
        if v > 1:
            errors.append(f"people.yaml 人名重复 {v} 次：{k}（应合并为一条）")
    if not names:
        errors.append("people.yaml 无任何条目")
    # 单遍循环：条目契约 + 注入索引总预算（防再次膨胀；超限压缩 brief 而不是删人）
    idx_len = 0
    for blk in re.split(r"\n  - name: ", s)[1:]:
        nm = blk.split("\n")[0]
        idx_len += len(nm.strip()) + 4
        if re.search(r"(已各自独立建档|见上方条目|见下方条目)", blk):
            errors.append(f"people.yaml 冗余指针条目应删除：{nm[:40]}")
        # brief 契约（2026-09-10）：注入只发 name+brief 索引，缺 brief 的条目在其他会话里没有画像
        mb = re.search(r"^\s+brief:\s*(.+)$", blk, re.M)
        if not mb:
            errors.append(f"people.yaml 条目缺 brief（注入索引必需，≤150 字单行）：{nm[:40]}")
        else:
            bl = mb.group(1).strip()
            idx_len += len(bl)
            if bl.startswith("|"):
                errors.append(f"people.yaml brief 必须单行普通标量（不要块标量）：{nm[:40]}")
            elif len(bl) > 160:
                warns.append(f"people.yaml brief 过长（{len(bl)} 字>160，占用注入预算）：{nm[:40]}")
    if idx_len > 4500:
        warns.append(f"people.yaml 注入索引约 {idx_len} 字（预算 4500）——压缩 brief 或合并条目")


def check_assets():
    p = os.path.join(PERSONA, "assets.yaml")
    if not os.path.exists(p):
        warns.append("assets.yaml 缺失（无资料地图，会话找材料只能问本人）")
        return
    s = open(p, encoding="utf-8").read()
    ids = re.findall(r"^  - id: (.+)$", s, re.M)
    for k, v in collections.Counter(ids).items():
        if v > 1:
            errors.append(f"assets.yaml id 重复 {v} 次：{k}（应合并为一条）")
    for blk in re.split(r"\n  - id: ", s)[1:]:
        nm = blk.split("\n")[0][:40]
        if not re.search(r"^\s+path:\s*\S+", blk, re.M):
            errors.append(f"assets.yaml 条目缺 path：{nm}")
        mb = re.search(r"^\s+brief:\s*(.+)$", blk, re.M)
        if not mb:
            errors.append(f"assets.yaml 条目缺 brief（注入索引必需，≤80 字单行）：{nm}")
        elif mb.group(1).strip().startswith("|"):
            errors.append(f"assets.yaml brief 必须单行普通标量（不要块标量）：{nm}")
        elif len(mb.group(1).strip()) > 80:
            warns.append(f"assets.yaml brief 过长（{len(mb.group(1).strip())} 字>80，占用注入预算）：{nm}")
        if not re.search(r"^\s+evidence:", blk, re.M):
            errors.append(f"assets.yaml 条目缺 evidence（资料位置也要可溯源）：{nm}")
    if len(ids) > 12:
        warns.append(f"assets.yaml {len(ids)} 条（上限 12）——合并或移入 assets_archive.yaml")


def check_threads():
    import datetime
    today = datetime.date.today()
    files = sorted(glob.glob(os.path.join(PERSONA, "threads", "*.yaml")))
    open_count = dormant_count = 0
    for f in files:
        try:
            s = open(f, encoding="utf-8").read()
        except Exception as e:
            errors.append(f"{rel(f)} 读取失败（跳过该文件继续检查其余）: {e}")
            continue
        st = re.search(r"^status:\s*(\w+)", s, re.M)
        if not st:
            errors.append(f"{rel(f)} 缺 status")
            continue
        if st.group(1) == "open":
            open_count += 1
            m = re.search(r'^last_seen:\s*"?([\d-]+)', s, re.M)
            if not m:
                errors.append(f"{rel(f)} open 线头缺 last_seen")
            else:
                try:
                    d = datetime.date.fromisoformat(m.group(1))
                    if (today - d).days > 14:
                        warns.append(f"{rel(f)} open 线头 {(today - d).days} 天未更新（考虑闭合或复核，"
                                     f"夜间任务应把『闭合还是继续』存入 questions_pool；"
                                     f"超 21 天由 foldlib sweep 置 dormant）")
                except ValueError:
                    errors.append(f"{rel(f)} last_seen 日期格式非法：{m.group(1)}")
            if not re.search(r"^digest:\s*\S", s, re.M):
                warns.append(f"{rel(f)} open 线头缺 digest 字段（DIGEST 确定性再生的语义源，补上后由 foldlib.regen_digest 统一渲染）")
        elif st.group(1) == "dormant":
            dormant_count += 1
            # 休眠线头（foldlib.sweep_threads 产出，2026-10-05）：不进 DIGEST/注入，再折叠自动唤醒
            if not re.search(r'^dormant:\s*"?[\d-]+', s, re.M):
                errors.append(f"{rel(f)} dormant 线头缺 dormant 日期（应由 sweep_threads 写入）")
            if not re.search(r"^digest:\s*\S", s, re.M):
                warns.append(f"{rel(f)} dormant 线头缺 digest 字段（唤醒前最后一次摘要，便于检索）")
        else:
            if not re.search(r'^closed:\s*"?[\d-]+', s, re.M):
                errors.append(f"{rel(f)} closed 线头缺 closed 日期")
            else:
                d = datetime.date.fromisoformat(re.search(r'^closed:\s*"?([\d-]+)', s, re.M).group(1))
                if (today - d).days > 30 and os.path.getsize(f) > 2048:
                    warns.append(f"{rel(f)} closed 已 {(today - d).days} 天且正文 {os.path.getsize(f)}B 未压缩"
                                 f"（foldlib.archive_thread 迁移 _archive 留桩）")
            if not re.search(r"^closure:\s*\S+", s, re.M):
                errors.append(f"{rel(f)} closed 线头缺 closure（schema 契约：结局一句话）")
        # related 交叉引用契约（A-MEM 式连边）
        m = re.search(r"^related:\s*\[(.*?)\]", s, re.M)
        if m:
            my_id = re.search(r"^id:\s*(\S+)", s, re.M)
            my_id = my_id.group(1) if my_id else os.path.basename(f)[:-5]
            for r in re.findall(r"[\"']?([\w\-]+)[\"']?", m.group(1)):
                if r == my_id:
                    errors.append(f"{rel(f)} related 自引用：{r}")
                elif not os.path.exists(os.path.join(PERSONA, "threads", r + ".yaml")):
                    errors.append(f"{rel(f)} related 指向不存在的线头：{r}")
        # paths 目录存在性（位置易变，warn 督促回写）
        m = re.search(r"^paths:\s*\[(.*?)\]", s, re.M)
        if m:
            for p in re.findall(r"[\"']?(.+?)[\"']?(?:,|$)", m.group(1)):
                p = p.strip().strip("'\"")
                if p and not os.path.isdir(p):
                    warns.append(f"{rel(f)} paths 目录不存在（使用时回写或删除）：{p}")
    if open_count > 10:
        warns.append(f"open 线头 {open_count} 条（FOLD_RULES §-1 软上限 10）——夜间任务应闭合/归档最弱者")
    if dormant_count > 8:
        warns.append(f"dormant 线头 {dormant_count} 条（软上限 8）——foldlib sweep 60 天自动闭合"
                     f"兜底前，夜间任务应先复核可否直接关闭")


def check_beliefs():
    for f in sorted(glob.glob(os.path.join(PERSONA, "beliefs", "*.yaml"))):
        s = open(f, encoding="utf-8").read()
        vs = [int(x) for x in re.findall(r"^\s*-\s*v:\s*(\d+)", s, re.M)]
        cur = re.search(r"^current:\s*(\d+)", s, re.M)
        if not vs:
            errors.append(f"{rel(f)} 无 versions 条目")
            continue
        if not cur:
            errors.append(f"{rel(f)} 缺 current")
        elif int(cur.group(1)) not in vs:
            errors.append(f"{rel(f)} current={cur.group(1)} 但存在版本为 {vs}")
        if sorted(vs) != list(range(1, max(vs) + 1)):
            warns.append(f"{rel(f)} 版本号断号：{sorted(vs)}")


def check_links():
    pat = re.compile(r"(persona[\\/][\w\-/\\\.]+\.(?:yaml|md))")
    for f in glob.glob(os.path.join(PERSONA, "**", "*"), recursive=True) + \
            glob.glob(os.path.join(ROOT, "distiller", "*.md")) + [os.path.join(ROOT, "README.md")]:
        if os.path.isdir(f):
            continue
        try:
            s = open(f, encoding="utf-8").read()
        except Exception:
            continue
        for m in set(pat.findall(s)):
            if "YYYY" in m or "MM" in os.path.basename(m):
                continue  # 文档里的模板占位路径（如 persona/journal/YYYY-MM.md）
            target = os.path.join(ROOT, m.replace("\\", os.sep).replace("/", os.sep))
            if not os.path.exists(target):
                warns.append(f"{rel(f)} 引用不存在的路径：{m}")


def check_yaml():
    try:
        import yaml
    except ImportError:
        warns.append("未安装 pyyaml，跳过 YAML 解析检查")
        return
    for f in glob.glob(os.path.join(PERSONA, "**", "*.yaml"), recursive=True):
        try:
            yaml.safe_load(open(f, encoding="utf-8").read())
        except Exception as e:
            errors.append(f"{rel(f)} YAML 解析失败：{str(e)[:80]}")


def check_privacy():
    # 密钥形态复用统一扫描（此前这里内联过第二份 sk-/ghp_/密码正则，口径漂移源）；
    # 身份证/银行卡是隐私层独有面。persona 同时被 check_repo_secrets 覆盖，双闸从两个角度钉
    hard = [(r"\b\d{17}[\dXx]\b", "身份证"), (r"\b\d{16,19}\b", "银行卡号")]
    soft = [(r"\b1[3-9]\d{9}\b", "手机号"), (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "邮箱")]
    for f in glob.glob(os.path.join(PERSONA, "**", "*"), recursive=True):
        if os.path.isdir(f):
            continue
        try:
            s = open(f, encoding="utf-8").read()
        except Exception:
            continue
        for name in set(scan_text_for_secrets(rel(f), s)):
            errors.append(f"{rel(f)} 含{name}明文（必须脱敏）")
        for p, name in hard:
            if re.search(p, s):
                errors.append(f"{rel(f)} 含{name}明文（必须脱敏）")
        for p, name in soft:
            n = len(re.findall(p, s))
            if n:
                warns.append(f"{rel(f)} 含 {n} 处{name}（确认是否必要）")


def check_kpi():
    p = os.path.join(ROOT, "eval", "kpi.jsonl")
    if not os.path.exists(p):
        warns.append("kpi.jsonl 不存在")
        return
    lines = open(p, encoding="utf-8").read().splitlines()
    rows = []
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            assert isinstance(r, dict)
        except Exception:
            errors.append(f"kpi.jsonl 第 {i} 行不是合法 JSON 对象（损坏/手工改坏）")
            continue
        for k in ("date", "kind", "signals", "pruned"):
            if k not in r:
                errors.append(f"kpi.jsonl 第 {i} 行（{r.get('date', '?')}）缺字段 {k}")
        rows.append(r)
    miss = sum(1 for r in rows if "kind" not in r)
    if miss:
        warns.append(f"kpi.jsonl 有 {miss} 行缺 kind 字段（趋势图会混入不可比数据）")
    nightly = [r for r in rows if r.get("kind", "nightly") == "nightly"]
    for r in nightly:
        # 2026-10-06 起 nightly 行必须带质量指标字段（metrics.py 产出，FOLD_RULES §8）
        if r.get("date", "") >= "2026-10-06" and ("coverage" not in r or "backlink" not in r):
            warns.append(f"kpi {r.get('date')} nightly 行缺 coverage/backlink 字段"
                         f"（折叠前先跑 python eval/metrics.py 并抄入）")
    streak = 0
    for r in reversed(nightly):
        if r.get("pruned", 0) == 0:
            streak += 1
        else:
            break
    if streak >= 7:
        errors.append(f"KPI 减法失效：最近 {streak} 次 nightly 折叠 pruned 均为 0（FOLD_RULES §8 要求不应长期为 0）")
    elif streak >= 3:
        warns.append(f"KPI 减法偏弱：最近 {streak} 次 nightly 折叠 pruned 为 0")
    for r in nightly:
        sig = r.get("signals", 0)
        if sig > 50000:
            warns.append(f"kpi {r.get('date')} nightly signals={sig} 异常大（原始扫描量应记 note、kind 应为 mining）")
        # rate 必须与 corrections/signals 对得上（容差 0.02）
        if "rate" in r and "corrections" in r and sig:
            expect = r["corrections"] / max(sig, 1)
            if abs(float(r["rate"]) - expect) > 0.02:
                warns.append(f"kpi {r.get('date')} rate={r['rate']} 与 corrections/signals={expect:.3f} 不符")
    # 同日同 signals 的 nightly 行 → 疑似重复折叠（幂等守卫漏网）
    seen = {}
    for r in nightly:
        key = (r.get("date"), r.get("signals"))
        if key in seen:
            errors.append(f"kpi {r.get('date')} 出现两条 signals={r.get('signals')} 的 nightly 行（疑似重复折叠）")
        seen[key] = True


def iter_git_tracked():
    """git 已跟踪文件；git 不可用时退化为目录遍历（跳过 data/.git/tests）"""
    try:
        out = subprocess.run(["git", "-c", "core.quotepath=false", "-C", ROOT, "ls-files"],
                             capture_output=True,
                             text=True, timeout=15, check=True).stdout
        return [os.path.join(ROOT, x) for x in out.splitlines() if x.strip()]
    except Exception:
        files = []
        for dp, dn, fn in os.walk(ROOT):
            dn[:] = [d for d in dn if d not in SECRET_SCAN_SKIP_DIRS]
            for n in fn:
                files.append(os.path.join(dp, n))
        return files


def scan_text_for_secrets(relname, text):
    """供单测复用：返回命中的密钥说明列表（tests/ 合成夹具由调用方排除）"""
    hits = []
    for pat, name in SECRET_PATTERNS:
        if re.search(pat, text):
            hits.append(name)
    return hits


def check_repo_secrets():
    for f in iter_git_tracked():
        parts = rel(f).split("/")
        if parts[0] in SECRET_SCAN_SKIP_DIRS:
            continue
        if os.path.isdir(f) or os.path.getsize(f) > 2_000_000:
            continue
        try:
            with open(f, "rb") as fh:
                raw = fh.read()
            if b"\x00" in raw[:4096]:
                continue  # 二进制文件
            text = raw.decode("utf-8", "replace")
        except Exception:
            continue
        text = secretscan.strip_synthetic(text)
        for name in scan_text_for_secrets(rel(f), text):
            errors.append(f"{rel(f)} 含明文{name}（入库文件禁止密钥；历史泄露见 docs/SECURITY.md）")


def check_policies_lifecycle():
    """policies 规则软失效字段（foldlib.invalidate_rule 写入）的格式检查。"""
    import datetime
    for f in sorted(glob.glob(os.path.join(PERSONA, "policies", "*.yaml"))):
        s = open(f, encoding="utf-8").read()
        for m in re.finditer(r"^(\s+)invalidated:\s*[\"']?([\d-]{10})", s, re.M):
            try:
                datetime.date.fromisoformat(m.group(2))
            except ValueError:
                errors.append(f"{rel(f)} invalidated 日期非法：{m.group(2)}")


def check_fold_ledger():
    """折叠账本（data/fold_ledger.jsonl，gitignore）行级合法性——精确幂等的数据基础。"""
    p = os.path.join(ROOT, "data", "fold_ledger.jsonl")
    if not os.path.exists(p):
        return  # 账本自 2026-10-02 起建，历史折叠不回填；缺失不算错
    for i, line in enumerate(open(p, encoding="utf-8").read().splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            assert isinstance(r, dict)
        except Exception:
            errors.append(f"fold_ledger.jsonl 第 {i} 行不是合法 JSON 对象")
            continue
        if r.get("action") not in ("add", "update", "prune", "invalidate"):
            errors.append(f"fold_ledger.jsonl 第 {i} 行非法 action：{r.get('action')}")
        ids = r.get("event_ids") or []
        if not isinstance(ids, list) or any(not re.fullmatch(r"[0-9a-f]{12}", str(x)) for x in ids):
            errors.append(f"fold_ledger.jsonl 第 {i} 行 event_ids 含非法 id")


def check_digest_and_core():
    d = os.path.join(PERSONA, "threads", "DIGEST.md")
    if not os.path.exists(d):
        warns.append("threads/DIGEST.md 缺失（SessionStart 注入会少一块）")
    else:
        lines = [l for l in open(d, encoding="utf-8").read().splitlines()
                 if re.match(r"^\s*-\s*\[", l)]
        if len(lines) > 8:
            errors.append(f"DIGEST.md 有 {len(lines)} 条（上限 8，超的留给按需读取）")
        for l in lines:
            body = re.sub(r"^\s*-\s*\[(open|closed)\]\s*", "", l)
            if len(body) > 40:
                errors.append(f"DIGEST.md 条目超 40 字（{len(body)} 字）：{body[:24]}…")
            if re.match(r"^\s*-\s*\[closed\]", l):
                warns.append("DIGEST.md 含 closed 条目（契约只放 open；闭合历史在线头文件里）")
    c = os.path.join(PERSONA, "core.md")
    if not os.path.exists(c) or not os.path.getsize(c):
        errors.append("core.md 缺失或为空（注入会静默退化）")
    else:
        s = open(c, encoding="utf-8").read()
        if "AUTO:BEGIN" not in s or "AUTO:END" not in s:
            warns.append("core.md 缺 AUTO:BEGIN/END 标记（夜间任务无法安全重写自动区）")


def main():
    quiet = "--quiet" in sys.argv
    for fn in (check_people, check_assets, check_threads, check_beliefs, check_links,
               check_yaml, check_privacy, check_kpi, check_policies_lifecycle,
               check_fold_ledger, check_repo_secrets, check_digest_and_core):
        try:
            fn()
        except Exception as e:
            errors.append(f"{fn.__name__} 自身异常：{type(e).__name__}: {e}")
    if errors:
        print("ERROR (%d)：" % len(errors))
        for e in errors:
            print("  ✗", e)
    if warns and not quiet:
        print("WARN (%d)：" % len(warns))
        for w in warns:
            print("  ·", w)
    if not errors and not warns:
        print("档案自检通过：无 error 无 warn")
    elif not errors:
        print("档案自检通过（仅 %d 条 warn）" % len(warns))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
