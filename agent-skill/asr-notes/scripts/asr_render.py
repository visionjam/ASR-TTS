#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown 渲染层：front matter、四个产出文件、清洗稿重排。

只接数据（meta dict / chunks 列表），不联网、不读配置。
唯一的例外是 write_cleaned 需要知道用哪个模型名写进 front matter：
它自己不认识 config.env，所以由调用方通过 model= 传进来。
这一层是纯函数，tests/t_core.py 的往返用例直接拿它当库用。
"""

import json
import re
from pathlib import Path

from asr_common import fmt


# ------------------------------------------------------------------ 三个文件

def yaml_val(v):
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(str(x) for x in v) + "]"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if re.search(r"[:#\[\]{}&*?|>\"']", s) or s != s.strip() or s.startswith("-"):
        return json.dumps(s, ensure_ascii=False)
    return s


def front_matter(d):
    lines = ["---"]
    for k, v in d.items():
        if v is None or v == "":
            continue
        lines.append("%s: %s" % (k, yaml_val(v)))
    lines.append("---")
    return "\n".join(lines)


def write_index(folder, meta, chunks, summary):
    cost = float(meta.get("asr_cost") or 0)
    sub = meta.get("input") == "subtitle"
    body = [
        "# %s" % meta["title"],
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 时长 | %s |" % meta["duration"],
        "| 类型 | %s |" % ("自语" if meta.get("kind", "self") == "self" else "外部音频"),
    ]
    if sub:
        body += ["| 来源 | %s · %s 段（未走 ASR，无转写费）|"
                 % (meta["source"], meta["asr_chunks"])]
    else:
        body += ["| 录音 | %s |" % meta["captured_at"],
                 "| 转写 | %s · %s 片 · ¥%.3f |"
                 % (meta["asr_model"], meta["asr_chunks"], cost)]
    body += [
        "| 整理 | %s |" % meta["llm_model"],
        "| 状态 | %s |" % meta["status"],
        "",
    ]
    if summary:
        body += ["## 一句话", "", summary, ""]
    body += ["## 文稿", "",
             "- [[cleaned|清洗稿]]：%s（读这个最快）"
             % ("分段重排后的字幕" if sub else "去了口水词，保留原顺序"),
             "- [[transcript|逐字稿]]：%s（核对原话用这个）"
             % ("字幕原样，只做了去重和分段" if sub else "ASR 原样输出"),
             "- [[notes|整理稿]]：AI 结构化，可继续编辑",
             ""]
    text = front_matter(meta) + "\n\n" + "\n".join(body)
    (folder / "index.md").write_text(text, encoding="utf-8")


def write_text_index(folder, meta, summary):
    """--text 路径的卡片：没有音频，所以不写转写字段"""
    head = front_matter({
        "id": meta["id"],
        "title": meta["title"],
        "type": "voice-index",
        "date": meta["date"],
        "source": meta["source"],
        "notes": "[[notes]]",
        "llm_model": meta["llm_model"],
        "status": "draft",
        "generated_at": meta["generated_at"],
        "tags": ["语音", "文本"],
    })
    body = ["# %s" % meta["title"], "",
            "| 项 | 值 |",
            "|---|---|",
            "| 来源 | 输入法文本 %s |" % meta["source"],
            "| 整理 | %s |" % meta["llm_model"],
            "| 状态 | draft |",
            ""]
    if summary:
        body += ["## 一句话", "", summary, ""]
    body += ["## 文稿", "",
             "- [[notes|整理稿]]：AI 结构化，可继续编辑",
             ""]
    (folder / "index.md").write_text(head + "\n\n" + "\n".join(body), encoding="utf-8")


def write_transcript(folder, meta, chunks):
    head = front_matter({
        "id": meta["id"],
        "type": "transcript",
        "status": "final",
        "source": meta["source"],
        "duration": meta["duration"],
        "duration_sec": meta["duration_sec"],
        "asr": meta["asr_model"],
        "asr_chunks": meta["asr_chunks"],
        "asr_cost_cny": meta["asr_cost"],
        "generated_at": meta["generated_at"],
    })
    body = ["", "# 逐字稿", ""]
    for start, _, text in chunks:
        body += ["### %s" % fmt(start), "", text.strip(), ""]
    (folder / "transcript.md").write_text(head + "\n" + "\n".join(body), encoding="utf-8")


def write_notes(folder, meta, body, based="[[transcript]]"):
    head = front_matter({
        "id": meta["id"],
        "type": "notes",
        "status": meta["status"],
        "llm": meta["llm_model"],
        "based_on": based,
        "generated_at": meta["generated_at"],
        "tags": ["语音"],
    })
    (folder / "notes.md").write_text(head + "\n\n" + body.strip() + "\n", encoding="utf-8")


# 句末标点（含紧跟的收尾引号/括号），用于在片段内部重新分段
SENT_RE = re.compile(r"[^。！？；!?;…\n]+[。！？；!?;…]+[”’」』）)】]*|[^。！？；!?;…\n]+$")
TS_LINE = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)$")


def reflow(text, target=180):
    """把一段长文按句末标点重新分段，每段 120–220 字

    纯本地处理，不花 token。目的不是"排版好看"，是让 800 字一段变成人
    读得下去的段落——分片边界对机器有意义，对眼睛没有。
    """
    out = []
    for para in text.splitlines():
        para = para.strip()
        if not para:
            continue
        sents = [s.strip() for s in SENT_RE.findall(para) if s.strip()]
        buf = ""
        for s in sents:
            if buf and len(buf) + len(s) > target:
                out.append(buf)
                buf = s
            else:
                buf += s
        if buf:
            out.append(buf)
    return "\n\n".join(out)


def cleaned_blocks(text):
    """把 "[mm:ss] 文本" 的清洗稿切成 (时间戳, 文本) 列表

    模型偶尔会丢掉时间戳，这时整段落进上一个块；一块都没有就返回 [("", 全文)]。
    """
    blocks = []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = TS_LINE.match(line.strip())
        if m:
            blocks.append([m.group(1), m.group(2).strip()])
        elif blocks:
            blocks[-1][1] += line.strip()
        else:
            blocks.append(["", line.strip()])
    return [(ts, t) for ts, t in blocks]


def write_cleaned(folder, meta, text, llm="auto", model=None):
    """写 cleaned.md

    llm="auto" 表示"用实际清洗它的模型名"，来源依次是 meta 里的 llm_model、
    调用方传进来的 model（asr.py 传 CFG["LLM_MODEL"]）。显式写死的情况只有
    一处：字幕模式把 llm 传成"未清洗（字幕原文）"，因为它压根没调模型。
    """
    head = front_matter({
        "id": meta["id"],
        "type": "cleaned",
        "status": "generated",
        "based_on": "[[transcript]]",
        "llm": (llm if llm != "auto" else (meta.get("llm_model") or model or "")),
        "generated_at": meta["generated_at"],
        "note": meta.get("clean_note")
        or "去口水词、修同音字、补标点；顺序与逐字稿一致，段落按句末标点重排",
    })
    body = ["# 清洗稿", ""]
    for ts, t in cleaned_blocks(text):
        if not t.strip():
            continue
        if ts:
            body += ["### %s" % ts, ""]
        body += [reflow(t), ""]
    (folder / "cleaned.md").write_text(head + "\n\n" + "\n".join(body), encoding="utf-8")
