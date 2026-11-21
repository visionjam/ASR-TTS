#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验层：清洗稿体检 + 摘引逐字核对。

两块放在一起，是因为它们回答同一个问题——模型说的这句，原文里到底有没有？
清洗稿掉字、标了【原话】的那句其实被改写，都是模型在"顺手帮你整理"，
只看产出看不出来，必须由脚本按字面比对。
"""

import re

from asr_common import log


# ------------------------------------------------------------------ 校验

def check_cleaned(ts_list, src_text, clean_text):
    """清洗稿的体检：字数掉太多 / 丢了片段，都要说出来

    ASR 会吞句子，清洗模型会顺手删句子，两者都表现为"字数变少"。
    不设阈值就是静默丢内容——稿子少了一段，你也不会去数。
    """
    src = _norm(src_text)
    got = _norm(clean_text)
    ratio = len(got) / max(1, len(src))
    present = set(re.findall(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", clean_text))
    present |= set(re.findall(r"^###\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*$",
                              clean_text, flags=re.M))
    missing = [ts for ts in ts_list if ts not in present]
    return ratio, missing


def report_cleaned(ts_list, src_text, clean_text):
    """打印体检结果，返回是否正常"""
    ratio, missing = check_cleaned(ts_list, src_text, clean_text)
    ok = True
    if ratio < 0.80:
        log("  ⚠ 清洗稿只有原文的 %.0f%%（正常 ≥88%%），可能吞了句子：见 cleaned.md" % (ratio * 100))
        ok = False
    if missing:
        log("  ⚠ %d 个片段没时间戳（%s），可能整段丢了"
            % (len(missing), "、".join(missing[:4]) + ("…" if len(missing) > 4 else "")))
        ok = False
    return ok


def _norm(s):
    """去掉标点空白，只留字，用于比对原文"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", s).lower()


def longest_run(t, src):
    """t 在 src 里最长连续匹配多少字"""
    n = len(t)
    for L in range(n, 11, -1):
        for i in range(n - L + 1):
            if t[i:i + L] in src:
                return L
    return 0


# 模型给的原文摘引：（原文：「……」）或 (原文: "…")
QUOTE_RE = re.compile(r"[（(]\s*(?:原文|原话)\s*[：:]\s*[\"“「『'](.+?)[\"”」』']\s*[)）]\s*$")


def verify_quote(quote, src):
    """摘引是不是真的逐字来自原文

    要求整句在原文里连续存在，只容忍 1 个字的差别（ASR 与清洗稿之间的个别错字）。
    不用比例阈值：85% 会让「原文前半句 + 模型自己补的主语」通过，那种拼接出来的
    引文看着像原话，实际是半句真半句编，比标 [归纳] 更危险。

    返回 (是否通过, 最长连续命中字数)。
    """
    q = _norm(quote)
    if len(q) < 6:
        return False, 0
    if q in src:
        return True, len(q)
    return False, (longest_run(q, src) if len(q) > 12 else 0)


def stamp_quotes(body, source):
    """给「核心观点 / 关键论点」的每条自动标 [原话] 或 [归纳]

    两段判据，模型摘句 + 脚本校验：

    1. 模型给了（原文："……"）→ 校验这句是否真能在原文里找到。
       通过标 [原话]（措辞是说话人本人的），不通过标 [归纳] 并计数。
    2. 模型没给摘引 → 退回机械判据：本条有没有一段连续 60%（至少 12 字）
       的内容能在原文里原样找到。

    为什么不靠模型自评：模型几乎总认为自己是原话。但模型摘出来的那句话
    是真是假，脚本可以逐字验。
    """
    src = _norm(source)
    heads = ("## 核心观点", "## 关键论点")
    in_sec, out, nq, ns, bad, worst = False, [], 0, 0, 0, 0
    for line in body.splitlines():
        s = line.rstrip()
        if s.startswith("## "):
            in_sec = s.startswith(heads)
            out.append(s)
            continue
        if in_sec and s.lstrip().startswith(("- ", "* ")):
            raw = re.sub(r"\s*\[(原话|归纳)\]\s*$", "", s).rstrip()
            claim = raw.lstrip()[2:].strip()
            m = QUOTE_RE.search(claim)
            if m:
                quote = m.group(1).strip()
                ok, run = verify_quote(quote, src)
                head = claim[:m.start()].strip().rstrip("。；;，,、 ")
                if ok:
                    out.append("- %s（原文：%s） [原话]" % (head, quote))
                    nq += 1
                else:
                    out.append("- %s [归纳]" % head)
                    ns += 1
                    bad += 1
                    worst = max(worst, run)
                continue
            text = _norm(claim)
            run = longest_run(text, src) if text else 0
            if text and run >= max(12, int(len(text) * 0.6)):
                out.append(raw + " [原话]")
                nq += 1
            else:
                out.append(raw + " [归纳]")
                ns += 1
            continue
        out.append(s)
    if bad:
        log("  ⚠ %d 条摘引没通过逐字核对（最长只对上 %d 字），已降级为 [归纳]"
            % (bad, worst))
    return "\n".join(out), nq, ns
