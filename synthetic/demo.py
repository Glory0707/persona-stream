#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""demo：一键复现「从事件流长出人格档案」的全链路（生成 → 折叠 → 三闸门 → 注入）。

断言即文档：每个 ✓ 都是一条行为契约——读者不读源码也能看懂闭环：
  正样本：协议沉淀、规则推翻、线头生命周期、证据回链、注入可见性……
  负样本：真实人名/本机私有路径/密钥形态在产物里必须零命中（隐私纪律自证）。

用法：python synthetic/demo.py [--seed 7]   （产物在 demo-home/，gitignored）
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys

try:  # CI 控制台常为 cp1252/ascii：全输出强制 UTF-8（py3.7+ reconfigure）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.join(REPO, "demo-home")

AP = argparse.ArgumentParser()
ARGS, _rest = AP.parse_known_args()
SEED = getattr(ARGS, "seed", 7)

# PERSONA_HOME 必须先于任何框架模块 import（ROOT 在 import 时刻定型）
os.environ["PERSONA_HOME"] = HOME
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "collector"))
sys.path.insert(0, os.path.join(REPO, "distiller"))
sys.path.insert(0, os.path.join(REPO, "eval"))


def s(*codes):
    return "".join(map(chr, codes))


# 负样本黑名单（拆码构造：本文件也在被扫描之列，不能自证命中）
BLACKLIST = [
    "D:" + os.sep + "persona-stream",          # 私有仓本机路径
    "C:" + os.sep + "Users",                   # 用户目录
    s(76, 101, 110, 111, 118, 111),            # 主机身份
    s(71, 108, 111, 114, 121),                 # 作者 GitHub id 片段
    s(21608, 22885),                           # 作者真名
]

RESULTS = []


def check(desc, fn):
    try:
        fn()
        RESULTS.append((True, desc, ""))
        print("  ✓ %s" % desc)
    except AssertionError as e:
        RESULTS.append((False, desc, str(e)))
        print("  ✗ %s\n      %s" % (desc, e))


def run(cmd):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")  # 子进程输出同样强制 UTF-8
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return r


def main():
    import generate  # noqa: F401  确认 env 先行（模块顶部即校验 PERSONA_HOME 语义）
    import distill_demo
    import inject
    import check_events
    import check_injection
    import audit_persona

    if os.path.exists(HOME):
        shutil.rmtree(HOME)
    os.makedirs(HOME)

    print("== 1/5 生成合成事件流（虚构人物「林小测」，seed=%d） ==" % SEED)
    r = run([sys.executable, os.path.join(REPO, "synthetic", "generate.py"),
             "--home", HOME, "--seed", str(SEED)])
    print(r.stdout.strip())
    assert r.returncode == 0, r.stderr[-800:]

    print("== 2/5 确定性折叠（走 foldlib 受控写路径 + 折叠账本） ==")
    sys.path.insert(0, os.path.join(REPO, "synthetic"))
    summary = distill_demo.Distiller(HOME).run()
    print("sweep=%s" % json.dumps(summary["swept"], ensure_ascii=False))
    print("kpi=%s" % json.dumps(summary["kpi"], ensure_ascii=False))

    print("== 3/5 事件索引（FTS5） ==")
    r = run([sys.executable, os.path.join(REPO, "eval", "build_index.py"), "--quiet"])
    assert r.returncode == 0, r.stderr[-500:]

    print("== 4/5 三闸门 ==")
    for name, script in (("check_events", "eval/check_events.py"),
                         ("audit_persona", "eval/audit_persona.py"),
                         ("check_injection", "eval/check_injection.py")):
        r = run([sys.executable, os.path.join(REPO, script), "--quiet"])
        print("  %s: exit=%d %s" % (name, r.returncode, r.stdout.strip()[:160]))
        assert r.returncode == 0, "%s 未通过：\n%s" % (name, (r.stdout + r.stderr)[-800:])

    tide_dir = os.path.join(HOME, "projects", "tide-forecast")
    core_path = os.path.join(HOME, "persona", "core.md")
    threads_dir = os.path.join(HOME, "persona", "threads")
    csv_path = os.path.join(tide_dir, "data", "tides.csv")
    ctx = inject.build_context(cwd=tide_dir)

    print("== 5/5 行为断言（正样本 = 契约；负样本 = 隐私纪律） ==")

    # ---- 注入可见性（写侧成功 ≠ 接收方可见，逐层验证） ----
    check("注入含【人格内核】与【记忆召回】等全部层级", lambda: (
        [m for m in ("人格内核", "人格速览", "未闭合线头", "人物索引", "资料地图",
                     "口令协议", "本项目线头", "记忆召回") if m not in ctx] and 1 / 0 or None))
    check("口令协议 when「开工」可见且 evidence 哈希已剥离", lambda: (
        None if ("开工" in ctx and "evidence:" not in ctx) else 1 / 0))
    check("人物索引注入 name+brief（陈远/苏芮）", lambda: (
        None if ("陈远" in ctx and "苏芮" in ctx and "demo 合成人物" in ctx) else 1 / 0))
    check("资料地图注入 tides.csv 路径", lambda: (
        None if csv_path in ctx else 1 / 0))
    check("cwd 定向命中毕设线头（digest 进注入）", lambda: (
        None if "潮汐预测：基线已跑通" in ctx else 1 / 0))
    check("记忆召回召回近期 tide 事件", lambda: (
        None if any(k in ctx for k in ("数据集到手", "基线模型跑通")) else 1 / 0))

    # ---- 折叠产物契约 ----
    def _digest_open():
        lines = [l for l in open(os.path.join(threads_dir, "DIGEST.md"), encoding="utf-8")
                 .read().splitlines() if l.strip().startswith("- [open]")]
        assert len(lines) == 1 and "潮汐预测" in lines[0], lines
    check("DIGEST 只含 1 条 open（毕设；dormant/closed 不进 DIGEST）", _digest_open)

    def _lifecycle():
        novel = open(os.path.join(threads_dir, "novel-starbarge.yaml"), encoding="utf-8").read()
        club = open(os.path.join(threads_dir, "reading-club.yaml"), encoding="utf-8").read()
        assert "status: dormant" in novel and "dormant: \"" in novel, "连载线头未休眠"
        assert "status: closed" in club and "closure:" in club, "读书会未闭合"
    check("线头生命周期：35 天未动 → dormant；办完 → closed+closure", _lifecycle)

    def _invalidated():
        comms = open(os.path.join(HOME, "persona", "policies", "comms.yaml"),
                     encoding="utf-8").read()
        assert "invalidated:" in comms, comms
    check("纠错对推翻旧规则（软失效保留原文）", _invalidated)

    def _beliefs():
        bl = open(os.path.join(HOME, "persona", "beliefs", "language.yaml"),
                  encoding="utf-8").read()
        assert "current: 2" in bl and "v: 1" in bl, bl
    check("beliefs 版本链 v1→v2，current 指向最新", _beliefs)

    def _ledger():
        rows = [json.loads(l) for l in open(os.path.join(HOME, "data", "fold_ledger.jsonl"),
                                            encoding="utf-8") if l.strip()]
        assert len(rows) >= 6, "账本行数 %d" % len(rows)
        assert all(all(len(i) == 12 for i in r["event_ids"]) for r in rows if r["event_ids"])
    check("折叠账本 ≥6 行且 event_ids 全为 12-hex", _ledger)

    def _metrics():
        m = summary["metrics"]
        assert m["broken"] == [], m["broken"]
        assert m["backlink"] == 1.0, m["backlink"]
        assert (m["coverage"] or 0) >= 0.05, m["coverage"]
    check("metrics：broken=0、backlink=1.0、coverage 达标（趋势口径）", _metrics)

    # ---- 隐私纪律（负样本：这条线 fail 就是泄漏） ----
    def _no_leak():
        for dp, dn, fn in os.walk(HOME):
            dn[:] = [d for d in dn if d not in (".git", "__pycache__")]
            for n in fn:
                p = os.path.join(dp, n)
                text = open(p, encoding="utf-8", errors="replace").read().lower()
                for b in BLACKLIST:
                    assert b.lower() not in text, "%s 含 %r" % (os.path.relpath(p, HOME), b)
    check("负样本：产物全树无真实人名/私有路径（黑名单零命中）", _no_leak)

    def _secret_gone():
        raw = "".join(open(f, encoding="utf-8", errors="replace").read()
                      for f in glob.glob(os.path.join(HOME, "data", "events-*.jsonl")))
        assert "sk-demokey" not in raw and "sk-***" in raw, "脱敏样本未按预期处理"
        assert audit_persona.scan_text_for_secrets("demo", raw) == [], "原始层残留密钥形态"
    check("负样本：假密钥在采集层被脱敏（sk-***），原始层零密钥形态", _secret_gone)

    def _noise_not_in_persona():
        raw = "".join(open(f, encoding="utf-8", errors="replace").read()
                      for f in glob.glob(os.path.join(HOME, "data", "events-*.jsonl")))
        assert "你好" in raw, "噪声样本未落盘（打标不丢弃）"
        core = open(core_path, encoding="utf-8").read()
        assert "你好" not in core, "噪声污染了画像"
        tagged = [json.loads(l) for l in raw.splitlines() if l.strip()]
        assert any("noise_like" in (e.get("tags") or []) for e in tagged)
    check("负样本：噪声打标照记（noise_like）但不入画像", _noise_not_in_persona)

    # ---- 汇总 ----
    failed = [r for r in RESULTS if not r[0]]
    print("\n== 结果：%d/%d 断言通过 ==" % (len(RESULTS) - len(failed), len(RESULTS)))
    print("\n---- SessionStart 注入全文（截取前 2200 字，完整版见运行输出）----\n")
    print(ctx[:2200])
    print("\n---- 怎么读这份输出 ----")
    print("  上面的注入文本就是每个新会话开场时 agent 看到的全部人格上下文：")
    print("  内核/速览来自 persona 层，线头/召回按 cwd 定向，全部结论可凭")
    print("  evidence 事件 id 经 eval/search_events.py 回溯到原始事件。")
    print("  产物目录：demo-home/（data/ 原始层 + persona/ 沉淀层，均已 gitignore）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
