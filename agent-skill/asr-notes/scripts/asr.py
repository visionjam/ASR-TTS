#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
录音 -> DeepSeek ASR 转写 -> DeepSeek 整理 -> 一条录音一个文件夹

输出结构（逐字稿与 AI 稿解耦，互不覆盖）：

    out/2026-11-18-产品想法/
    ├─ index.md         元数据卡片 + 双链
    ├─ transcript.md    逐字稿，只在转写阶段生成
    └─ notes.md         AI 整理稿，可以随时重生成

    python3 asr.py --check                    只验 Key 和 ffmpeg
    python3 asr.py --dry-run                  看会处理哪些文件
    python3 asr.py                            处理 ./inbox 里所有新音频
    python3 asr.py a.m4a                      处理指定文件
    python3 asr.py --ai-only out/<文件夹>      只重跑 AI，不重转写（省钱）
    python3 asr.py --asr-only                 只转写，不整理
    python3 asr.py --text note.txt            跳过转写，直接整理文本

依赖 ffmpeg。找不到时依次尝试 $FFMPEG_BIN -> PATH -> imageio-ffmpeg。
Key 读取顺序：--config > $ASR_CONFIG > ./config.env > ~/.config/asr-notes/env，
最后用环境变量 DEEPSEEK_API_KEY 覆盖。

代码分四层，改之前先看改的是哪一层：

    asr_common.py   log / fmt / slugify —— 三个模块共用，改一处全生效
    asr_render.py   Markdown 渲染（front matter、四个产出文件、清洗稿重排），纯函数
    asr_quotes.py   清洗稿体检 + 摘引逐字核对（不信模型自评的两道闸）
    asr.py          配置、ffmpeg 切片、ASR / LLM 调用、主流程（本文件）
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("缺少 requests：pip install requests")

HERE = Path(__file__).resolve().parent
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".amr", ".ogg",
             ".flac", ".opus", ".3gp", ".mp4", ".wma", ".webm"}

API = "https://api.deepseek.com/v1/tools/asr"
PRICE_YUAN_PER_HOUR = 0.39          # 限时 5 折价，刊例 0.78

CHUNK_MAX = 110                     # 秒；接口上限 120，留 10 秒余量
CHUNK_MIN = 60
SILENCE_DB = -35
SILENCE_D = 0.6

CFG = {}
ARGS = None


# ------------------------------------------------------------------ 配置

def load_cfg(explicit=None):
    CFG.update({
        "LLM_BASE": "https://api.deepseek.com/v1",
        "LLM_MODEL": "deepseek-chat",
        "EXTRA_DIRS": "",
    })
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("ASR_CONFIG"):
        candidates.append(Path(os.environ["ASR_CONFIG"]))
    candidates.append(Path.cwd() / "config.env")
    candidates.append(Path.home() / ".config" / "asr-notes" / "env")
    for p in candidates:
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                CFG[k.strip()] = v.strip().strip('"').strip("'")
            CFG["_from"] = str(p)
            break
    for k in ("DEEPSEEK_API_KEY", "LLM_BASE", "LLM_MODEL", "EXTRA_DIRS"):
        if os.environ.get(k):
            CFG[k] = os.environ[k]

# 拆出去的三层：小工具 / Markdown 渲染 / 校验。
# HERE 必须先进 sys.path——测试用 spec_from_file_location 加载本模块，
# 那时 sys.path[0] 是测试目录，不手动加就 import 不到同目录的兄弟模块。
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from asr_common import log, fmt, slugify                      # noqa: E402
from asr_render import (                                     # noqa: E402
    yaml_val, front_matter, write_index, write_text_index,
    write_transcript, write_notes, write_cleaned, reflow, cleaned_blocks)
from asr_quotes import (                                     # noqa: E402
    _norm, longest_run, verify_quote, stamp_quotes, check_cleaned, report_cleaned)

# ------------------------------------------------------------------ ffmpeg

def find_ffmpeg():
    cand = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")
    if cand and Path(cand).exists():
        return cand
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def run_ffmpeg(ff, args, timeout=1800):
    return subprocess.run([ff, "-hide_banner"] + args,
                          capture_output=True, text=True, timeout=timeout)


def duration_of(ff, src):
    p = run_ffmpeg(ff, ["-i", str(src)])
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr)
    if not m:
        raise RuntimeError("读不出音频时长，文件可能损坏")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def silences(ff, src):
    p = run_ffmpeg(ff, ["-vn", "-i", str(src), "-af",
                        "silencedetect=noise=%ddB:d=%s" % (SILENCE_DB, SILENCE_D),
                        "-f", "null", "-"])
    out, cur = [], None
    for line in p.stderr.splitlines():
        m = re.search(r"silence_start:\s*([\d.]+)", line)
        if m:
            cur = float(m.group(1))
        m = re.search(r"silence_end:\s*([\d.]+)", line)
        if m and cur is not None:
            out.append((cur, float(m.group(1))))
            cur = None
    return out


def plan(total, sils):
    cuts, pos = [0.0], 0.0
    while total - pos > CHUNK_MAX:
        lo, hi = pos + CHUNK_MIN, pos + CHUNK_MAX
        cands = [(a + b) / 2 for a, b in sils if lo <= a <= hi]
        cut = max(cands) if cands else hi
        cuts.append(cut)
        pos = cut
    cuts.append(total)
    return [(round(cuts[i], 3), round(cuts[i + 1] - cuts[i], 3))
            for i in range(len(cuts) - 1)]


def encode(ff, src, start, dur, dst):
    p = run_ffmpeg(ff, ["-y", "-loglevel", "error", "-ss", str(start), "-t", str(dur),
                        "-i", str(src), "-vn", "-ac", "1", "-ar", "48000",
                        "-c:a", "libopus", "-b:a", "24k", "-f", "ogg", str(dst)])
    if p.returncode != 0 or not Path(dst).exists():
        raise RuntimeError("ffmpeg 切片失败：" + p.stderr[-300:])
    return dst


# ------------------------------------------------------------------ 转写

def asr(path):
    for attempt in range(3):
        with Path(path).open("rb") as f:
            r = requests.post(API,
                              headers={"Authorization": "Bearer " + CFG["DEEPSEEK_API_KEY"]},
                              files={"file": (Path(path).name, f, "audio/ogg")},
                              timeout=600)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 503, 504) and attempt < 2:
            time.sleep(3 * (attempt + 1))
            continue
        raise RuntimeError("ASR HTTP %s: %s" % (r.status_code, r.text[:300]))
    raise RuntimeError("ASR 重试耗尽")


# ------------------------------------------------------------------ 整理

def chat(system, user, max_tokens=6000):
    payload = {"model": CFG["LLM_MODEL"],
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "temperature": 0.3, "max_tokens": max_tokens}
    url = CFG["LLM_BASE"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": "Bearer " + CFG["DEEPSEEK_API_KEY"],
               "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=900)
        except requests.RequestException as e:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError("LLM 请求失败：%s" % e)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(3 * (attempt + 1))
            continue
        raise RuntimeError("LLM HTTP %s: %s" % (r.status_code, r.text[:300]))
    raise RuntimeError("LLM 重试耗尽")


def prompt(name):
    for base in (ARGS.prompts, HERE / "prompts"):
        p = Path(base) / name
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return ""




def first_summary(body):
    """取 notes 开头那句概括；self 模板叫「一句话概括」，external 叫「这期在讲什么」"""
    m = re.search(r"^##\s*(?:一句话|这期在讲什么).*?\n+(.+?)\n", body, re.S | re.M)
    if not m:
        return ""
    line = m.group(1).strip().strip("（）()")
    return line[:120]


def parse_transcript(p):
    """从 transcript.md 读回 (时间戳字符串, 文本) 列表"""
    raw = p.read_text(encoding="utf-8")
    if raw.startswith("---"):
        raw = raw.split("---", 2)[2]
    parts = re.split(r"^###\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*$", raw, flags=re.M)
    out = []
    for i in range(1, len(parts) - 1, 2):
        text = parts[i + 1].strip()
        if text:
            out.append((parts[i], text))
    return out


def read_cleaned(p):
    """读 cleaned.md 的正文，统一还原成 "[mm:ss] 文本" 供后续提示词使用

    新版 cleaned.md 是「### mm:ss 标题 + 分段正文」，旧版是「[[mm:ss] 文本」一行一段，
    两种都要能读——库里的老稿件不会因为改了脚本就读不出来。
    """
    raw = p.read_text(encoding="utf-8")
    if raw.startswith("---"):
        raw = raw.split("---", 2)[-1]
    raw = re.sub(r"^#\s*清洗稿\s*$", "", raw, flags=re.M)
    if re.search(r"^###\s+\d{1,2}:\d{2}", raw, flags=re.M):
        parts = re.split(r"^###\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*$", raw, flags=re.M)
        out = []
        for i in range(1, len(parts) - 1, 2):
            body = re.sub(r"\n{2,}", "\n", parts[i + 1].strip())
            if body:
                out.append("[%s] %s" % (parts[i], body))
        if out:
            return "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", raw.strip())


def ts_to_sec(ts):
    parts = [int(x) for x in ts.split(":")]
    return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)


# ------------------------------------------------------------------ 主流程

def gather(dry=False):
    files = [p for p in ARGS.inbox.glob("*") if p.suffix.lower() in AUDIO_EXT] \
        if ARGS.inbox.is_dir() else []
    for d in [x for x in CFG.get("EXTRA_DIRS", "").split(",") if x.strip()]:
        dp = Path(d.strip())
        if not dp.is_dir():
            continue
        for p in dp.glob("*"):
            if p.suffix.lower() not in AUDIO_EXT:
                continue
            dst = ARGS.inbox / p.name
            if not dst.exists():
                if dry:                      # dry-run 不许有副作用：不往 inbox 里搬
                    files.append(p)
                    continue
                ARGS.inbox.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                files.append(dst)
    return sorted(set(files))


def digest(p):
    return "%s-%s" % (p.name, p.stat().st_size)


def cache_dir(path):
    """切片缓存目录：按「文件名-大小」建，避免不同目录的同名文件互相覆盖。

    旧版按文件名建（work/<文件名>），这里保留兼容：新目录不存在、旧目录却有
    chunks.json 时沿用旧的，手机端已有缓存不用重新转写。
    """
    d = ARGS.work / digest(path)
    legacy = ARGS.work / path.stem
    if not d.exists() and (legacy / "chunks.json").is_file():
        return legacy
    return d


def transcribe(ff, path):
    total = duration_of(ff, path)
    sils = silences(ff, path)
    pieces = plan(total, sils)
    log("  时长 %s，切成 %d 片" % (fmt(total), len(pieces)))

    work = cache_dir(path)
    work.mkdir(parents=True, exist_ok=True)
    cache_file = work / "chunks.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.is_file() else {}

    chunks, spent, spent_new = [], 0.0, 0.0
    for i, (start, dur) in enumerate(pieces):
        key = "%.1f-%.1f" % (start, dur)
        if key in cache:
            text, ms = cache[key]["text"], cache[key]["ms"]
        else:
            ogg = work / ("%03d.ogg" % i)
            encode(ff, path, start, dur, ogg)
            if ogg.stat().st_size > 8 * 1024 * 1024:
                raise RuntimeError("第 %d 片超过 8MiB" % (i + 1))
            j = asr(ogg)
            text, ms = j.get("text", ""), j.get("duration_ms", int(dur * 1000))
            if not str(text).strip():
                # 空片不写缓存：写进去就等于永久承认"这段没声音"，重跑也读回来。
                # 保留 ogg 方便重听定位；重跑时这一片会重试，前面成功的片段照样走缓存。
                raise RuntimeError("第 %d 片（%s 起 %d 秒）返回空文本，没写缓存；"
                                   "看一下 %s 是静音还是接口抽风"
                                   % (i + 1, fmt(start), dur, ogg))
            cache[key] = {"text": text, "ms": ms}
            cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
            ogg.unlink(missing_ok=True)
            spent_new += ms / 1000
        spent += ms / 1000
        chunks.append((start, dur, text))
        log("  [%d/%d] %s 完成 %d 字" % (i + 1, len(pieces), fmt(start), len(text)))

    return chunks, spent, total, spent_new


def make_folder(stem, force=False):
    date = time.strftime("%Y-%m-%d")
    base = "%s-%s" % (date, slugify(stem))
    folder = ARGS.out / base
    if folder.exists() and not force:
        log("  文件夹已存在，跳过（--force 覆盖）")
        return None
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def process(path, state, asr_only=False, force=False):
    dg = digest(path)
    kind = ARGS.kind or "self"
    log("▶ %s (%.1f MB) · %s" % (path.name, path.stat().st_size / 1048576,
                                 "自语" if kind == "self" else "外部音频"))
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("找不到 ffmpeg，先跑 scripts/setup.sh")

    folder = make_folder(path.stem, force)
    if folder is None:
        return None, 0.0

    chunks, spent, total, spent_new = transcribe(ff, path)
    cost = spent / 3600 * PRICE_YUAN_PER_HOUR
    log("  转写费：本次新转写 ¥%.3f，全片累计 ¥%.3f（%.1f 分钟）"
        % (spent_new / 3600 * PRICE_YUAN_PER_HOUR, cost, spent / 60))

    meta = {
        "id": folder.name,
        "title": path.stem,
        "type": "voice-index",
        "date": time.strftime("%Y-%m-%d"),
        "captured_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
        "duration": fmt(total),
        "duration_sec": int(total),
        "source": path.name,
        "audio": "../archive/" + path.name,
        "transcript": "[[transcript]]",
        "notes": "[[notes]]" if not asr_only else None,
        "asr_model": "deepseek-asr",
        "asr_chunks": len(chunks),
        "asr_cost": round(cost, 4),
        "llm_model": None if asr_only else CFG["LLM_MODEL"],
        "status": "transcribed" if asr_only else "draft",
        "kind": kind,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tags": ["语音", "自语" if kind == "self" else "播客"],
    }
    write_transcript(folder, meta, chunks)

    if not asr_only:
        text = "\n".join("[%s] %s" % (fmt(s), t.strip()) for s, _, t in chunks)
        log("  整理中（%d 字）" % len(text))
        raw_text = text
        clean = prompt("clean.md")
        if clean.strip():
            text = chat(clean, raw_text)
            log("  清洗完成（%d 字 → %d 字）" % (len(raw_text), len(text)))
        report_cleaned([fmt(c[0]) for c in chunks], raw_text, text)
        write_cleaned(folder, meta, text, model=CFG["LLM_MODEL"])
        tpl = "structure.md" if kind == "self" else "external.md"
        body = chat(prompt(tpl), text)
        body, nq, ns = stamp_quotes(body, text)
        if nq or ns:
            log("  标注：%d 条 [原话] / %d 条 [归纳]" % (nq, ns))
        log("  结构化完成（%s）" % tpl)
        write_notes(folder, meta, body, based="[[cleaned]]")
        write_index(folder, meta, chunks, first_summary(body))
    else:
        write_index(folder, meta, chunks, "")

    if not ARGS.no_archive:
        ARGS.archive.mkdir(parents=True, exist_ok=True)
        dest = ARGS.archive / path.name
        if dest.exists():
            dest = ARGS.archive / ("%s-%s%s" % (path.stem, time.strftime("%H%M%S"),
                                                path.suffix))
        path.replace(dest)

    state[dg] = {"done": time.time(), "folder": folder.name, "cost": round(cost, 4)}
    ARGS.state.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    log("  ✅ " + str(folder))
    return folder, cost


def ai_only(folder, state):
    """根据 cleaned.md（没有就从 transcript.md 重新清洗）重跑整理，不碰音频"""
    folder = Path(folder).expanduser()
    if folder.is_file():
        folder = folder.parent
    tp, ip, np_ = folder / "transcript.md", folder / "index.md", folder / "notes.md"
    if not tp.is_file():
        raise RuntimeError("找不到 %s" % tp)

    chunks = parse_transcript(tp)
    if not chunks:
        raise RuntimeError("transcript.md 里没解析出段落")

    meta = {}
    if ip.is_file():
        raw = ip.read_text(encoding="utf-8")
        for line in raw.split("---")[1].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"')

    kind = ARGS.kind or meta.get("kind") or "self"
    meta.update({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    log("▶ 重跑整理：%s（%d 段 · %s）"
        % (folder.name, len(chunks), "自语" if kind == "self" else "外部音频"))

    text = ""
    cp = folder / "cleaned.md"
    if cp.is_file() and not ARGS.reclean:
        text = read_cleaned(cp)
        if text:
            log("  读 cleaned.md（%d 字，跳过清洗）" % len(text))
    wrote_cleaned = False
    if not text:
        text = "\n".join("[%s] %s" % (ts, t) for ts, t in chunks)
        raw_text = text
        clean = prompt("clean.md")
        if clean.strip():
            text = chat(clean, raw_text)
            log("  清洗完成（%d 字 → %d 字）" % (len(raw_text), len(text)))
        report_cleaned([ts for ts, _ in chunks], raw_text, text)
        write_cleaned(folder, meta, text, model=CFG["LLM_MODEL"])
        wrote_cleaned = True
    tpl = "structure.md" if kind == "self" else "external.md"
    body = chat(prompt(tpl), text)
    body, nq, ns = stamp_quotes(body, text)
    if nq or ns:
        log("  标注：%d 条 [原话] / %d 条 [归纳]" % (nq, ns))
    log("  结构化完成（%s）" % tpl)

    meta.setdefault("id", folder.name)
    meta.update({"type": "voice-index", "status": "draft",
                 "kind": kind,
                 "llm_model": CFG["LLM_MODEL"],
                 "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    meta.setdefault("title", folder.name)
    meta.setdefault("duration", chunks[-1][0])
    meta.setdefault("asr_chunks", len(chunks))
    meta.setdefault("asr_cost", 0)
    meta.setdefault("captured_at", "")
    # index.md 里读回来的是字符串，转回收费/片数字段
    meta["asr_chunks"] = int(float(meta["asr_chunks"]))
    meta["asr_cost"] = float(meta["asr_cost"] or 0)
    meta["duration_sec"] = int(float(meta.get("duration_sec") or 0))
    meta["tags"] = ["语音", "自语" if kind == "self" else "播客"]

    write_notes(folder, meta, body, based="[[cleaned]]")
    write_index(folder, meta, chunks, first_summary(body))
    log("  ✅ 已更新 %snotes.md 与 index.md"
        % ("cleaned.md、" if wrote_cleaned else ""))


def from_subtitle(path, state):
    """字幕文件直接进管线：不碰音频，不花转写费

    字幕已经带标点，默认**跳过模型清洗**——拿模型去"清洗"一份人工字幕，
    只会把人家辛苦断的句和标点改坏。结构整理仍走同一套 structure/external，
    所以产出还是四个文件，downstream 的用法不用分开记。
    """
    path = Path(path).expanduser()
    if not path.is_file():
        raise RuntimeError("找不到字幕文件：%s" % path)
    sys.path.insert(0, str(HERE))
    import subtitle

    cues = subtitle.parse(path)
    if not cues:
        raise RuntimeError("没解析出字幕：%s（支持 .srt/.vtt/.ass）" % path.name)
    chunks = subtitle.to_chunks(cues)
    total = int(cues[-1][1])
    kind = ARGS.kind or "external"
    if not ARGS.kind:
        log("  字幕默认按「别人的音频」整理，自己的视频加 --kind self")
    log("▶ %s（%d 条字幕 → %d 段 %s）" % (path.name, len(cues), len(chunks), fmt(total)))
    if ARGS.dry_run:
        for s, _d, t in chunks[:3]:
            log("  %s %s…" % (fmt(s), t[:40]))
        if len(chunks) > 3:
            log("  …（只列前 3 段）")
        log("  --dry-run：解析完成，未写文件、未调模型")
        return None, 0.0

    dg = digest(path)
    folder = make_folder(path.stem, ARGS.force)
    if folder is None:
        return None, 0.0

    meta = {
        "id": folder.name,
        "title": path.stem,
        "type": "voice-index",
        "date": time.strftime("%Y-%m-%d"),
        "captured_at": time.strftime("%Y-%m-%d %H:%M",
                                     time.localtime(path.stat().st_mtime)),
        "duration": fmt(total),
        "duration_sec": total,
        "source": path.name,
        "input": "subtitle",
        "transcript": "[[transcript]]",
        "notes": "[[notes]]",
        "asr_model": "subtitle",
        "asr_chunks": len(chunks),
        "asr_cost": 0,
        "llm_model": CFG["LLM_MODEL"],
        "status": "draft",
        "kind": kind,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tags": ["字幕", "自语" if kind == "self" else "播客"],
    }
    write_transcript(folder, meta, chunks)

    text = "\n".join("[%s] %s" % (fmt(s), t) for s, _, t in chunks)
    meta["clean_note"] = "字幕自带标点，未做模型清洗；只去了滚动重复并按停顿分段"
    write_cleaned(folder, meta, text, llm="未清洗（字幕原文）")
    meta.pop("clean_note")

    tpl = "structure.md" if kind == "self" else "external.md"
    body = chat(prompt(tpl), text)
    body, nq, ns = stamp_quotes(body, text)
    if nq or ns:
        log("  标注：%d 条 [原话] / %d 条 [归纳]" % (nq, ns))
    write_notes(folder, meta, body, based="[[cleaned]]")
    write_index(folder, meta, chunks, first_summary(body))
    log("  ✅ %s（未花转写费）" % folder.name)

    if not ARGS.no_archive:
        ARGS.archive.mkdir(parents=True, exist_ok=True)
        dest = ARGS.archive / path.name
        if dest.exists():
            dest = ARGS.archive / ("%s-%s%s" % (path.stem, time.strftime("%H%M%S"),
                                               path.suffix))
        path.replace(dest)

    state[dg] = {"done": time.time(), "folder": folder.name, "cost": 0.0}
    ARGS.state.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return folder, 0.0


def inbox_path(date=None):
    d = date or time.strftime("%Y-%m-%d")
    return ARGS.notes_dir / ("%s.md" % d)


def add_thought(text):
    p = inbox_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text("---\ntype: inbox\ndate: %s\n---\n\n# %s 碎片\n\n"
                     % (time.strftime("%Y-%m-%d"), time.strftime("%Y-%m-%d")),
                     encoding="utf-8")
    with p.open("a", encoding="utf-8") as f:
        f.write("- %s %s\n" % (time.strftime("%H:%M"), text.strip()))
    log("✅ 已追加到 %s" % p)


def parse_fragments(p):
    raw = p.read_text(encoding="utf-8")
    if raw.startswith("---"):
        raw = raw.split("---", 2)[-1]
    out = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^[-*]\s*(?:(\d{1,2}:\d{2})\s*)?(.+)$", s)
        if m and m.group(2).strip():
            out.append((m.group(1) or "", m.group(2).strip()))
    return out


def daily(date="", force=False):
    p = inbox_path(date)
    if not p.is_file():
        log("找不到 %s（用 --add \"想法\" 先记一条）" % p)
        return
    frags = parse_fragments(p)
    if len(frags) < 2:
        log("只有 %d 条碎片，太少，先不聚合。" % len(frags))
        return

    target = ARGS.out / "daily" / ("%s-日结.md" % p.stem)
    if target.exists() and not force:
        log("已存在 %s，要重跑加 --force" % target.name)
        return

    log("▶ 聚合 %s（%d 条碎片）" % (p.name, len(frags)))
    text = "\n".join("%s %s" % (t, x) if t else x for t, x in frags)
    body = chat(prompt("fragments.md"), text, max_tokens=4000)

    target.parent.mkdir(parents=True, exist_ok=True)
    head = front_matter({
        "id": p.stem + "-日结",
        "type": "daily",
        "date": p.stem,
        "fragments": len(frags),
        "llm": CFG["LLM_MODEL"],
        "source": "[[%s]]" % p.stem,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tags": ["语音", "日结"],
    })
    target.write_text(head + "\n\n" + body.strip() + "\n", encoding="utf-8")
    log("  ✅ " + str(target))


def check_keys():
    k = CFG.get("DEEPSEEK_API_KEY", "")
    if not k:
        log("✗ 没有 DEEPSEEK_API_KEY")
        return False
    try:
        r = requests.get(CFG["LLM_BASE"].rstrip("/") + "/models",
                         headers={"Authorization": "Bearer " + k}, timeout=20)
    except Exception as e:
        log("✗ 连不上 DeepSeek：%s" % e)
        return False
    if r.status_code != 200:
        log("✗ Key 无效（HTTP %s）" % r.status_code)
        return False
    models = [m["id"] for m in r.json().get("data", [])]
    log("✓ Key 可用，账号下模型：" + ", ".join(models[:6]))
    log("  ASR: POST /v1/tools/asr（≤120 秒 / ≤8MiB，0.39 元/小时）")
    ff = find_ffmpeg()
    log("  ffmpeg: " + (ff if ff else "✗ 未找到，需要 bash scripts/setup.sh"))
    log("配置来源：" + CFG.get("_from", "环境变量（无配置文件）"))
    log("整理模型：%s" % CFG["LLM_MODEL"])
    return bool(ff)


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--inbox", default="", help="音频目录，默认 ./inbox")
    ap.add_argument("--out", default="./out")
    ap.add_argument("--archive", default="./archive")
    ap.add_argument("--work", default="./work", help="切片缓存目录")
    ap.add_argument("--state", default="./.asr_state.json")
    ap.add_argument("--prompts", default="", help="提示词目录")
    ap.add_argument("--config", default="", help="config.env 路径")
    ap.add_argument("--no-archive", action="store_true", help="不移动源文件")
    ap.add_argument("--asr-only", action="store_true", help="只转写，不整理")
    ap.add_argument("--ai-only", metavar="DIR", help="只重跑 AI 整理")
    ap.add_argument("--force", action="store_true", help="重跑已处理的文件")
    ap.add_argument("--reclean", action="store_true",
                    help="忽略已有的 cleaned.md，重新清洗并覆盖")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true", help="只验 Key 和 ffmpeg")
    ap.add_argument("--text", help="跳过转写，直接整理文本文件")
    ap.add_argument("--srt", metavar="FILE",
                    help="字幕文件（.srt/.vtt/.ass）直接整理，跳过 ASR，无转写费")
    ap.add_argument("--add", metavar="TEXT", help="往今天的碎片笔记追加一条")
    ap.add_argument("--daily", action="store_true", help="聚合当天碎片，出一份日结")
    ap.add_argument("--date", default="", help="配合 --daily，指定日期 YYYY-MM-DD")
    ap.add_argument("--notes-dir", default="./00-Inbox", help="碎片笔记目录")
    ap.add_argument("--kind", choices=["self", "external"], default=None,
                    help="self=自己口述（默认），external=播客/讲座等别人的音频")
    ARGS = ap.parse_args()

    if not ARGS.inbox:
        ARGS.inbox = "./inbox" if Path("./inbox").is_dir() else "."
    for k in ("inbox", "out", "archive", "work", "notes_dir"):
        setattr(ARGS, k, Path(getattr(ARGS, k)).expanduser().resolve())
    ARGS.state = Path(ARGS.state).expanduser().resolve()
    load_cfg(ARGS.config or None)

    if ARGS.check:
        sys.exit(0 if check_keys() else 1)

    if ARGS.add:
        add_thought(ARGS.add)
        return

    if ARGS.daily:
        if not CFG.get("DEEPSEEK_API_KEY"):
            sys.exit("缺少 DEEPSEEK_API_KEY")
        daily(ARGS.date, ARGS.force)
        return

    if ARGS.text:
        p = Path(ARGS.text).expanduser()
        src_text = p.read_text(encoding="utf-8")
        body = chat(prompt("structure.md"), src_text)
        body, nq, ns = stamp_quotes(body, src_text)
        if nq or ns:
            log("  标注：%d 条 [原话] / %d 条 [归纳]" % (nq, ns))
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        folder = ARGS.out / ("%s-%s" % (time.strftime("%Y-%m-%d"), slugify(p.stem)))
        folder.mkdir(parents=True, exist_ok=True)
        meta = {"id": folder.name, "title": p.stem,
                "date": time.strftime("%Y-%m-%d"), "source": p.name,
                "llm_model": CFG["LLM_MODEL"], "generated_at": now}
        write_notes(folder, dict(meta, status="draft"), body, based="")
        write_text_index(folder, meta, first_summary(body))
        log("✅ " + str(folder))
        return

    state = json.loads(ARGS.state.read_text(encoding="utf-8")) if ARGS.state.is_file() else {}

    if ARGS.srt:
        if not CFG.get("DEEPSEEK_API_KEY") and not ARGS.dry_run:
            sys.exit("缺少 DEEPSEEK_API_KEY")
        from_subtitle(ARGS.srt, state)
        return

    if ARGS.ai_only:
        ai_only(ARGS.ai_only, state)
        return

    files = ([Path(f).expanduser() for f in ARGS.files] if ARGS.files
             else gather(dry=ARGS.dry_run))
    todo = [f for f in files if ARGS.force or digest(f) not in state]

    if not todo:
        log("没有新音频（处理过的会自动跳过；要重跑加 --force）。")
        return
    for f in todo:
        log("待处理：%s (%.1f MB)" % (f.name, f.stat().st_size / 1048576))

    if ARGS.dry_run:
        log("dry-run：未调用任何 API。")
        return
    if not CFG.get("DEEPSEEK_API_KEY"):
        sys.exit("缺少 DEEPSEEK_API_KEY")

    ok, total_cost = 0, 0.0
    for f in todo:
        try:
            _, c = process(f, state, asr_only=ARGS.asr_only, force=ARGS.force)
            ok += 1
            total_cost += c
        except Exception as e:
            log("❌ %s：%s" % (f.name, e))
            ARGS.work.mkdir(parents=True, exist_ok=True)
            with (ARGS.work / "error.log").open("a", encoding="utf-8") as fh:
                fh.write("\n%s %s\n%s" % (time.strftime("%F %T"), f.name,
                                          traceback.format_exc()))
    log("完成 %d/%d，转写费合计约 ¥%.3f" % (ok, len(todo), total_cost))
    if ok < len(todo):
        sys.exit(1)


if __name__ == "__main__":
    main()
