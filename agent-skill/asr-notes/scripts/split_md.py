#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把旧版单文件输出（AI 正文 + <details> 逐字稿）拆成 index / transcript / notes：

    out/<文件夹>/
    ├─ index.md
    ├─ transcript.md
    └─ notes.md

cleaned.md 需要之后跑一次 --ai-only 才会生成（它要调模型清洗）。

    python3 split_md.py "out/2026-11-18-xxx.md"
    python3 split_md.py "out/2026-11-18-xxx.md" --keep

不调用任何 API。--keep 保留原 md，默认删除。
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asr_common import fmt, slugify  # noqa: E402


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


def parse(raw):
    fm = {}
    m = re.match(r"^---\n(.*?)\n---\n?", raw, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"').strip("'")
        raw = raw[m.end():]

    cut = raw.find("<details>")
    body = (raw[:cut] if cut >= 0 else raw).strip()
    block = raw[cut:] if cut >= 0 else ""

    chunks = []
    for line in block.splitlines():
        s = line.strip()
        mm = re.match(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)$", s)
        if mm:
            chunks.append((mm.group(1), mm.group(2).strip()))
        elif chunks and s and not s.startswith(("<", "#")):
            chunks[-1] = (chunks[-1][0], (chunks[-1][1] + s).strip())
    return fm, body, chunks


def secs(ts):
    p = [int(x) for x in ts.split(":")]
    return (p[0] * 3600 + p[1] * 60 + p[2]) if len(p) == 3 else (p[0] * 60 + p[1])


def summary_of(body):
    m = re.search(r"^##\s*一句话.*?\n+(.+?)\n", body, re.S | re.M)
    return m.group(1).strip().strip("（）()")[:120] if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("--keep", action="store_true", help="保留原单文件")
    a = ap.parse_args()

    src = Path(a.md).expanduser()
    if not src.is_file():
        sys.exit("找不到 " + str(src))

    fm, body, chunks = parse(src.read_text(encoding="utf-8"))
    if not chunks:
        sys.exit("没解析出逐字稿（找不到 [mm:ss] 行）")

    stem = src.stem
    m = re.match(r"^(\d{4}-\d{2}-\d{2})-(.*)$", stem)
    date, title = (m.group(1), m.group(2)) if m else (time.strftime("%Y-%m-%d"), stem)

    folder = src.parent / ("%s-%s" % (date, slugify(title)))
    folder.mkdir(parents=True, exist_ok=True)

    duration = fm.get("duration") or fmt(secs(chunks[-1][0]))
    dur_sec = secs(duration) if ":" in str(duration) else 0

    cost = 0.0
    state = src.parent.parent / ".asr_state.json"
    if state.is_file():
        for v in json.loads(state.read_text(encoding="utf-8")).values():
            # 旧版 state 记 out（扁平 md 文件名），新版记 folder（文件夹名）
            if v.get("out") == src.name or v.get("folder") == folder.name:
                cost = v.get("cost", 0)

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    meta = {
        "id": folder.name,
        "title": title,
        "type": "voice-index",
        "date": date,
        "captured_at": fm.get("date", date),
        "duration": duration,
        "duration_sec": dur_sec,
        "source": fm.get("audio", "").split("/")[-1],
        "audio": fm.get("audio", ""),
        "transcript": "[[transcript]]",
        "notes": "[[notes]]",
        "asr_model": fm.get("asr", "deepseek-asr"),
        "asr_chunks": len(chunks),
        "asr_cost": cost,
        "llm_model": fm.get("llm", ""),
        "status": fm.get("status", "draft"),
        "generated_at": now,
        "tags": ["语音"],
    }

    (folder / "transcript.md").write_text(
        front_matter({"id": meta["id"], "type": "transcript", "status": "final",
                      "source": meta["source"], "duration": duration,
                      "duration_sec": dur_sec, "asr": meta["asr_model"],
                      "asr_chunks": len(chunks), "asr_cost_cny": cost,
                      "generated_at": now, "split_from": src.name})
        + "\n\n# 逐字稿\n\n"
        + "\n".join("### %s\n\n%s\n" % (t, x) for t, x in chunks),
        encoding="utf-8")

    (folder / "notes.md").write_text(
        front_matter({"id": meta["id"], "type": "notes", "status": meta["status"],
                      "llm": meta["llm_model"], "based_on": "[[transcript]]",
                      "generated_at": now, "tags": ["语音"]})
        + "\n\n" + body.strip() + "\n",
        encoding="utf-8")

    idx = ["# %s" % title, "", "| 项 | 值 |", "|---|---|",
           "| 时长 | %s |" % duration,
           "| 录音 | %s |" % meta["captured_at"],
           "| 转写 | %s · %d 片 · ¥%.3f |" % (meta["asr_model"], len(chunks), cost),
           "| 整理 | %s |" % meta["llm_model"],
           "| 状态 | %s |" % meta["status"],
           ""]
    s = summary_of(body)
    if s:
        idx += ["## 一句话", "", s, ""]
    idx += ["## 文稿", "",
            "- [[transcript|逐字稿]]：ASR 原样输出，带时间戳",
            "- [[notes|整理稿]]：AI 结构化，可继续编辑",
            "",
            "> cleaned.md 还没生成：跑一次 `--ai-only` 会补上。", ""]
    (folder / "index.md").write_text(front_matter(meta) + "\n\n" + "\n".join(idx),
                                     encoding="utf-8")

    if not a.keep:
        src.unlink()
    print("✅ %s（%d 段）" % (folder, len(chunks)))
    for f in sorted(folder.iterdir()):
        print("   %-16s %6d 字节" % (f.name, f.stat().st_size))


if __name__ == "__main__":
    main()
