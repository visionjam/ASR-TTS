#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文章 → TTS（DeepSeek / 豆包）→ 一期带字幕和章节的 m4a

    inbox/某篇文章.md
    out/2026-11-18-某篇文章/
    ├─ 2026-11-18-某篇文章.m4a   音频，内嵌章节（chpl）
    ├─ 2026-11-18-某篇文章.srt   字幕，可 grep 定位
    ├─ script.md                 带 [mm:ss] 的口播稿（源，可改后重跑）
    └─ index.md                  元数据卡片

    python3 cast.py --check                     只验 Key / ffmpeg，不联网
    python3 cast.py --dry-run                   出计划、估钱、估时长，不花钱
    python3 cast.py a.md                        处理单篇
    python3 cast.py                            处理 ./inbox 里所有新的
    python3 cast.py --rewrite a.md              先让 LLM 改写成口播稿再读
    python3 cast.py --force a.md                重跑（仍复用没变的段）
    python3 cast.py --provider doubao a.md      用豆包合成（默认 deepseek）

Key 读取顺序：--config > $CAST_CONFIG > ./config.env > ../config.env >
~/.config/asr-notes/env > 环境变量。
DEEPSEEK_API_KEY 和 asr-notes 共用一个；豆包另需 DOUBAO_API_KEY（新版控制台的
X-Api-Key，一串 UUID），两家账单各自独立。

代码分三层，改之前先看改的是哪一层：

    cast_common.py  log / 时间格式 / 计费字符 / 显示宽度 —— 全模块共用
    cast_text.py    解析、切句、断行、时间轴插值 —— 纯函数，不联网
    cast_render.py  srt / script.md / index.md / ffmetadata —— 纯函数
    cast.py         配置、ffmpeg、TTS 调用、拼装、主流程（本文件）
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("缺少 requests：pip install requests")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cast_common import billed_chars, digest, fmt, log, slugify, visual_len  # noqa: E402
import cast_render as R                                                    # noqa: E402
import cast_text as T                                                      # noqa: E402

TTS_API = "https://api.deepseek.com/v1/tools/tts"
LLM_API = "https://api.deepseek.com/v1/chat/completions"

# 豆包语音合成 2.0：HTTP chunked 单向流式，一次进文本、流式回音频
DOUBAO_API = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"

# 限时五折 1.5 元/万计费字符，刊例 3。改价只改这里。
PRICE_PER_10K = 1.5
VOICES = ("stella", "tide")           # 女声 / 男声，官方就这两个

# 两家的计费口径不一样，`cjk` 就是「一个汉字算几个计费字符」：
#   deepseek 2（官方口径）   doubao 1（官方计费说明明写）
# 所以同一段文字，deepseek 的 billed 是 doubao 的两倍——比价时别看单价，
# 看折算后的「元/万汉字」。价格都取官网原价，活动折扣看控制台。
PROVIDERS = {
    "deepseek": {
        "price": PRICE_PER_10K, "cjk": 2, "limit": 10000,
        "model": "deepseek-tts", "label": "DeepSeek TTS",
    },
    "doubao": {
        "price": 3.0, "cjk": 1, "limit": 2000,
        "model": "doubao-seed-tts-2.0", "label": "豆包语音合成 2.0",
        "default_voice": "zh_male_liufei_uranus_bigtts",
    },
}
# --voice stella/tide 是 DeepSeek 的叫法。切到豆包时按性别对过去，
# 免得「换了 provider，音色却还是旧名字」直接报 400。
VOICE_FALLBACK = {"tide": "zh_male_liufei_uranus_bigtts",
                  "stella": "zh_female_vv_uranus_bigtts"}

MAX_SEG_CHARS = 600                   # 视觉宽度上限 ≈ 600 汉字

SIL_HEAD = 1.2                        # 小标题前后留白
SIL_PARA = 0.7                        # 段间留白
SILENCE = {"h": (SIL_HEAD, SIL_HEAD), "p": (0.0, SIL_PARA)}

CFG = {}
ARGS = None


# ------------------------------------------------------------------ 配置

def load_cfg(explicit=None):
    CFG.update({"VOICE": "tide", "LLM_MODEL": "deepseek-chat",
                "PRICE_PER_10K": PRICE_PER_10K,
                "TTS_PROVIDER": "deepseek",
                "DOUBAO_RESOURCE_ID": "seed-tts-2.0",
                "DOUBAO_SPEECH_RATE": "0"})
    cands = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("CAST_CONFIG"):
        cands.append(Path(os.environ["CAST_CONFIG"]))
    cands += [Path.cwd() / "config.env", Path.cwd().parent / "config.env",
              Path.home() / ".config" / "asr-notes" / "env"]
    for p in cands:
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                CFG[k.strip()] = v.strip().strip("\"'")
            CFG["_cfg_file"] = str(p)
            break
    for k in ("DEEPSEEK_API_KEY", "CAST_VOICE", "LLM_MODEL", "PRICE_PER_10K",
              "TTS_PROVIDER", "DOUBAO_API_KEY", "DOUBAO_VOICE",
              "DOUBAO_RESOURCE_ID", "DOUBAO_SPEECH_RATE"):
        if os.environ.get(k):
            CFG[k] = os.environ[k]
    try:
        CFG["PRICE_PER_10K"] = float(CFG.get("PRICE_PER_10K") or PRICE_PER_10K)
    except ValueError:
        CFG["PRICE_PER_10K"] = PRICE_PER_10K
    CFG["VOICE"] = CFG.get("CAST_VOICE") or CFG.get("VOICE") or "tide"


def provider():
    """当前 provider 的名字和它的参数表（价格 / 计费口径 / 上限 / 默认音色）。

    --provider > TTS_PROVIDER > 默认 deepseek。默认值故意不动：
    换 provider 是显式动作，不能因为配置里多个变量就悄悄换了账单。
    """
    name = (getattr(ARGS, "provider", None) or CFG.get("TTS_PROVIDER")
            or "deepseek").strip().lower()
    if name not in PROVIDERS:
        raise RuntimeError("未知的 TTS_PROVIDER：%s（可选 %s）"
                           % (name, " / ".join(PROVIDERS)))
    p = dict(PROVIDERS[name])
    if name == "deepseek":
        # 兼容老的 PRICE_PER_10K 配置；CFG 还没读（如自测直接调函数）时用默认值
        try:
            p["price"] = float(CFG.get("PRICE_PER_10K") or PRICE_PER_10K)
        except (TypeError, ValueError):
            p["price"] = PRICE_PER_10K
    return name, p


def key():
    k = CFG.get("DEEPSEEK_API_KEY", "")
    if not k:
        raise RuntimeError("没有 DEEPSEEK_API_KEY：在数据目录（库根）的 config.env 里填一行，"
                           "或设环境变量；和 asr-notes 共用同一个 Key")
    return k


def doubao_key():
    k = CFG.get("DOUBAO_API_KEY", "")
    if not k:
        raise RuntimeError("没有 DOUBAO_API_KEY：去豆包语音控制台建一个 API Key，"
                           "写进 config.env 的 DOUBAO_API_KEY 一行")
    return k


def normalize_voice(voice):
    """把音色名对齐到当前 provider。

    stella/tide 是 DeepSeek 的；zh_*_bigtts 是豆包的。走错家会直接 400，
    所以在发请求前就换掉，而不是等接口报错。
    """
    name, p = provider()
    voice = (voice or "").strip()
    if name == "doubao":
        if voice in VOICE_FALLBACK:
            return VOICE_FALLBACK[voice]
        return voice or CFG.get("DOUBAO_VOICE") or p["default_voice"]
    if not voice:
        return CFG["VOICE"]
    if voice not in VOICES:
        log("  ! 音色 %s 不是 DeepSeek 的，回落到 %s" % (voice, CFG["VOICE"]))
        return CFG["VOICE"]
    return voice



def find_ffmpeg():
    cand = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")
    if cand and Path(cand).exists():
        return cand
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


# ------------------------------------------------------------------ 计划

def chunk_paragraph(text, limit=MAX_SEG_CHARS):
    """自然段 → 若干朗读单元，切点落在句末。

    短段原样返回（保住整段一口气读下来的韵律），太长才切——
    切太碎听感像播报机，切太粗字幕插值误差会累积。
    """
    if visual_len(text) <= limit:
        return [text]
    out, cur, acc = [], [], 0.0
    for s in T.split_sentences(text, max_cue=limit):
        w = visual_len(s)
        if acc + w > limit and cur:
            out.append("".join(cur))
            cur, acc = [], 0.0
        cur.append(s)
        acc += w
    if cur:
        out.append("".join(cur))
    return out


def truncate_blocks(blocks, limit):
    """只留前 limit 个字符，切点落在句末。用于 --preview 试音。

    试音的意义：整篇烧完才发现音色不对/语速太快，钱和时间都白花。
    300 字大约 1 分钟，花 5 分钱。
    """
    out, used = [], 0
    for b in blocks:
        t = b["text"]
        if used + len(t) <= limit:
            out.append(b)
            used += len(t)
            continue
        room = limit - used
        if room > 30:
            buf = ""
            for s in T.split_sentences(t):
                if len(buf) + len(s) > room:
                    break
                buf += s
            if buf:
                out.append({"kind": b["kind"], "text": buf})
        break
    return out or blocks[:1]


def build_plan(article):
    segs = []
    for b in article["blocks"]:
        if b["kind"] == "h":
            segs.append({"kind": "h", "text": b["text"]})
        else:
            for piece in chunk_paragraph(b["text"]):
                segs.append({"kind": "p", "text": piece})
    name, p = provider()
    for i, s in enumerate(segs, 1):
        s["i"] = i
        s["hash"] = digest(s["text"])
        s["billed"] = billed_chars(s["text"], p["cjk"])
        s["chars"] = len(s["text"])
    return segs


def plan_report(segs):
    name, p = provider()
    chars = sum(s["chars"] for s in segs)
    billed = sum(s["billed"] for s in segs)
    est = sum(visual_len(s["text"]) * 60.0 / 250.0 for s in segs)
    heads = sum(1 for s in segs if s["kind"] == "h")
    per_char = "汉字算 %d" % p["cjk"]
    lines = [
        "计划：%d 段（小标题 %d）· %s" % (len(segs), heads, p["label"]),
        "字数 %d，计费字符 %d（%s）" % (chars, billed, per_char),
        "预估时长 %s，预估花费 约 %.2f 元（%.1f 元/万计费字符 ≈ %.1f 元/万汉字）"
        % (fmt(est), billed / 10000.0 * p["price"], p["price"],
           p["price"] * p["cjk"]),
    ]
    over = [s for s in segs if s["billed"] > p["limit"]]
    if over:
        lines.append("⚠ %d 段超过单次接口上限 %d，会被切开"
                     % (len(over), p["limit"]))
    return "\n".join(lines)


# ------------------------------------------------------------------ TTS

class Retryable(Exception):
    pass


def tts_call(text, voice):
    """一次合成 → wav 字节。走哪家由 --provider / TTS_PROVIDER 决定。"""
    name, _ = provider()
    fn = _tts_doubao if name == "doubao" else _tts_deepseek
    for attempt in range(3):
        try:
            return fn(text, voice)
        except ValueError:
            raise                                  # 文本问题，重试没用
        except Retryable as e:
            if attempt == 2:
                raise
            log("  … 重试（%s）" % e)
            time.sleep(2 ** attempt)
        except requests.RequestException as e:
            if attempt == 2:
                raise
            log("  … 网络重试（%s）" % e)
            time.sleep(2 ** attempt)
    raise Retryable("unreachable")


def _tts_deepseek(text, voice):
    with requests.post(
        TTS_API,
        headers={"Authorization": "Bearer " + key(),
                 "Content-Type": "application/json"},
        json={"text": text, "voice_id": voice, "format": "wav"},
        stream=True, timeout=(20, 300),
    ) as r:
        if r.status_code == 200:
            return _stream(r)
        body = r.text[:300]
        if r.status_code == 400:
            raise ValueError("400 %s" % body)
        raise Retryable("%d %s" % (r.status_code, body))


def _stream(resp):
    return b"".join(c for c in resp.iter_content(65536) if c)


def _tts_doubao(text, voice):
    """豆包单向流式：回来的**不是裸音频**，是一串 JSON，音频在 data 里（base64）。

    所以不能像 DeepSeek 那样直接拼字节——拼出来是 JSON 文本，
    `startswith(b"RIFF")` 立刻挂。要逐块解开 base64 再拼。
    实测 600 字一段能一次合成完（约 5MB wav），不用额外切碎。
    """
    body = {
        "user": {"uid": "tts-cast"},
        "req_params": {
            "text": text,
            "speaker": voice,
            "audio_params": {
                "format": "wav",           # 保住 RIFF 头，probe_duration 不用改
                "sample_rate": 24000,
                "speech_rate": int(CFG.get("DOUBAO_SPEECH_RATE") or 0),
            },
        },
    }
    with requests.post(
        DOUBAO_API,
        headers={"X-Api-Key": doubao_key(),
                 "X-Api-Resource-Id": CFG.get("DOUBAO_RESOURCE_ID")
                 or "seed-tts-2.0",
                 "X-Api-Request-Id": str(uuid.uuid4()),
                 "Content-Type": "application/json"},
        json=body, stream=True, timeout=(20, 600),
    ) as r:
        if r.status_code == 200:
            audio = _doubao_stream(r)
        else:
            msg = r.text[:300]
            if r.status_code == 400:               # 文本不过审 → 跳过该段
                raise ValueError("400 %s" % msg)
            if r.status_code in (401, 403):
                # 重试没用，而且绝不能当成「跳过」——否则整篇会静悄悄变成空音频
                raise RuntimeError(
                    "%d %s（Key 不对，或「语音合成大模型」没在控制台开通）"
                    % (r.status_code, msg))
            raise Retryable("%d %s" % (r.status_code, msg))
    if not audio:
        raise Retryable("豆包返回空音频")
    return audio


def _doubao_stream(resp):
    """把 JSON 流里的 base64 音频块拼回 wav。

    响应是一行一个 JSON 对象（可能带 `data:` 前缀，也可能被 TCP 切碎）：
        {"code":0,"message":"","data":"UklGR..."}
        {"code":0,...,"sentence":{"text":"…","words":[]}}   ← 字幕挂点
        {"code":20000000,"message":"OK","data":null}        ← 收尾

    words 现在是空的（没开 enable_subtitle）。将来要把 srt 从「段内插值」
    升级成字级时间轴，就在这个对象里拿，不用再发一次请求。
    """
    dec = json.JSONDecoder()
    buf = ""
    audio = bytearray()
    err = None
    for chunk in resp.iter_content(8192):
        if not chunk:
            continue
        buf += chunk.decode("utf-8", "ignore")
        while True:
            i = buf.find("{")
            if i < 0:
                buf = ""
                break
            try:
                obj, j = dec.raw_decode(buf, i)
            except ValueError:
                buf = buf[i:]                      # 半个对象，等下一块
                break
            buf = buf[j:]
            code = obj.get("code")
            data = obj.get("data")
            if data:
                try:
                    audio += base64.b64decode(data)
                except Exception:
                    pass
            elif code not in (0, 20000000, None) and obj.get("message"):
                err = "%s %s" % (code, obj.get("message"))
    if err:
        raise ValueError(err)                      # 200 里带错误码：当文本问题跳过
    return audio


def probe_duration(ff, path):
    p = subprocess.run([ff, "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", p.stderr)
    if not m:
        raise RuntimeError("读不出时长：%s" % path)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def _cache_hit(old, tag, workdir):
    """缓存命中判定：同一段文字 + 同一家 provider + 同一个音色。

    老缓存里没有 `v` 字段（那时只有 DeepSeek）。只在「默认档 deepseek|tide」
    时才认它，否则宁可重烧——两家的 wav 混在一起，听感会莫名其妙跳。
    """
    if not old or not old.get("wav") or not (workdir / old["wav"]).is_file():
        return False
    return old["v"] == tag if "v" in old else tag == "deepseek|tide"


def synth_all(segs, workdir, voice, resynth=False):
    """逐段合成。缓存粒度 = 段：改一个字只重烧那一段。

    缓存文件按 **hash 命名**，不按段号。按段号会出事：文章开头插一段，
    后面所有段号右移，第 3 段复用旧的 s0007.wav，而第 7 段重新合成又写
    同一个 s0007.wav —— 两段最后播的是同一句话。
    """
    ff = find_ffmpeg()
    name, p = provider()
    tag = "%s|%s" % (name, voice)
    state_p = workdir / "segments.json"
    cache = {}
    if state_p.is_file():
        try:
            for it in json.loads(state_p.read_text(encoding="utf-8")):
                cache[it["hash"]] = it
        except Exception:
            cache = {}

    done, spent, skipped = 0, 0, []
    for s in segs:
        old = cache.get(s["hash"])
        if (not resynth) and _cache_hit(old, tag, workdir):
            s["wav"], s["dur"] = old["wav"], old["dur"]
            log("  = 复用 %s（%s）" % (s["wav"], fmt(s["dur"])))
            continue
        wav = workdir / ("s_%s.wav" % s["hash"])
        text = s["text"]
        if s["billed"] > p["limit"]:
            text = text[:p["limit"] // p["cjk"]]
            log("  ! 段 %d 超上限，已截断" % s["i"])
        log("  ▶ 段 %d/%d · %d 字 · 约 %s"
            % (s["i"], len(segs), s["chars"],
               fmt(visual_len(text) * 60.0 / 250.0)))
        try:
            audio = tts_call(text, voice)
        except ValueError as e:                     # 内容审核 / 空文本
            log("  ✗ 跳过（模型拒读）：%s" % str(e)[:120])
            s["skipped"] = str(e)[:160]
            skipped.append(s)
            continue
        if not audio.startswith(b"RIFF"):
            raise RuntimeError("段 %d 返回的不是 wav（%d 字节）" % (s["i"], len(audio)))
        # 安卓共享存储是 FUSE，目录刚建好时另一个进程/句柄可能还看不见，
        # 这里补一次 mkdir，免得偶发 ENOENT 把一个跑了一半的任务整个打掉。
        workdir.mkdir(parents=True, exist_ok=True)
        wav.write_bytes(audio)
        s["wav"] = wav.name
        s["dur"] = probe_duration(ff, wav)
        spent += s["billed"]
        done += 1
        cache[s["hash"]] = {"hash": s["hash"], "wav": s["wav"],
                            "dur": s["dur"], "v": tag}
        state_p.write_text(json.dumps(list(cache.values()), ensure_ascii=False, indent=1),
                           encoding="utf-8")

    for s in segs:
        s.setdefault("dur", 0.0)
    log("合成本次 %d 段，跳过 %d 段，本次计费字符 %d" % (done, len(skipped), spent))
    return spent, skipped


# ------------------------------------------------------------------ 时间轴

def build_timeline(segs):
    """累计时间。返回 (总时长, 章节表)。章节取小标题，没有就整篇一章。"""
    t = 0.0
    chapters = []
    for s in segs:
        if s.get("skipped") or not s.get("dur"):
            s["start"] = s["end"] = t
            continue
        lead, tail = SILENCE[s["kind"]]
        t += lead
        s["start"] = t
        t += s["dur"]
        s["end"] = t
        t += tail
        if s["kind"] == "h":
            chapters.append((max(0.0, s["start"] - lead), s["text"]))
    # 第一个小标题之前如果还有正文，补一个「开篇」章节。
    # chpl 的第一章不从 0 开始的话，有些播放器的章节列表会少一截。
    if not chapters:
        chapters = [(0.0, "全文")]
    elif chapters[0][0] > 0.5:
        chapters.insert(0, (0.0, "开篇"))
    return t, chapters


def build_cues(segs, max_cue=T.MAX_CUE):
    """段 → 句，段内插值。字幕和歌词都从这里出，只是切得粗细不同。

    srt 可以两行（mov_text 不自动折行，我们自己断），
    lrc 只能一行，所以歌词要按更短的单位再切一遍。
    """
    cues = []
    for s in segs:
        if s.get("skipped") or not s.get("dur"):
            continue
        sents = T.split_sentences(s["text"], max_cue=max_cue)
        cues += T.allocate(sents, s["start"], s["dur"])
    return T.fix_cues(cues)


# ------------------------------------------------------------------ 拼装

def _silence(ff, workdir, sec):
    p = workdir / ("sil-%d.wav" % int(sec * 1000))
    if not p.is_file():
        subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "anullsrc=r=24000:cl=mono",
                        "-t", "%.2f" % sec, "-c:a", "pcm_s16le", str(p)], check=True)
    return p


def assemble(segs, workdir, outdir, name, total, chapters,
             srt=None, normalize=True, embed_sub=False):
    """拼装成 m4a。字幕要是也想塞进容器，就在这一趟里做完。

    别先出 m4a 再二次 remux 加字幕：`-c copy` 会把 srt 的 text tag 带进 mov，
    封装器直接报 "Tag text incompatible with output codec id"。一次成型最省事。
    """
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("找不到 ffmpeg：跑 scripts/setup.sh，或设 FFMPEG_BIN")

    sil_h = _silence(ff, workdir, SIL_HEAD)
    sil_p = _silence(ff, workdir, SIL_PARA)

    lines = []
    for s in segs:
        if s.get("skipped") or not s.get("dur"):
            continue
        lead, tail = SILENCE[s["kind"]]
        if lead:
            lines.append("file '%s'" % sil_h.resolve())
        lines.append("file '%s'" % (workdir / s["wav"]).resolve())
        if tail:
            lines.append("file '%s'" % (sil_h if s["kind"] == "h" else sil_p).resolve())
    if not lines:
        raise RuntimeError("没有任何可拼的段")

    lst = workdir / "concat.txt"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    merged = workdir / "merged.wav"
    subprocess.run([ff, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c:a", "pcm_s16le", "-ar", "24000",
                    "-ac", "1", str(merged)], check=True)

    meta_p = workdir / "chapters.txt"
    meta_p.write_text(R.render_ffmeta(chapters, total), encoding="utf-8")

    out_m4a = outdir / (name + ".m4a")
    cmd = [ff, "-y", "-v", "error", "-i", str(merged), "-i", str(meta_p)]
    if embed_sub and srt and Path(srt).is_file():
        cmd += ["-i", str(srt)]
    cmd += ["-map", "0:a"]
    if embed_sub and srt and Path(srt).is_file():
        cmd += ["-map", "2", "-c:s", "mov_text", "-metadata:s:s:0", "language=chi"]
    cmd += ["-map_metadata", "1", "-map_chapters", "1",
            "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-ar", "24000",
            "-movflags", "+faststart"]
    if normalize:
        cmd += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    cmd.append(str(out_m4a))
    subprocess.run(cmd, check=True)
    return out_m4a


# ------------------------------------------------------------------ 改写

def rewrite(article):
    """书面语 → 口播稿。这一步比换模型更影响听感，也便宜得多。"""
    prompt = (HERE / "prompts" / "rewrite.md").read_text(encoding="utf-8")
    body = "\n\n".join(("# " if b["kind"] == "h" else "") + b["text"]
                       for b in article["blocks"])
    r = requests.post(LLM_API, timeout=300,
                      headers={"Authorization": "Bearer " + key()},
                      json={"model": CFG["LLM_MODEL"], "temperature": 0.3,
                            "messages": [{"role": "system", "content": prompt},
                                         {"role": "user", "content": body}]})
    r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"]
    return T.parse_article(txt)


# ------------------------------------------------------------------ 主流程

def process(path, args):
    raw = Path(path).read_text(encoding="utf-8")
    art = T.parse_article(raw)
    log("▶ %s · %d 段" % (art["title"], len(art["blocks"])))

    if args.preview:
        # 试音只读前 N 个字，改写也跟着只改这一截（省钱，也省得模型乱扩写）
        art["blocks"] = truncate_blocks(art["blocks"], args.preview)
        log("  试音模式：只读前 %d 字（约 %s）"
            % (args.preview, fmt(sum(len(b["text"]) for b in art["blocks"]) * 60.0 / 250.0)))

    if args.rewrite:
        if args.dry_run:
            # dry-run 的承诺是「一分钱不花」，改写也是一次付费调用，不能偷偷发出去
            log("  --dry-run：跳过改写（那要花一次 LLM 调用），下面按原文估算")
        else:
            log("  改写中（%s）…" % CFG["LLM_MODEL"])
            art = rewrite(art)

    segs = build_plan(art)
    print(plan_report(segs))
    if args.dry_run:
        if args.verbose:
            for s in segs:
                print("  %-3d %s %4d字 %5d计费  %s"
                      % (s["i"], "H" if s["kind"] == "h" else " ",
                         s["chars"], s["billed"], s["text"][:28]))
        return None

    # 目录名就是这一期的身份。标题改一个字就换目录、换 work/、把整篇重新烧一遍，
    # 所以「从已有的 out/<目录>/script.md 重跑」必须沿用原目录名，不能重新 slugify。
    src = Path(path).expanduser().resolve()
    reuse_dir = (src.name == "script.md" and (src.parent / "index.md").is_file()
                 and src.parent.parent.name == Path(args.out).name)
    if reuse_dir:
        name = src.parent.name
        date = args.date or name[:10]
    else:
        date = args.date or time.strftime("%Y-%m-%d")
        slug = args.slug or slugify(art["title"])
        name = "%s-%s" % (date, slug)
    if args.preview:
        name += "-试音"
    outdir = Path(args.out) / name
    # 判「做过没有」看成品，不看目录：目录在建完之后、合成之前被打断（切后台被杀、
    # 手滑 Ctrl-C）会留下一个空壳，只看 exists() 的话这一期就被永远跳过，只能靠 --force 救。
    done = outdir.is_dir() and (any(outdir.glob("*.m4a")) or (outdir / "index.md").is_file())
    if done and not (args.force or args.resynth or args.preview):
        log("  已存在，跳过：%s（--force 重做产物，--resynth 全部重烧）" % outdir)
        return outdir
    if outdir.exists() and not done:
        log("  目录在、成品不在（上次中途断了），这次重做：%s" % outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    workdir = Path(args.work) / name
    workdir.mkdir(parents=True, exist_ok=True)

    voice = normalize_voice(args.voice)
    _, p = provider()           # 注意：别叫 name，上面的 name 是产出目录名
    spent, skipped = synth_all(segs, workdir, voice, resynth=args.resynth)

    # 先算时间轴和字幕，再拼装：字幕要内嵌的话得在同一趟 ffmpeg 里完成
    total, chapters = build_timeline(segs)
    cues = build_cues(segs)
    srt_p = outdir / (name + ".srt")
    srt_p.write_text(R.render_srt(cues), encoding="utf-8")
    # 歌词另切一遍：LRC 一行只能一行，36 个字放不下
    (outdir / (name + ".lrc")).write_text(
        R.render_lrc(build_cues(segs, max_cue=18), art["title"], "tts-cast"),
        encoding="utf-8")

    out_m4a = assemble(segs, workdir, outdir, name, total, chapters,
                       srt=srt_p, normalize=not args.no_normalize,
                       embed_sub=args.embed_sub)
    (outdir / "script.md").write_text(R.render_script(art["title"], segs),
                                      encoding="utf-8")
    # 累计花费记在 work/meta.json：index.md 只写「本次」的话，重跑一次就变 0.00，
    # 这期到底花过多少钱就查不出来了。
    meta_p = workdir / "meta.json"
    cum = {"billed_total": 0}
    if meta_p.is_file():
        try:
            cum = json.loads(meta_p.read_text(encoding="utf-8"))
        except Exception:
            pass
    cum["billed_total"] = int(cum.get("billed_total", 0)) + int(spent)
    meta_p.write_text(json.dumps(cum, ensure_ascii=False), encoding="utf-8")

    unit = p["price"] / 10000.0
    (outdir / "index.md").write_text(R.render_index({
        "date": date, "title": art["title"], "name": name,
        "source": str(path), "voice": voice, "model": p["model"],
        "chars": sum(s["chars"] for s in segs),
        "billed": sum(s["billed"] for s in segs),
        "duration": total, "cost": spent * unit,
        "cost_total": cum["billed_total"] * unit,
        "chapters": chapters,
        "skipped": [s["skipped"] for s in skipped],
    }), encoding="utf-8")

    log("✅ %s" % out_m4a.name)
    log("   时长 %s · %d 条字幕 · %d 个章节 · 约 %.2f 元"
        % (fmt(total), len(cues), len(chapters),
           spent / 10000.0 * p["price"]))
    return outdir


def check():
    ok = True
    name, p = provider()
    if name == "doubao":
        k, label = CFG.get("DOUBAO_API_KEY", ""), "DOUBAO_API_KEY"
    else:
        k, label = CFG.get("DEEPSEEK_API_KEY", ""), "DEEPSEEK_API_KEY"
    print("Provider : %s（%s）" % (name, p["label"]))
    # 只报长度和来源，不报内容——四个字符也不该出现在聊天记录里
    print("Key      : %s %s" % (label, "已读（%d 位）" % len(k) if k else "✗ 缺失"))
    ok &= bool(k)
    print("配置文件 : %s" % CFG.get("_cfg_file", "（未找到，只用环境变量）"))
    ff = find_ffmpeg()
    print("ffmpeg   : %s" % (ff or "✗ 缺失"))
    ok &= bool(ff)
    print("音色     : %s" % normalize_voice(None))
    print("单价     : %.1f 元/万计费字符 ≈ %.1f 元/万汉字（汉字算 %d）"
          % (p["price"], p["price"] * p["cjk"], p["cjk"]))
    if name == "doubao":
        print("资源     : %s · 语速 %s"
              % (CFG.get("DOUBAO_RESOURCE_ID"), CFG.get("DOUBAO_SPEECH_RATE")))
    print("状态     : %s" % ("可以跑" if ok else "先修上面标 ✗ 的"))
    return 0 if ok else 1


def main(argv=None):
    p = argparse.ArgumentParser(
        description="文章 → 带字幕和章节的 m4a（DeepSeek TTS）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="*", help="文章 .md/.txt；不给就扫 --inbox")
    p.add_argument("--inbox", default="inbox")
    p.add_argument("--out", default="out")
    p.add_argument("--work", default="work")
    p.add_argument("--config")
    p.add_argument("--provider", choices=sorted(PROVIDERS),
                   help="TTS 供应商，默认 deepseek；也可写 config.env 的 TTS_PROVIDER")
    p.add_argument("--voice",
                   help="DeepSeek: stella 女声 / tide 男声；"
                        "豆包: zh_male_liufei_uranus_bigtts 这类音色 ID")
    p.add_argument("--speech-rate", type=int,
                   help="豆包语速 [-50,100]，0 默认，-20 左右更像播客")
    p.add_argument("--date", help="目录日期前缀，默认今天")
    p.add_argument("--slug")
    p.add_argument("--preview", type=int, metavar="N",
                   help="只合成前 N 个字试音（300 字约 1 分钟、5 分钱）")
    p.add_argument("--rewrite", action="store_true", help="先让 LLM 改写成口播稿")
    p.add_argument("--dry-run", action="store_true", help="只出计划，不花钱")
    p.add_argument("--force", action="store_true",
                   help="覆盖已有产出目录（没改过的段仍复用，不重复计费）")
    p.add_argument("--resynth", action="store_true",
                   help="忽略缓存，所有段重新合成（会重新计费，改稿调试时才用）")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--no-normalize", action="store_true", help="不做 -16 LUFS 归一化")
    p.add_argument("--embed-sub", action="store_true", help="把字幕轨也塞进 m4a")
    p.add_argument("--check", action="store_true")
    p.add_argument("--all", action="store_true", help="扫 --inbox 时连已做过的也重跑")
    args = p.parse_args(argv)

    global ARGS
    ARGS = args
    load_cfg(args.config)
    if args.speech_rate is not None:
        CFG["DOUBAO_SPEECH_RATE"] = str(args.speech_rate)
    if args.check:
        return check()

    for d in (args.out, args.work):
        Path(d).mkdir(parents=True, exist_ok=True)

    files = [Path(f).expanduser() for f in args.files]
    if not files:
        inbox = Path(args.inbox)
        if not inbox.is_dir():
            log("没有 %s/ 也没有指定文件" % inbox)
            return 1
        ext = {".md", ".txt", ".markdown"}
        files = sorted(f for f in inbox.iterdir()
                       if f.is_file() and f.suffix.lower() in ext
                       and not f.name.startswith("."))
    if not files:
        log("%s/ 里没有 .md/.txt" % args.inbox)
        return 0

    rc = 0
    for f in files:
        try:
            process(f, args)
        except Exception as e:
            rc = 1
            log("✗ %s：%s" % (f.name, e))
            Path(args.work, "error.log").open("a", encoding="utf-8").write(
                "\n[%s] %s\n%s\n" % (time.strftime("%F %T"), f, traceback.format_exc()))
    return rc


if __name__ == "__main__":
    sys.exit(main())
