#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线自检：不联网、不花钱。纯函数那层坏了，这里先炸。

    python3 scripts/selftest.py

查四件事：
  1. 解析：标题、小标题、表格和脚注有没有被丢掉
  2. 时间轴：字幕不重叠、不穿越、单调递增
  3. 渲染：srt 序号连续、ffmetadata 的 START/END 合法
  4. 往返：script.md 喂回去能还原成同一批段（改稿重跑靠这条）
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cast_render as R          # noqa: E402
import cast_text as T            # noqa: E402
from cast_common import billed_chars, digest, fmt, visual_len  # noqa: E402

SAMPLE = HERE.parent / "references" / "sample.md"
FAIL = []


def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        FAIL.append(name)


def fake_plan(art):
    """不调 API，按字数估时长，专门用来验时间轴和渲染。"""
    segs = []
    for b in art["blocks"]:
        segs.append({"kind": b["kind"], "text": b["text"],
                     "chars": len(b["text"]), "billed": billed_chars(b["text"])})
    for i, s in enumerate(segs, 1):
        s["i"] = i
        s["dur"] = max(0.6, visual_len(s["text"]) * 60.0 / 250.0)
    return segs


def build_timeline(segs):
    """和 cast.py 里的那份保持一致：小标题前后留白，段间留白。"""
    sil = {"h": (1.2, 1.2), "p": (0.0, 0.7)}
    t, chapters = 0.0, []
    for s in segs:
        lead, tail = sil[s["kind"]]
        t += lead
        s["start"] = t
        t += s["dur"]
        s["end"] = t
        t += tail
        if s["kind"] == "h":
            chapters.append((s["start"] - lead, s["text"]))
    if not chapters:
        chapters = [(0.0, "全文")]
    elif chapters[0][0] > 0.5:
        chapters.insert(0, (0.0, "开篇"))
    return t, chapters


def build_cues(segs, max_cue=T.MAX_CUE):
    cues = []
    for s in segs:
        cues += T.allocate(T.split_sentences(s["text"], max_cue=max_cue),
                           s["start"], s["dur"])
    return T.fix_cues(cues)


def main():
    raw = SAMPLE.read_text(encoding="utf-8")
    art = T.parse_article(raw)

    print("\n[1] 解析")
    check("标题取到 H1", art["title"] == "一九九八年的一个下午", art["title"])
    heads = [b["text"] for b in art["blocks"] if b["kind"] == "h"]
    check("两个小标题都在", heads == ["那时候的版面", "一件小事"], heads)
    flat = " ".join(b["text"] for b in art["blocks"])
    check("表格没有混进来", "采访时长" not in flat)
    check("脚注定义没有混进来", "二〇〇五年以后" not in flat)
    check("脚注引用没有留下空括号", "[^1]" not in flat)
    check("加粗标记已去掉", "**" not in flat)

    print("\n[2] 时间轴")
    segs = fake_plan(art)
    total, chapters = build_timeline(segs)
    cues = build_cues(segs)
    check("cue 数量 > 段数（按句切开了）", len(cues) > len(segs), (len(cues), len(segs)))
    mono = all(cues[i][1] <= cues[i + 1][0] + 1e-6 for i in range(len(cues) - 1))
    check("相邻字幕不重叠", mono)
    inside = all(0 <= a < b <= total + 1e-6 for a, b, _ in cues)
    check("都在总时长内", inside, total)
    check("总时长 > 0", total > 0)
    check("章节 ≥ 1 且从 0 开始", chapters[0][0] == 0.0, chapters)

    print("\n[3] 渲染")
    srt = R.render_srt(cues)
    blocks = [b for b in srt.split("\n\n") if b.strip()]
    check("srt 段数 == cue 数", len(blocks) == len(cues), (len(blocks), len(cues)))
    check("srt 序号从 1 连续", blocks[0].startswith("1\n"), blocks[0][:8])
    check("srt 有箭头", "-->" in blocks[0])
    check("每条最多 3 行正文", all(len(b.split("\n")) <= 5 for b in blocks))
    check("手动断行生效（最长一行 ≤ 20 宽度）",
          all(visual_len(ln) <= 20 for b in blocks for ln in b.split("\n")[2:]
              if ln.strip()))

    lrc = R.render_lrc(build_cues(segs, 18.0), art["title"], "tts-cast")
    body = [ln for ln in lrc.split("\n") if ln and not ln.startswith("[ti:")
            and not ln.startswith("[ar:") and not ln.startswith("[re:")]
    import re as _re
    check("lrc 每行都是 [mm:ss.xx] 开头",
          all(_re.match(r"^\[\d\d:\d\d\.\d\d\]", ln) for ln in body),
          body[:2])
    check("lrc 一条只有一行（无内嵌换行）", "\n" not in "".join(body))
    ts = [int(ln[1:3]) * 60 + float(ln[4:9]) for ln in body]
    check("lrc 时间戳单调", all(ts[i] <= ts[i + 1] for i in range(len(ts) - 1)))

    meta = R.render_ffmeta(chapters, total)
    check("ffmetadata 头正确", meta.startswith(";FFMETADATA1"))
    pairs = [ln for ln in meta.split("\n") if ln.startswith(("START=", "END="))]
    check("章节成对", len(pairs) == 2 * len(chapters), (len(pairs), len(chapters)))
    nums = [int(x.split("=")[1]) for x in pairs]
    starts_ok = all(nums[i] < nums[i + 1] for i in range(0, len(nums) - 1, 2))
    joins_ok = all(nums[i] <= nums[i + 1] for i in range(1, len(nums) - 1, 2))
    check("每章 START < END", starts_ok, nums)
    check("相邻章节首尾相接", joins_ok, nums)

    print("\n[4] 往返（改稿后重跑）")
    script = R.render_script(art["title"], segs)
    art2 = T.parse_article(script)
    a = [b["text"] for b in art["blocks"]]
    b = [b["text"] for b in art2["blocks"]]
    check("段数与内容一致", a == b, [x for x, y in zip(a, b) if x != y][:2])
    check("时间戳没有被念进正文", "[00:" not in " ".join(b))

    print("\n[5] 缓存复用（改一段只重烧一段）")
    # 这一节专门防「--force 全量重烧」和「按段号命名导致串音」这两个坑。
    # 不联网：tts_call 和 probe_duration 都被替身接管。
    import tempfile
    import cast as C
    calls = []

    def fake_tts(text, voice, ff=None):
        calls.append(text)
        return b"RIFF" + b"\0" * 64

    real_tts, real_probe = C.tts_call, C.probe_duration
    C.tts_call, C.probe_duration = fake_tts, lambda ff, p: 1.0
    try:
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            art3 = T.parse_article("# 三句\n\n第一段。\n\n第二段。\n\n第三段。\n")
            segs3 = fake_plan(art3)
            for s in segs3:
                s["hash"] = digest(s["text"])
            C.synth_all(segs3, wd, "tide")
            check("首次全部合成", len(calls) == 3, len(calls))

            calls.clear()
            for s in segs3:
                s.pop("wav", None)
                s.pop("dur", None)
            C.synth_all(segs3, wd, "tide")
            check("原样重跑不再花钱", len(calls) == 0, len(calls))

            calls.clear()
            segs3[1]["text"] = "第二段改了。"
            segs3[1]["hash"] = digest(segs3[1]["text"])
            for s in segs3[1:]:
                s.pop("wav", None)
                s.pop("dur", None)
            C.synth_all(segs3, wd, "tide")
            check("改一段只重烧一段", len(calls) == 1, calls)

            names = sorted(p.name for p in wd.glob("s_*.wav"))
            check("缓存文件按 hash 命名（4 个 hash 各一份）", len(names) == 4, names)
    finally:
        C.tts_call, C.probe_duration = real_tts, real_probe

    print("\n[6] 计费估算")
    check("汉字算 2", billed_chars("汉字") == 4, billed_chars("汉字"))
    check("半角算 1", billed_chars("abc") == 3, billed_chars("abc"))

    print()
    if FAIL:
        print("✗ 失败 %d 项：%s" % (len(FAIL), "、".join(FAIL)))
        return 1
    print("✅ 全过（%d 段 / %d 条字幕 / %s）"
          % (len(segs), len(cues), fmt(total)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
