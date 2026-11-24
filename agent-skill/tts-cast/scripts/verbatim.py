#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读法层：把引擎念不出来的写法，换成该念出来的词。

三条纪律：

    纯函数   —— 同样的输入永远同样的输出，没有网络、没有随机、没有时间
    幂等     —— 输出里不再有会被规则二次命中的写法，跑两遍等于跑一遍
    不改义   —— 只动读法（符号怎么念），不改观点、不改句子结构、不扩写

三种处理，别混：

    read    换读法：→ 读成「到」，≥ 读成「大于等于」
    strip   删标记：[原话]、<!-- 注释 --> 这类念出来是噪音、删了不丢信息的
    move    摘出来：代码、公式、表格、长 URL、括注，正文不念，进原文附录

## 规则表为什么这么短

2026-11-24 拿二十条短句在豆包 2.0 上实测过（tests/verbalize/），结论是
这家引擎比想象中能干，下面这些**本来就读对，所以不做任何处理**：

    2025 年 / 5601 人      年份和数量按数值读
    80% / 7.67%            读成「百分之八十」「百分之七点六七」
    1/10                   读成「十分之一」
    12.5 亿元              读成「十二点五亿元」
    RTX 5090 / GPT-4       型号和版本读得对
    iPhone 15              读成「十五」
    NASA / AI / GDP        按字母读
    120km/h / 3000m²       读成「公里每小时」「平方米」
    -5℃ / 3~5 天 / ±3      单位、范围、正负都读得对
    2026/11/20             读成「十一月二十日」，不会被当除法
    《理性》 / ——          书名号、破折号都不念出来

唯一实测念错的是 `→`，它被读成「右箭头」。所以读数规则只剩一条。

留着 ≥ ≤ & 是因为没测过，改动小、方向安全；换 provider（DeepSeek）时
这张表要重测一遍，别把结论当永久事实。

单独跑（不联网、不花钱、不写文件）：
    python3 verbatim.py --diff out/2026-11-20-某篇/script.md
"""

import argparse
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

DICT_NAME = "verbalize.tsv"     # 放库根，一行「原文<tab>读法」，随 Syncthing 同步

# (名字, 正则, 处理)
# 顺序即执行顺序：先摘大块，再处理残留的标记和符号。
RULES = [
    ("html注释",   re.compile(r"<!--.*?-->", re.S),                   "strip"),
    ("代码块",     re.compile(r"```.*?```", re.S),                    "move"),
    ("行内代码",   re.compile(r"`([^`\n]{1,80})`"),                    "move"),
    ("表格行",     re.compile(r"^\s*\|.*\|\s*$", re.M),                "move"),
    ("裸链接",     re.compile(r"https?://\S+|www\.\S+"),               "move"),
    ("公式块",     re.compile(r"\$\$.*?\$\$", re.S),                   "move"),
    ("行内公式",   re.compile(r"\$[^$\n]{1,80}\$"),                     "move"),
    ("方括号注",   re.compile(r"\[(?:原话|注|笑|掌声|此处有删节|略)\]"),  "strip"),
    ("括注原文",   re.compile(r"[（(]原文[：:][^）)]*[）)]"),              "move"),
    ("箭头",       re.compile(r"\s*(?:→|⇒|->)\s*"),                     "read"),
    ("大于等于",   re.compile(r"≥"),                                    "read"),
    ("小于等于",   re.compile(r"≤"),                                    "read"),
    ("和号",       re.compile(r"\s*&\s*"),                              "read"),
]

_READ = {
    "箭头":     " 到 ",     # A→B 是「A 到 B」，不是「A 变成 B」
    "大于等于": "大于等于",
    "小于等于": "小于等于",
    "和号":     " 和 ",
}

# 段尾常见的噪音尾巴：asr-notes 的夹注标记，摘掉后句子就没了句号
_TAIL_NOISE = re.compile(
    r"(?:\[(?:原话|注|笑|掌声|此处有删节|略)\]|[（(]原文[：:][^）)]*[）)])\s*$")


def load_dict(path=None):
    """库根的可选词典：一行「原文<tab>读法」，# 开头是注释。改一行就生效，不用动代码。"""
    d = {}
    p = Path(path) if path else None
    if p is None:
        for cand in (Path.cwd() / DICT_NAME, _HERE.parent / DICT_NAME):
            if cand.is_file():
                p = cand
                break
    if p is None or not p.is_file():
        return d
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\t+|\s{2,}", line, maxsplit=1)
        if len(parts) == 2 and parts[0]:
            d[parts[0]] = parts[1].strip()
    return d


def verbalize(text, dict_=None):
    """→ (新文本, [改动])

    每处改动记成 {"rule","kind","before","after"}，--diff 用它逐条打印。
    """
    out = text
    changes = []

    for name, pat, kind in RULES:
        def f(m, name=name, kind=kind):
            changes.append({"rule": name, "kind": kind,
                            "before": m.group(0), "after": _READ.get(name, "")})
            return _READ.get(name, "")

        out = pat.sub(f, out)

    if dict_:
        for src in sorted(dict_, key=len, reverse=True):
            if src in out:
                out = out.replace(src, dict_[src])
                changes.append({"rule": "词典", "kind": "read",
                                "before": src, "after": dict_[src]})

    # 收拾替换留下的碎空格：中文之间的多余空白会让韵律发飘，
    # 摘掉网址/公式后常留下「这儿 ，」这种空格加标点的残迹，也一并清掉
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"(?<=[\u4e00-\u9fff，。！？；：、])\s+(?=[\u4e00-\u9fff])", "", out)
    out = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[，。！？；：、）])", "", out)
    out = out.strip()

    # 摘掉段尾的「（原文：…） [原话]」后，句子可能没了终止标点，
    # 引擎会在段末拖一个上扬的尾音。补一个句号，只补段尾、只补这一种情况。
    if _TAIL_NOISE.search(text) and out and out[-1] not in "。！？…；”」":
        out += "。"
        changes.append({"rule": "补句号", "kind": "read",
                        "before": "（段尾）", "after": "。", "ctx": None})
    return out, changes


def _plan_segs(blocks):
    """按 cast.py 的口径切段编号：h 各算一段，p 先过 chunk_paragraph。

    编号必须和 build_plan 一致，否则附录里的「第 N 段」会指错地方。
    这里照抄一遍切段逻辑而不 import cast，是为了让预览不读 config、不碰网络。
    """
    from cast_text import split_sentences
    from cast_common import visual_len

    try:
        import cast as _cast
        limit = _cast.MAX_SEG_CHARS
    except Exception:
        limit = 600

    segs = []
    for bi, b in enumerate(blocks, 1):
        if b["kind"] == "h":
            segs.append({"kind": "h", "text": b["text"], "block": bi})
            continue
        t = b["text"]
        if visual_len(t) <= limit:
            segs.append({"kind": "p", "text": t, "block": bi})
            continue
        cur, acc = [], 0.0
        for s in split_sentences(t, max_cue=limit):
            w = visual_len(s)
            if acc + w > limit and cur:
                segs.append({"kind": "p", "text": "".join(cur), "block": bi})
                cur, acc = [], 0.0
            cur.append(s)
            acc += w
        if cur:
            segs.append({"kind": "p", "text": "".join(cur), "block": bi})
    return segs


def preview(path, dict_=None):
    """按段打印差异。段号与 cast.py 的 build_plan 一一对应，消息里不出现任何假时间。"""
    from cast_text import parse_article

    raw = Path(path).read_text(encoding="utf-8")
    art = parse_article(raw)
    segs = _plan_segs(art["blocks"])

    total, moved, stripped, touched = 0, [], [], 0
    print("文件：%s" % path)
    print("标题：%s" % art["title"])
    print("段数：%d（与成品段号一致，小标题也算一段）" % len(segs))
    print("")

    for no, s in enumerate(segs, 1):
        new, ch = verbalize(s["text"], dict_=dict_)
        if not ch:
            continue
        total += len(ch)
        touched += 1
        tag = "小标题" if s["kind"] == "h" else "正文"
        print("[第 %d 段·%s]" % (no, tag))
        for c in ch:
            if c["kind"] == "move":
                moved.append((no, c["before"]))
                print("  摘出 → 原文附录：%s" % c["before"][:60].replace("\n", " "))
            elif c["kind"] == "strip":
                stripped.append((no, c["before"]))
                print("  删标记：%s" % c["before"][:40].replace("\n", " "))
            else:
                print("  读法「%s」：%s  →  %s"
                      % (c["rule"], c["before"], c["after"]))
        print("")

    print("——")
    print("共 %d 段需要处理，%d 处改动：换读法 %d，删标记 %d，摘出 %d"
          % (touched, total,
             total - len(moved) - len(stripped), len(stripped), len(moved)))
    if moved:
        print("")
        print("摘出的内容会在成品目录生成「原文附录.md」，按「第 N 段（接在 mm:ss 之后）」记。")
    return total


def main():
    ap = argparse.ArgumentParser(description="读法层：预览引擎会念错的写法")
    ap.add_argument("--diff", metavar="FILE", help="只看差异，不写文件、不联网")
    ap.add_argument("--tsv", help="指定 verbalize.tsv，默认找当前目录和技能目录")
    args = ap.parse_args()

    if not args.diff:
        ap.print_help()
        return 1
    preview(args.diff, dict_=load_dict(args.tsv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
