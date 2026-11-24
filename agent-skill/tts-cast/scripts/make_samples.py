#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读数样音：把 tests/verbalize/文本.tsv 逐条烧成 wav。

只回答一个问题：这家引擎把「80%」「1/10」「-5℃」到底念成什么。
不建目录、不写字幕、不改稿，烧完就停。

    cd /storage/emulated/0/Documents/vault/ASRTTS
    python3 /skills/tts-cast/scripts/make_samples.py --dry-run   # 先看花多少
    python3 /skills/tts-cast/scripts/make_samples.py
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cast  # noqa: E402


def read_tsv(p):
    rows = []
    for line in Path(p).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    return rows


def main():
    ap = argparse.ArgumentParser(description="读数样音：二十条短句，只为听引擎怎么念")
    ap.add_argument("--tsv", default="tests/verbalize/文本.tsv")
    ap.add_argument("--out", default="tests/verbalize/samples")
    ap.add_argument("--root", default=".", help="库根，config.env 在它下面")
    ap.add_argument("--dry-run", action="store_true", help="只报字数和花费")
    ap.add_argument("--only", help="只烧某几条，如 02,06")
    args = ap.parse_args()

    os.chdir(args.root)

    class _A:
        provider = None
        config = None
        voice = None
        speech_rate = None

    cast.ARGS = _A()
    cast.load_cfg(None)
    name, p = cast.provider()
    voice = cast.normalize_voice(None)

    rows = read_tsv(args.tsv)
    if args.only:
        keep = {s.strip() for s in args.only.split(",")}
        rows = [r for r in rows if r[0] in keep]
    if not rows:
        print("没有可烧的条目：%s" % args.tsv)
        return 1

    billed = sum(cast.billed_chars(t, p["cjk"]) for _, _, t in rows)
    est = billed / 10000.0 * p["price"]
    print("条数 %d · 计费字符 %d · 预估 %.2f 元（%s，%s）"
          % (len(rows), billed, est, p["label"], voice))

    if args.dry_run:
        for i, cat, t in rows:
            print("  %s %s %s" % (i, cat, t))
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ok, fail = 0, []
    for i, cat, t in rows:
        dst = out / ("%s.wav" % i)
        if dst.is_file() and dst.stat().st_size > 1000:
            print("  = %s 已有，跳过" % dst.name)
            ok += 1
            continue
        try:
            data = cast.tts_call(t, voice)
            dst.write_bytes(data)
            ok += 1
            print("  ✓ %s %s（%d 字节）" % (i, cat, len(data)))
        except Exception as e:
            fail.append((i, str(e)[:80]))
            print("  ✗ %s %s：%s" % (i, cat, str(e)[:80]))

    print("完成 %d 条，失败 %d 条 → %s" % (ok, len(fail), out))
    if fail:
        print("失败的条目按原样留在 tsv 里，重跑即可，已成功的不重复计费。")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
