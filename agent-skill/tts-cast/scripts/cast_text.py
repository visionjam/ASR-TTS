#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文章 → 可朗读的段落结构 → 字幕切句 → 时间轴插值。

全是纯函数，不碰网络、不碰 ffmpeg，所以可以单独跑测试：
    python3 -c "import cast_text as t; print(t.split_sentences('他说，走吧。天亮了。'))"

两层粒度是分开的，这一点别混：

    朗读粒度 = 段（一整个自然段一次合成，韵律连贯）
    字幕粒度 = 句（段内按字数比例插值，误差 ±0.3 秒）

之所以不按句合成：一句一次请求，段与段之间会断气，听感像播报机；
之所以不按段出字幕：一段 200 字会在屏幕上挂 40 秒，根本没法定位。
"""

import re

from cast_common import visual_len

MAX_CUE = 36.0        # 一条字幕最多多少「宽度单位」≈ 36 个汉字
LINE_CHARS = 18.0     # 每行最多多少，超过手动断行（mov_text 不会自动折行）

_SENT_END = "。！？；…!?;"
_SOFT_BREAK = "，、,：:"

# Markdown / 网页粘贴残留的清理顺序有讲究：先删图片，再把链接降级成文字
_RE_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_RE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_RE_FOOTNOTE = re.compile(r"\[\^[^\]]*\]")
_RE_HTML = re.compile(r"<[^>]{1,80}>")
_RE_CODE = re.compile(r"`([^`]*)`")
_RE_BOLD = re.compile(r"\*{1,3}([^*]+)\*{1,3}")
_RE_LEAD = re.compile(r"^\s*(?:>\s*|[-*+]\s+|\d+[.)]\s+)")
# script.md 自己产出的时间戳前缀。剥掉它，script.md 才能当输入喂回来
# ——“改稿后重跑”这条路全靠这一行，不然 [03:07] 会被念出来。
_RE_TS = re.compile(r"^\[\d{1,2}:\d{2}(?::\d{2})?\]\s*")
_RE_SPACES = re.compile(r"[ \t\u00a0\u3000]{2,}")


def clean_inline(s):
    s = _RE_IMG.sub("", s)
    s = _RE_LINK.sub(r"\1", s)
    s = _RE_FOOTNOTE.sub("", s)
    s = _RE_CODE.sub(r"\1", s)
    s = _RE_BOLD.sub(r"\1", s)
    s = _RE_HTML.sub("", s)
    s = _RE_LEAD.sub("", s)
    s = _RE_TS.sub("", s)
    s = s.replace("**", "").replace("__", "")
    s = _RE_SPACES.sub(" ", s)
    return s.strip().strip("*").strip()


def parse_article(raw):
    """→ {"title": str, "blocks": [{"kind": "h"|"p", "text": ...}]}

    kind=h 的小标题会：① 被朗读 ② 变成章节点。所以标题别乱加。
    """
    raw = raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")

    # YAML front matter 整块丢掉，但 title 可以救回来
    fm_title = None
    if raw.startswith("---\n"):
        end = raw.find("\n---", 4)
        if end > 0:
            head = raw[4:end]
            m = re.search(r"^title:\s*(.+)$", head, re.M)
            if m:
                fm_title = m.group(1).strip().strip("\"'")
            raw = raw[end + 4:]

    lines = raw.split("\n")
    title, blocks, buf = None, [], []
    dropped, table_buf, code_buf = [], [], []
    in_code = False

    def flush():
        if not buf:
            return
        text = " ".join(buf).strip()
        buf.clear()
        if text:
            blocks.append({"kind": "p", "text": text})

    def flush_table():
        """连续表格行合成一块收走。表格念出来是灾难，但不能凭空消失。"""
        if table_buf:
            drop("table", "\n".join(table_buf))
            table_buf.clear()

    def drop(kind, text):
        flush()
        dropped.append({"kind": kind, "text": text, "after_block": len(blocks)})

    for line in lines:
        s = line.rstrip()
        st = s.strip()

        if st.startswith("```"):
            if in_code:
                code_buf.append(s)
                drop("code", "\n".join(code_buf))
                code_buf = []
                in_code = False
            else:
                flush_table()
                flush()
                code_buf = [s]
                in_code = True
            continue
        if in_code:
            code_buf.append(s)
            continue

        if not st:
            flush_table()
            flush()
            continue
        if re.match(r"^([-*_])\1{2,}$", st):        # 分隔线
            flush_table()
            flush()
            continue
        if st.startswith("|") and st.endswith("|"):  # 表格：读出来是灾难，但别丢
            flush()
            table_buf.append(st)
            continue
        flush_table()
        if re.match(r"^\[\^[^\]]+\]:", st):          # 脚注定义整行丢掉
            flush()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", st)
        if m:
            flush()
            body = clean_inline(m.group(2))
            if not body:
                continue
            if len(m.group(1)) == 1 and title is None:
                title = body
            else:
                blocks.append({"kind": "h", "text": body})
            continue
        # 列表项各自成段。不这么处理的话，连续的 `- xxx` 会被并成一个
        # 几百字的长段，一口气念下来没有停顿，纪要体听着像绕口令。
        if re.match(r"^(?:[-*+]|\d+[.)])\s+", st):
            flush()
            body = clean_inline(st)
            if body:
                blocks.append({"kind": "p", "text": body})
            continue
        body = clean_inline(st)
        if body:
            buf.append(body)
    flush()

    if title is None:
        title = fm_title or "未命名"
    flush_table()
    # 开头的 H1 已经被抽成 title 了，别让它再当一次章节
    return {"title": title, "blocks": blocks, "dropped": dropped}


def split_sentences(text, max_cue=MAX_CUE):
    """一句一条字幕。先按句末标点切，过长再按逗号切，过短并回上一条。"""
    parts = re.split(r"(?<=[%s])" % re.escape(_SENT_END), text)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if visual_len(p) <= max_cue:
            out.append(p)
            continue
        for q in re.split(r"(?<=[%s])" % re.escape(_SOFT_BREAK), p):
            q = q.strip()
            if not q:
                continue
            while visual_len(q) > max_cue:      # 没有标点的长串，硬切
                cut, acc = 0, 0.0
                for i, ch in enumerate(q):
                    acc += 1.0 if visual_len(ch) >= 1 else 0.5
                    if acc > max_cue:
                        break
                    cut = i + 1
                out.append(q[:cut].strip())
                q = q[cut:].strip()
            if q:
                out.append(q)

    merged = []
    for s in out:
        if (merged and visual_len(s) < 6
                and visual_len(merged[-1]) + visual_len(s) <= max_cue):
            merged[-1] += s
        else:
            merged.append(s)
    return merged


def wrap_lines(s, per_line=LINE_CHARS):
    """按显示宽度断成多行。mov_text 不自动折行，所以必须自己切。"""
    lines, cur, acc = [], [], 0.0
    for ch in s:
        w = 1.0 if visual_len(ch) >= 1 else 0.5
        if acc + w > per_line and cur:
            lines.append("".join(cur))
            cur, acc = [], 0.0
        cur.append(ch)
        acc += w
    if cur:
        lines.append("".join(cur))
    return "\n".join(lines)


def allocate(sentences, start, dur):
    """一段内按字数比例分配时间。返回 [(start, end, text)]，单位秒。

    字数比例插值不是「精确」而是「看着不出戏」：误差集中在段末，一般 ±0.3 秒。
    所以段别切太长——这是 cast.py 里 MAX_SEG 存在的唯一理由。
    """
    if not sentences:
        return []
    weights = [max(visual_len(s), 1.0) for s in sentences]
    total = sum(weights)
    cues, t = [], start
    for s, w in zip(sentences, weights):
        d = dur * w / total
        cues.append([t, t + d, s])
        t += d
    return cues


def fix_cues(cues, gap=0.05, min_dur=0.7, limit=None):
    """收拾重叠和一闪而过的碎片。

    两条字幕时间重叠时，VLC 会闪、mpv 会叠字，所以这里强制
    「这条的结束 ≤ 下一条的开始 − gap」。
    """
    for i in range(len(cues) - 1):
        if cues[i][1] > cues[i + 1][0] - gap:
            cues[i][1] = max(cues[i][0] + 0.1, cues[i + 1][0] - gap)
    for c in cues:
        if c[1] - c[0] < min_dur and c[0] > min_dur:
            c[0] = c[1] - min_dur
    if limit is not None and cues:
        cues[-1][1] = min(cues[-1][1], limit)
    return [(a, b, t) for a, b, t in cues if b > a]
