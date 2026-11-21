#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""渲染层：同一份带时间的段落表 → srt / script.md / index.md / ffmetadata。

纯函数，不吃 IO。四种产物是四个视图，真实数据只有一份（items）。
改样式只改这个文件，别去动 cast.py 的流程。
"""

from cast_common import fmt, ms, srt_ts
from cast_text import wrap_lines


def render_srt(cues):
    """[(start, end, text)] → .srt 文本。

    手动断行在这里做，因为 mov_text / 有些播放器不自动折行，
    一行 40 个字会横穿整个屏幕。
    """
    out = []
    for i, (a, b, t) in enumerate(cues, 1):
        out.append("%d\n%s --> %s\n%s\n" % (i, srt_ts(a), srt_ts(b), wrap_lines(t)))
    return "\n".join(out)


def render_lrc(cues, title="", artist=""):
    """LRC 歌词。

    为什么需要它：VLC 和不少安卓本地播放器对**纯音频**的旁挂 .srt 视而不见
    （它们只在有视频输出时才建字幕轨），但认 .lrc。mpv 两者都行。

    LRC 的硬约束：一条只能一行。换行会被当成新条目，所以这里不做断行，
    靠调用方把句子切短（见 cast.py 里 lrc 用 max_cue=18 单独切一遍）。
    时间戳是「分:秒.百分秒」，不是 srt 的「时:分:秒,毫秒」。
    """
    out = []
    if title:
        out.append("[ti:%s]" % title)
    if artist:
        out.append("[ar:%s]" % artist)
    out.append("[re:tts-cast]")
    out.append("")
    for a, _b, t in cues:
        mm = int(a // 60)
        sec = a - mm * 60
        out.append("[%02d:%05.2f]%s" % (mm, sec, t.replace("\n", " ")))
    return "\n".join(out) + "\n"


def render_script(title, items):
    """口播稿：段前带 [mm:ss]，小标题单独一行。

    这份是**源**，不是展示品：改稿、重烧、查错都以它为准，
    Obsidian 里也只索引 .md 不索引 .srt。
    """
    # 说明用 HTML 注释而不是引用块：引用块会被 parse_article 当成一段正文，
    # 把 script.md 喂回去时就会多出一段、整篇错位。
    out = ["# " + title, "",
           "<!-- 带时间戳的口播稿：可直接改，改完重跑，只有改过的段会重烧。 -->", ""]
    for it in items:
        if it.get("skipped"):
            continue
        ts = "[%s]" % fmt(it["start"])
        if it["kind"] == "h":
            out += ["", "## %s %s" % (ts, it["text"]), ""]
        else:
            out += ["%s %s" % (ts, it["text"]), ""]
    return "\n".join(out).rstrip() + "\n"


def _ffesc(v):
    """ffmetadata 的值要转义这些字符，否则 ffmpeg 会当成新字段。"""
    return (str(v).replace("\\", "\\\\").replace("=", "\\=")
            .replace(";", "\\;").replace("#", "\\#").replace("\n", " "))


def render_ffmeta(chapters, total):
    """章节 → ffmetadata。ffmpeg 的 mp4 封装器会把它写成 chpl atom。

    比 mp4chaps / AtomicParsley 好的地方：不用装任何额外工具。
    """
    if not chapters:
        chapters = [(0.0, "全文")]
    out = [";FFMETADATA1"]
    for i, (start, name) in enumerate(chapters):
        end = chapters[i + 1][0] if i + 1 < len(chapters) else total
        if end <= start:
            continue
        out += ["[CHAPTER]", "TIMEBASE=1/1000",
                "START=%d" % ms(start), "END=%d" % ms(end), "title=" + _ffesc(name)]
    return "\n".join(out) + "\n"


def render_index(meta):
    """元数据卡片。front matter 的键跟 asr-notes 的 out/ 保持一致，方便合库检索。"""
    fm = [
        "---",
        "type: cast",
        "date: %s" % meta["date"],
        "title: \"%s\"" % meta["title"].replace('"', "'"),
        "source: \"%s\"" % meta.get("source", "").replace('"', "'"),
        "voice: %s" % meta.get("voice", ""),
        "model: %s" % meta.get("model", ""),
        "chars: %d" % meta.get("chars", 0),
        "billed_chars: %d" % meta.get("billed", 0),
        "duration: \"%s\"" % fmt(meta.get("duration", 0)),
        "cost_yuan: %.2f" % meta.get("cost", 0.0),
        "cost_total_yuan: %.2f" % meta.get("cost_total", meta.get("cost", 0.0)),
        "---",
        "",
    ]
    body = [
        "# " + meta["title"],
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 字数 | %d |" % meta.get("chars", 0),
        "| 计费字符 | %d |" % meta.get("billed", 0),
        "| 时长 | %s |" % fmt(meta.get("duration", 0)),
        "| 花费 | 本次 %.2f 元 / 累计 %.2f 元 |"
        % (meta.get("cost", 0.0), meta.get("cost_total", meta.get("cost", 0.0))),
        "| 音色 | %s |" % meta.get("voice", ""),
        "",
        "## 文件",
        "",
        "- `%s.m4a` 音频（内嵌章节）" % meta["name"],
        "- `%s.srt` 字幕，mpv 等；grep 定位：`bash cast-find.sh 关键词`" % meta["name"],
        "- `%s.lrc` 歌词，VLC / 安卓本地播放器认这个" % meta["name"],
        "- `script.md` 带时间戳的口播稿",
        "",
        "## 章节",
        "",
    ]
    chapters = meta.get("chapters") or [(0.0, "全文")]
    for start, name in chapters:
        body.append("- `%s` %s" % (fmt(start), name))
    if meta.get("skipped"):
        body += ["", "## 跳过的段", ""]
        for s in meta["skipped"]:
            body.append("- %s" % s)
    body.append("")
    return "\n".join(fm + body)
