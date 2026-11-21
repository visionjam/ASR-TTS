#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕文件（.srt / .vtt / .ass / .ssa）→ 分段文稿

不是"字幕格式转换器"，输出是给人读的段落，不是给播放器的 cue：
cue 是两秒一屏的碎片，读起来像电报；这里把碎片拼成句子，再按停顿分段。

    python3 subtitle.py a.srt                     # 打印分段正文
    python3 subtitle.py a.srt -o a.md --ts        # 写文件，段落前带 [mm:ss]
    python3 subtitle.py a.vtt --gap 1.2           # 停顿阈值（秒），默认 1.8

也能当模块用（asr.py --srt 就是这么调的）：

    import subtitle
    cues = subtitle.parse(path)          # [(start, end, text)]，已去滚动重复
    chunks = subtitle.to_chunks(cues)    # [(start, dur, text)]，可直接进管线

来源说明：抓取的逻辑在 workspace 的 tools/subs.py（yt-dlp），这里只管
手上已经有的字幕文件。两者分工：抓取归 subs.py，文稿归 asr.py --srt。
"""
import argparse
import html
import re
import sys
from pathlib import Path

# 同目录的 asr_common：单独运行时 sys.path[0] 是本目录，被 asr.py import 时也在，
# 但显式加一层，免得从别的 cwd 调起来找不到
sys.path.insert(0, str(Path(__file__).resolve().parent))
from asr_common import fmt  # noqa: E402

# 时间轴：00:00:01,000 --> 00:00:03,500 / 00:01.000 --> 00:03.000（vtt 可省小时）
TS = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*"
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
)
TAG = re.compile(r"<[^>]+>|\{[^}]{0,40}\}")      # html 标签 / ass 内联样式（{\i1} 和 {/i1}）
SOUND = re.compile(r"^(?:\([^)]{2,30}\)|\[[^\]]{2,30}\])\s*")  # (Laughter) [音乐]
END = re.compile(r"[.!?。！？…][\"'”’」』）)]*$")
CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


def _sec(h, m, s, ms):
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _clean(text):
    text = TAG.sub("", text)
    text = html.unescape(text)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"[ \t\u00a0]+", " ", text).strip()
    # 字幕换行常把中文断成两行，中间的半角空格要吃掉，不然每句都带个缝
    text = re.sub(r"(?<=%s) (?=%s)" % (CJK.pattern, CJK.pattern), "", text)
    return text


def _parse_blocks(raw):
    """.srt / .vtt：按空行切块，找 -->"行，后面的都是正文"""
    cues = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [l for l in block.splitlines()]
        i = next((k for k, l in enumerate(lines) if "-->" in l), None)
        if i is None:                      # WEBVTT 头、NOTE、STYLE 块
            continue
        m = TS.search(lines[i])
        if not m:
            continue
        g = m.groups()
        text = _clean(" ".join(lines[i + 1:]))
        if text:
            cues.append((_sec(*g[:4]), _sec(*g[4:]), text))
    return cues


def _parse_ass(raw):
    """[Events] 段里的 Dialogue 行；Format 行决定字段顺序，正文里可以有逗号"""
    fields, cues = None, []
    for line in raw.splitlines():
        s = line.strip()
        if s.lower().startswith("format:") and fields is None:
            fields = [f.strip().lower() for f in s.split(":", 1)[1].split(",")]
        elif s.lower().startswith("dialogue:") and fields:
            parts = s.split(":", 1)[1].split(",", len(fields) - 1)
            if len(parts) != len(fields):
                continue
            row = dict(zip(fields, parts))
            text = _clean(row.get("text", "").replace("\\N", " ").replace("\\n", " "))
            if not text:
                continue
            try:
                sh, sm, ss = _ass_ts(row.get("start", ""))
                eh, em, es = _ass_ts(row.get("end", ""))
            except ValueError:
                continue
            cues.append((sh * 3600 + sm * 60 + ss, eh * 3600 + em * 60 + es, text))
    return cues


def _ass_ts(s):
    h, m, sec = s.strip().split(":")
    return int(h), int(m), float(sec)


def dedupe(cues):
    """YouTube 自动字幕是滚动式的：每屏 = 上一屏的尾巴 + 新内容

    若「上一条的结尾」==「这一条的开头」，砍掉重叠部分只留增量。
    不去重的话，自动字幕转出来每句会重复两三遍。
    """
    out, prev = [], ""
    for start, end, text in cues:
        t = text
        if prev:
            for cut in range(min(len(prev), len(t)), 1, -1):
                if prev.endswith(t[:cut]):
                    t = t[cut:].strip()
                    break
        prev = text
        if t:
            out.append((start, end, t))
    return out


def parse(path):
    """读字幕文件 → [(start, end, text)]，已去滚动重复"""
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    cooked = raw.replace("\r\n", "\n").replace("\r", "\n")
    ext = Path(path).suffix.lower()
    cues = _parse_ass(cooked) if ext in (".ass", ".ssa") else _parse_blocks(cooked)
    return dedupe(cues)


def _join(buf, text):
    """中文直接接，英文之间补空格；中英边界不补（留给排版那一步）"""
    if not buf:
        return text
    if buf[-1].isascii() and buf[-1].isalnum() and text[:1].isascii() and text[:1].isalnum():
        return buf + " " + text
    return buf + text


def sentences(cues, soft=200):
    """cue → 句子：遇到句末标点收一句；没有标点时攒到 200 字也切开

    自动字幕整段没有标点，只按标点切会攒出几千字一块。
    """
    out, buf, start, end = [], "", None, None
    for t0, t1, text in cues:
        if start is None:
            start = t0
        buf = _join(buf, text)
        end = t1
        if END.search(text) or len(buf) >= soft:
            out.append((start, end, buf))
            buf, start = "", None
    if buf:
        out.append((start, end, buf))
    return out


def to_chunks(cues, gap=1.8, target=520, min_para=30):
    """句子 → 分片：停顿（默认 ≥1.8 秒）或字数到顶就切一块

    产出 (start, dur, text)，和 ASR 管线的 chunks 同构，可以直接写 transcript.md。
    分片大小跟 ASR 的 110 秒不同——这里没有接口限制，只是别让一块长到读不动。
    min_para：太短的块不单独成段（默认 30 字）；设太大反而会把停顿吃掉，
    因为一句话 40 字在口播里已经是一个完整的段落了。
    """
    sents = sentences(cues)
    if not sents:
        return []
    chunks, buf, start, prev_end = [], [], None, None
    for i, (t0, t1, text) in enumerate(sents):
        if start is None:
            start = t0
        n = sum(len(x) for x in buf)
        pause = prev_end is not None and (t0 - prev_end) >= gap
        if buf and ((pause and n >= min_para) or n >= target):
            chunks.append((start, max(1, int(prev_end - start + 0.5)), "".join(buf)))
            buf, start = [], None
            if start is None:
                start = t0
        buf.append(text)
        prev_end = t1
    if buf:
        chunks.append((start, max(1, int(prev_end - start + 0.5)), "".join(buf)))
    return chunks


def strip_sounds(text):
    """段落开头的 (Laughter) / [音乐] 挪掉：每段都以笑声开头，读起来很怪"""
    text = re.sub(r"(?m)^(?:(?:\([^)]{2,30}\)|\[[^\]]{2,30}\])\s*)+", "", text)
    return re.sub(r"(?:\(Laughter\)\s*){2,}", "(Laughter) ", text)


def to_text(cues, gap=1.8, target=520, with_ts=False):
    paras = []
    for start, _dur, text in to_chunks(cues, gap, target):
        paras.append(("[%s] %s" % (fmt(start), text)) if with_ts else text)
    return strip_sounds("\n\n".join(paras))


def main():
    ap = argparse.ArgumentParser(description="字幕文件 → 分段文稿")
    ap.add_argument("file")
    ap.add_argument("-o", "--output")
    ap.add_argument("--ts", action="store_true", help="每段前加 [mm:ss]")
    ap.add_argument("--gap", type=float, default=1.8, help="停顿阈值，秒")
    ap.add_argument("--target", type=int, default=520, help="单段目标字数")
    a = ap.parse_args()

    cues = parse(a.file)
    if not cues:
        sys.exit("没解析出字幕：%s（.srt/.vtt/.ass 都试过了）" % a.file)
    text = to_text(cues, a.gap, a.target, a.ts)
    if a.output:
        Path(a.output).write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    print("cues=%d chunks=%d chars=%d dur=%s"
          % (len(cues), len(to_chunks(cues, a.gap, a.target)), len(text),
             fmt(cues[-1][1])), file=sys.stderr)


if __name__ == "__main__":
    main()
