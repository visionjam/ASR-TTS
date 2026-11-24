#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给「人写的口述稿」做体检：不联网、不花钱，烧音频之前先验一遍。

脚本只管把成稿变成声音；多篇怎么合、删什么、控多少字，是写稿那一步的事。
这个检查器就是那一步的尺子——写完先量，再决定要不要送进 TTS。

    python3 check_draft.py 稿子.md                     # 只验稿子本身
    python3 check_draft.py 稿子.md 原文1.md 原文2.md   # 顺便算压缩比、对章节数
    python3 check_draft.py 稿子.md --cpm 265           # 换语速后改每分钟字数

查四样（都是真踩过的）：
  1. 时长：按每分钟字数估算，超 30 分钟就直接说，别等烧完才知道
  2. 章节：原文几节、稿子几节，少了通勤时没法跳段
  3. 重复：连续十五字重复，同一件事讲了两遍
  4. 长句：超过六十字，一口气念不完

退出码：0 没问题 / 1 有该改的问题（时长超、章节少、重复、长句）。
压缩比只报不改（它取决于你打算删多狠）。
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    import cast as C
    import cast_text as T
except (ImportError, SystemExit) as e:          # requests 没装
    sys.exit("✗ 导入失败：%s（pip install requests）" % e)

CPM = 265          # 豆包当前语速（-20）实测：260–275 字/分钟，取下限
LIMIT_MIN = 30     # 一集的时长上限


def main(argv=None):
    p = argparse.ArgumentParser(description="口述稿体检（不联网、不花钱）")
    p.add_argument("draft", help="稿子 .md")
    p.add_argument("sources", nargs="*", help="原文，用来算压缩比和对章节数")
    p.add_argument("--cpm", type=int, default=CPM, help="每分钟念多少字，默认 %d" % CPM)
    p.add_argument("--limit", type=int, default=LIMIT_MIN, help="时长上限（分钟）")
    a = p.parse_args(argv)

    raw = Path(a.draft).read_text(encoding="utf-8")
    art = T.parse_article(raw)
    chars = sum(len(b["text"]) for b in art["blocks"])
    mins = chars / float(a.cpm)
    heads = [b["text"] for b in art["blocks"] if b["kind"] == "h"]

    print("稿子：%s" % a.draft)
    print("字数：%d　预估时长：%d 分 %02d 秒（按 %d 字/分钟）"
          % (chars, int(mins), int((mins % 1) * 60), a.cpm))
    print("章节：%d 节%s" % (len(heads), ("　" + "／".join(heads)) if heads else "（一个都没有）"))

    bad = []
    if mins > a.limit:
        over = chars - int(a.limit * a.cpm)
        bad.append("时长")
        print("⚠ 超过 %d 分钟：多出约 %d 字，砍到 %d 字以内"
              % (a.limit, over, int(a.limit * a.cpm)))

    src_chars, src_heads = 0, []
    if a.sources:
        for s in a.sources:
            sa = T.parse_article(Path(s).read_text(encoding="utf-8"))
            c = sum(len(b["text"]) for b in sa["blocks"])
            h = [b["text"] for b in sa["blocks"] if b["kind"] == "h"]
            src_chars += c
            src_heads += h
            print("  原文 %-40s %6d 字　%d 节" % (Path(s).name, c, len(h)))
        if src_chars:
            print("压缩比：%d / %d 字 = %.0f%%（多篇合并时这个数会自然偏低，别拿它当指标）"
                  % (chars, src_chars, chars * 100.0 / src_chars))
        if len(heads) < min(len(src_heads), 3) and len(src_heads) >= 3:
            bad.append("章节")
            print("⚠ 原文合计 %d 节，稿子只有 %d 节：通勤时没法跳段" % (len(src_heads), len(heads)))

    items = C.spoken_qc({"title": "", "blocks": []}, art)
    for k, v in items:
        if k == "章节":
            continue                     # 上面按原文对过了，这里没有原文，比不了
        bad.append(k)
        print("⚠ %s：%s" % (k, v))
    if not items and not bad:
        print("✓ 没发现问题")

    print()
    if bad:
        print("✗ 有 %d 项要改：%s" % (len(set(bad)), "、".join(sorted(set(bad)))))
        print("  改完再跑一遍这个检查，通过了再烧音频。")
        return 1
    print("✓ 可以进 TTS：python3 cast.py %s" % a.draft)
    return 0


if __name__ == "__main__":
    sys.exit(main())
