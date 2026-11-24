#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_draft.py 的回归：不联网、不花钱。

它是「烧音频之前那道尺子」，尺子本身偏了，就会放一集超时的稿子进去烧钱，
或者把好好的稿子拦下来。所以单独测一遍：
  1. 干净短稿放行（退出码 0）
  2. 超三十分钟拦下（退出码 1，并说清多出多少字）
  3. 章节对不上拦下
  4. 同一件事讲两遍、超长句拦下
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_VAULT = _HERE.parent
SKILL = Path(os.environ.get("CAST_SKILL") or next(
    (p for p in (Path("/skills/tts-cast"), _VAULT / "agent-skill/tts-cast")
     if (p / "scripts/check_draft.py").is_file()), "/skills/tts-cast"))

sys.path.insert(0, str(SKILL / "scripts"))
try:
    import check_draft as D
except (ImportError, SystemExit) as e:
    print("✗ 导入失败：%s（pip install requests）" % e)
    sys.exit(1)

FAIL = []


def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        FAIL.append(name)


def run(*argv):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = D.main(list(argv))
    return rc, buf.getvalue()


def main():
    root = Path(tempfile.mkdtemp())
    try:
        clean = root / "clean.md"
        clean.write_text("# 标题\n\n## 一节\n\n短句。又一句。\n\n## 两节\n\n再一句。\n",
                         encoding="utf-8")
        rc, out = run(str(clean))
        check("干净短稿放行", rc == 0, out)
        check("报出预估时长", "预估时长" in out, out)

        big = root / "big.md"
        big.write_text("# 长稿\n\n## 一节\n\n" + "这是一句凑字数的话。" * 900, encoding="utf-8")
        rc, out = run(str(big))
        check("超三十分钟拦下", rc == 1, out)
        check("说清多出多少字", "多出约" in out, out)

        src = root / "src.md"
        src.write_text("# 原文\n\n## 一\n\na\n\n## 二\n\nb\n\n## 三\n\nc\n", encoding="utf-8")
        rc, out = run(str(clean), str(src))
        check("章节对不上拦下", rc == 1 and "章节" in out, out)

        dup = root / "dup.md"
        dup.write_text("# 稿\n\n## 一节\n\n这件事他后来又原原本本讲了一遍给大家听。\n\n"
                       "## 两节\n\n中间隔一段别的。\n\n"
                       "这件事他后来又原原本本讲了一遍给大家听。\n", encoding="utf-8")
        rc, out = run(str(dup))
        check("重复拦下", rc == 1 and "重复" in out, out)

        long1 = root / "long.md"
        long1.write_text("# 稿\n\n## 一节\n\n" + "这一句被故意写得很长，" * 6 + "一直不停顿。\n",
                         encoding="utf-8")
        rc, out = run(str(long1))
        check("长句拦下", rc == 1 and "长句" in out, out)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if FAIL:
        print("\n✗ 失败 %d 项：%s" % (len(FAIL), "、".join(FAIL)))
        return 1
    print("\n✓ check_draft 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
