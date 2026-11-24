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
import verbatim as V                                                       # noqa: E402

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
    # 本机 Kokoro 服务（tools/local-tts/server.py），价格 0。
    # 音色由服务端按语言自动选：中文 zf_001 / 英文 af_heart。
    "local": {
        "price": 0.0, "cjk": 1, "limit": 600,
        "model": "kokoro", "label": "本地 Kokoro",
        "default_voice": "auto",
    },
}
# --voice stella/tide 是 DeepSeek 的叫法。切到豆包时按性别对过去，
# 免得「换了 provider，音色却还是旧名字」直接报 400。
VOICE_FALLBACK = {"tide": "zh_male_liufei_uranus_bigtts",
                  "stella": "zh_female_vv_uranus_bigtts"}

MAX_SEG_CHARS = 600                   # 视觉宽度上限 ≈ 600 汉字

# 口述稿（--spoken）：原文 → 大纲 → 按大纲分块落地。
# 块是「一节」，所以长度跟着原文小标题走，只在这几个数之间兜底。
SPOKEN_BLOCK_MIN = 3000               # 再短的块没必要单开一次调用
SPOKEN_BLOCK_MID = 4500               # 攒到这个长度，遇到段落边界就收
SPOKEN_BLOCK_MAX = 6000               # 单块硬上限
SPOKEN_MAX_CHARS = 50000              # 再长就分两篇；不做多层大纲（分块重排会丢全局视野）
SPOKEN_RATIO_FLOOR = 0.55             # 低于原文这个比例，就在大纲里标「删得偏多」
SPOKEN_RATIO_CEIL = 0.78              # 高于这个比例，等于只改句子没做口述稿，也标一句
SPOKEN_TARGET = (0.60, 0.72)          # 落地的目标区间，直接写进提示词当硬指标
SPOKEN_QC_DUP = 15                    # 落稿体检：连续多少字重复才算「讲了两遍」
SPOKEN_QC_LONG = 60                   # 落稿体检：单句超过多少字算「没拆干净」

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
                "DOUBAO_SPEECH_RATE": "0",
                "LOCAL_TTS_URL": "http://127.0.0.1:1919"})
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
              "DOUBAO_RESOURCE_ID", "DOUBAO_SPEECH_RATE",
              "LOCAL_TTS_URL", "LOCAL_VOICE"):
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
    if name == "local":
        # 服务端认 auto / zh / en / zf_001 / af_heart；stella/tide 是别家的叫法，
        # 传过去只会让服务端按 auto 处理，不如在这里就换掉。
        if not voice or voice in VOICES:
            return CFG.get("LOCAL_VOICE") or p["default_voice"]
        return voice
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


def split_plan(article):
    """文章 → 朗读单元。这一步不算 hash、不算钱，因为读法层还没动过文本。

    每段记下它来自第几个原文块（`block`），附录要把被摘掉的整块内容
    （代码块、表格）定位到「接在哪一段之后」。
    """
    segs = []
    for bi, b in enumerate(article["blocks"], 1):
        if b["kind"] == "h":
            segs.append({"kind": "h", "text": b["text"], "block": bi})
        else:
            for piece in chunk_paragraph(b["text"]):
                segs.append({"kind": "p", "text": piece, "block": bi})
    return segs


def finish_plan(segs):
    """算 hash / 计费 / 字数。

    hash 必须在读法层之后算：缓存按文本 hash 命中，谁先谁后决定了
    「改了读法」是只重烧真正变了的那几段，还是整篇重烧。
    """
    name, p = provider()
    for i, s in enumerate(segs, 1):
        s["i"] = i
        s["hash"] = digest(s["text"])
        s["billed"] = billed_chars(s["text"], p["cjk"])
        s["chars"] = len(s["text"])
    return segs


def build_plan(article):
    return finish_plan(split_plan(article))


def verbalize_on(args):
    """读法层默认开。--no-verbalize 或 config.env 里 CAST_VERBALIZE=0 才关。"""
    if getattr(args, "no_verbalize", False):
        return False
    v = (CFG.get("CAST_VERBALIZE") or "1").strip().lower()
    return v not in ("0", "off", "false", "no")


def verbalize_segs(segs):
    """读法层：只动符号和标记。返回摘出清单 [(seg 下标, 原文)]。"""
    d = V.load_dict()
    moved = []
    for i, s in enumerate(segs):
        new, ch = V.verbalize(s["text"], dict_=d)
        s["text"] = new
        for c in ch:
            if c["kind"] == "move":
                moved.append((i, c["before"]))
    return moved


def write_appendix(outdir, moved, segs, dropped=()):
    """摘出来的内容写进成品目录。定位只写「接在哪一段之后」——它们没被念过，
    所以没有自己的时间点，写一个反而是假的。

    时间取那一段的**结束**时刻，不是开始：说「接在 00:25 之后」，而 00:25 是
    这一段开始的地方，那就自相矛盾了。同一段里摘出的多条合在一个标题下。
    """
    bucket = {}
    for idx, text in moved:
        bucket.setdefault(idx, []).append(text)
    for d in dropped:
        pos = 0
        for i, s in enumerate(segs):
            b = s.get("block") or 0
            if b and b <= d["after_block"]:
                pos = i
            elif b > d["after_block"]:
                break
        bucket.setdefault(pos, []).append(d["text"])

    lines = ["# 原文附录", "",
             "朗读稿里摘掉的内容。它们没被念出来，所以只写它接在哪一段之后，"
             "不给它们自己的时间点。", ""]
    count = 0
    for idx in sorted(bucket):
        seg = segs[idx]
        at = seg.get("end") or seg.get("start") or 0.0
        lines.append("## 第 %d 段（接在 %s 之后）" % (idx + 1, fmt(at)))
        for text in bucket[idx]:
            lines.append("")
            lines.append(text.strip())
            count += 1
        lines.append("")
    (outdir / "原文附录.md").write_text("\n".join(lines), encoding="utf-8")
    return count


def plan_name(args, art, path=None):
    """dry-run 也要知道将来落哪个目录，不然没法判断「这次是不是在重跑旧稿」。

    从 out/<目录>/script.md 重跑时必须沿用原目录名，否则 warn_reheat 会去
    一个不存在的 work/ 目录找缓存，永远报「零成本」。
    """
    src = Path(path).expanduser().resolve() if path else None
    if (src and src.name == "script.md"
            and (src.parent / "index.md").is_file()
            and src.parent.parent.name == Path(args.out).name):
        return src.parent.name
    if args.date or args.slug:
        nm = "%s-%s" % (args.date or time.strftime("%Y-%m-%d"),
                        args.slug or slugify(art["title"]))
    else:
        nm = "%s-%s" % (time.strftime("%Y-%m-%d"), slugify(art["title"]))
    return nm + ("-试音" if args.preview else "")


def warn_reheat(args, art, segs, path=None):
    """旧稿重跑时，读法层改了文本，那些段就要重烧。--dry-run 得提前说，别闷头花钱。

    判断必须和 synth_all 的 _cache_hit 同口径，否则会报一个偏小的数字：
    文本没变但 provider 或音色变了，一样得全烧。
    """
    wd = Path(args.work) / plan_name(args, art, path)
    st = wd / "segments.json"
    if not st.is_file():
        return
    try:
        cache = {it["hash"]: it for it in json.loads(st.read_text(encoding="utf-8"))}
    except Exception:
        return
    tag = "%s|%s" % (provider()[0], normalize_voice(None))
    n = sum(1 for s in segs if not _cache_hit(cache.get(s["hash"]), tag, wd))
    if n:
        log("  ⚠ %d 段没有可用缓存（文本变了，或换了 provider/音色），重跑会重烧这 %d 段"
            % (n, n))


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
    if name == "doubao":
        fn = _tts_doubao
    elif name == "local":
        fn = _tts_local
    else:
        fn = _tts_deepseek
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


def _tts_local(text, voice):
    """本机 Kokoro 服务：OpenAI 兼容 /v1/audio/speech，返回 16-bit wav。

    服务端按文本自动选中文（v1.1-zh / zf_001）或英文（v1.0 / af_heart），
    长段在服务端再切，避开 Kokoro 510 音素的静默截断。
    """
    url = (CFG.get("LOCAL_TTS_URL") or "http://127.0.0.1:1919").rstrip("/") + "/v1/audio/speech"
    try:
        r = requests.post(url, json={"model": "kokoro", "input": text,
                                     "voice": voice or "auto",
                                     "response_format": "wav"},
                          timeout=(5, 600))
    except requests.ConnectionError:
        raise RuntimeError("本地 TTS 服务没起：%s\n"
                           "先跑 tools\\local-tts\\start.cmd（或设 LOCAL_TTS_URL）" % url)
    if r.status_code == 200:
        return r.content
    if r.status_code == 400:
        raise ValueError("400 %s" % r.text[:300])
    raise Retryable("%d %s" % (r.status_code, r.text[:300]))


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

def prompt_text(name):
    return (HERE / "prompts" / name).read_text(encoding="utf-8")


def llm(system, body, temperature=0.3):
    r = requests.post(LLM_API, timeout=300,
                      headers={"Authorization": "Bearer " + key()},
                      json={"model": CFG["LLM_MODEL"], "temperature": temperature,
                            "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": body}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def article_body(article):
    """article → 能直接喂给模型的 Markdown（小标题还原成 #）。"""
    return "\n\n".join(("# " if b["kind"] == "h" else "") + b["text"]
                       for b in article["blocks"])


def rewrite(article):
    """书面语 → 口播稿。这一步比换模型更影响听感，也便宜得多。"""
    return T.parse_article(llm(prompt_text("rewrite.md"), article_body(article)))


# ------------------------------------------------------------ 口述稿（分块）

def spoken_blocks(article):
    """原文 → 若干块，块内保持段落边界。

    块就是「一节」：遇到小标题一定开新块；没有小标题的短文按段落累计，
    攒到中位数再收。切点永远落在段落边界，段太长时按句末切。
    """
    chunks, cur, cur_len = [], [], 0

    def flush():
        nonlocal cur, cur_len
        if cur:
            chunks.append(cur)
            cur, cur_len = [], 0

    for b in article["blocks"]:
        parts = ([{"kind": b["kind"], "text": b["text"]}]
                 if len(b["text"]) <= SPOKEN_BLOCK_MAX
                 else T.chunk_paragraph(b["text"], SPOKEN_BLOCK_MAX))
        for part in parts:
            if part["kind"] == "h":
                flush()
            elif cur_len and cur_len + len(part["text"]) > SPOKEN_BLOCK_MAX:
                flush()
            cur.append(part)
            cur_len += len(part["text"])
            if cur_len >= SPOKEN_BLOCK_MID and part["kind"] == "p":
                flush()
    flush()
    return chunks


def outline_section(text, names):
    """取大纲里某个二级标题下的正文。取不到返回空串。"""
    out, on = [], False
    for ln in text.split("\n"):
        m = re.match(r"^##\s+(.+?)\s*$", ln)
        if m:
            on = any(n in m.group(1) for n in names)
            continue
        if on:
            out.append(ln)
    return "\n".join(out).strip()


def strip_section(text, name):
    """删掉某个二级标题连同它的正文。重跑时用来把上一次的「字数」擦掉，
    不然每跑一次大纲尾巴就多一段。"""
    out, on = [], False
    for ln in text.split("\n"):
        m = re.match(r"^##\s+(.+?)\s*$", ln)
        if m:
            on = name in m.group(1)
        if not on:
            out.append(ln)
    return "\n".join(out).rstrip()


def tail_paras(text, n=2):
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "\n\n".join(paras[-n:])


def spoken_done(d):
    """判做过没有看成品，不看目录 —— 跟正常流程同一条口径。"""
    d = Path(d)
    return d.is_dir() and (any(d.glob("*.m4a")) or (d / "index.md").is_file())


def is_rewritten_source(path, args):
    """输入是不是上一轮的口述稿/口播稿（改一个字重烧那种重跑）。
    这种源已经改写过，不能再喂给改写，不然二次改写把稿子越改越跑偏。"""
    src = Path(path).expanduser().resolve()
    return (src.name in ("script.md", "口述稿.md")
            and (src.parent / "index.md").is_file()
            and src.parent.parent.name == Path(args.out).name)


def spoken_outdir(args, art, path):
    """口述稿目录名用**原文**标题，并带 -口述 后缀。

    改过稿的标题会被模型动，拿它当目录名等于每跑一次换一个 work/，段缓存全废；
    后缀是跟朗读版隔开：两边段文本不同，混在一个 work/ 里会互相认错缓存。
    """
    if is_rewritten_source(path, args):
        return Path(path).expanduser().resolve().parent
    date = args.date or time.strftime("%Y-%m-%d")
    slug = args.slug or slugify(art["title"])
    name = "%s-%s-口述" % (date, slug)
    if args.preview:
        name += "-试音"
    return Path(args.out) / name


def spoken_qc(src_article, new_article):
    """落稿体检：不联网、不花钱，把「只有听完才发现」的毛病先摊在纸面上。

    查三样，都是这次真踩过的：
      章节数少了 —— 通勤时没法跳段；
      整段重复   —— 同一件事讲两遍，听众会以为卡带了；
      超长句     —— 一口气念不完，跟不住。
    返回 [(标签, 说明)]，说明里带段号，方便拿去 grep。
    """
    items = []
    heads_src = [b["text"] for b in src_article["blocks"] if b["kind"] == "h"]
    heads_new = [b["text"] for b in new_article["blocks"] if b["kind"] == "h"]
    if len(heads_new) < len(heads_src):
        items.append(("章节", "原文 %d 节，口述稿只有 %d 节，听众没法跳段"
                      % (len(heads_src), len(heads_new))))

    paras = [b["text"] for b in new_article["blocks"] if b["kind"] == "p"]
    grams = {}
    for i, p in enumerate(paras, 1):
        for j in range(0, max(1, len(p) - SPOKEN_QC_DUP + 1)):
            grams.setdefault(p[j:j + SPOKEN_QC_DUP], set()).add(i)
    groups = {}
    for g, idx in grams.items():
        if len(idx) > 1 and len(set(g)) >= 6:      # 「的了是在和」这种不算
            groups.setdefault(frozenset(idx), []).append(g)
    dup = sorted(((len(idx), sorted(idx), max(gs, key=len))
                  for idx, gs in groups.items()), reverse=True)
    accepted = []
    for n, idx, g in dup:                      # 同一处重复会产出多个重叠片段，只报最长的那个
        s = set(idx)
        if any(s <= a for _, a, _ in accepted):
            continue
        accepted.append((n, s, g))
        if len(accepted) == 3:
            break
    for n, idx, g in accepted:
        items.append(("重复", "第 %s 段在讲同一件事：「%s…」"
                      % ("、".join(str(i) for i in sorted(idx)), g)))

    long_s = []
    for i, p in enumerate(paras, 1):
        for s in re.split(r"(?<=[。！？!?])", p):
            s = s.strip()
            if len(s) > SPOKEN_QC_LONG:
                long_s.append((len(s), i, s))
    long_s.sort(reverse=True)
    for n, i, s in long_s[:3]:
        items.append(("长句", "第 %d 段有一句 %d 字，一口气念不完：「%s…」"
                      % (i, n, s[:26])))
    return items


def write_qc(outdir, items, head_line):
    lines = ["# 落稿体检", "",
             "自动检查，不联网、不花钱。专查「只有听完才发现」的毛病："
             "章节够不够、有没有把同一件事讲两遍、有没有念不完的长句。", "",
             head_line, ""]
    if items:
        lines += ["## 发现的问题", ""]
        for k, v in items:
            lines.append("- **%s** %s" % (k, v))
    else:
        lines.append("没发现问题。")
    lines.append("")
    (Path(outdir) / "体检.md").write_text("\n".join(lines), encoding="utf-8")


def spoken_pass(article, args, outdir, path):
    """两遍改写：先出大纲（≤800 字），再按大纲分块落地。

    返回 (新 article, 是否停在大纲)。停在大纲时 article 为 None。
    """
    src_chars = sum(len(b["text"]) for b in article["blocks"])
    if src_chars > SPOKEN_MAX_CHARS:
        raise RuntimeError(
            "原文 %d 字，超过口述稿上限 %d 字；先切成两篇再跑"
            "（整篇一次发会静默截断，分块又没有全局视野）" % (src_chars, SPOKEN_MAX_CHARS))

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    outline_p = outdir / "大纲.md"

    if args.outline:
        outline = strip_section(Path(args.outline).read_text(encoding="utf-8"), "字数")
        log("  大纲：复用 %s" % args.outline)
        if Path(args.outline).resolve() != outline_p.resolve():
            outline_p.write_text(outline + "\n", encoding="utf-8")
    else:
        log("  大纲中（%s · %d 字）…" % (CFG["LLM_MODEL"], src_chars))
        outline = llm(prompt_text("spoken-outline.md"), article_body(article)).strip()
        outline_p.write_text(outline + "\n", encoding="utf-8")
        log("  → %s" % outline_p.name)

    dels = outline_section(outline, ("删改清单",))
    (outdir / "删改清单.md").write_text(
        "# 删改清单\n\n"
        "口述稿相对原文删掉和合并了什么。不满意就改 `大纲.md` 再跑：\n\n"
        "    python3 cast.py --spoken --outline %s\n\n" % outline_p
        + (dels or "（大纲里没写删改清单）") + "\n", encoding="utf-8")

    if args.stop_at_outline:
        log("  --stop-at-outline：只出大纲，不合成（零 TTS 费用）")
        log("    改完再跑：python3 cast.py --spoken --outline %s %s" % (outline_p, path))
        return None, True

    chunks = spoken_blocks(article)
    log("  落地：%d 块（%s）" % (len(chunks), "、".join(
        "%d 字" % sum(len(b["text"]) for b in c) for c in chunks)))
    outs, prev = [], ""
    for i, chunk in enumerate(chunks, 1):
        head = next((b["text"] for b in chunk if b["kind"] == "h"), "")
        cchars = sum(len(b["text"]) for b in chunk)
        # 标题和目标字数都必须随每一块发过去：标题在块里找不到（parse 时已经抽走），
        # 不给它就会拿第一节小标题顶上去，整篇就丢了名字，章节也只剩一个。
        user = ("【文章标题】《%s》\n\n【大纲】\n%s\n\n【上一块结尾】\n%s\n\n"
                "【这一块：第 %d/%d 块%s，原文 %d 字，目标 %d 到 %d 字】\n%s"
                % (article["title"], outline,
                   prev or "（这是第一块，没有上一块）",
                   i, len(chunks),
                   "，原小标题「%s」" % head if head else "",
                   cchars,
                   int(cchars * SPOKEN_TARGET[0]), int(cchars * SPOKEN_TARGET[1]),
                   "\n\n".join(("# " if b["kind"] == "h" else "") + b["text"]
                               for b in chunk)))
        txt = llm(prompt_text("spoken-block.md"), user, temperature=0.4).strip()
        outs.append(txt)
        prev = tail_paras(txt)
        log("    第 %d/%d 块 → %d 字" % (i, len(chunks), len(txt)))

    raw = "\n\n".join(outs) + "\n"
    (outdir / "口述稿.md").write_text(raw, encoding="utf-8")
    new = T.parse_article(raw)

    new_chars = sum(len(b["text"]) for b in new["blocks"])
    ratio = new_chars / float(src_chars or 1)
    note = ("原文 %d 字 → 口述稿 %d 字（%.0f%%）" % (src_chars, new_chars, ratio * 100))
    log("  " + note)
    if ratio < SPOKEN_RATIO_FLOOR:
        note += ("\n\n> ⚠ 删得偏多：只到原文的 %.0f%%。看一眼 `删改清单.md`，"
                 "觉得砍多了就改 `大纲.md` 重跑。" % (ratio * 100))
        log("  ⚠ 删得偏多（%.0f%%，低于 %.0f%%）" % (ratio * 100, SPOKEN_RATIO_FLOOR * 100))
    elif ratio > SPOKEN_RATIO_CEIL:
        note += ("\n\n> ⚠ 删得不够：还留着原文的 %.0f%%，基本只是把句子改顺了。"
                 "想要更像口述，就在 `大纲.md` 里把「合并或删」写具体一点，再跑一遍落地。"
                 % (ratio * 100))
        log("  ⚠ 删得不够（%.0f%%，高于 %.0f%%）：这份更像朗读稿"
            % (ratio * 100, SPOKEN_RATIO_CEIL * 100))
    with outline_p.open("w", encoding="utf-8") as f:
        f.write(strip_section(outline, "字数") + "\n\n## 字数\n\n" + note + "\n")

    items = spoken_qc(article, new)
    for k, v in items:
        log("  ⚠ %s：%s" % (k, v))
    write_qc(outdir, items, note.split("\n")[0])
    return new, False


# ------------------------------------------------------------------ 主流程

def archive_source(args, path, reuse_dir=False):
    """跑完把 inbox 里的原件搬进 archive/，跟 asr-notes 一个规矩。

    只搬 inbox 里的：从 out/…/script.md 重跑、或者用户直接点别处的文件，都不动。
    试音（--preview）也不搬——那不是一集的成品。
    归档失败不当成失败：文件留在原地，日志说一声，产出照样是好的。
    """
    if getattr(args, "no_archive", False) or getattr(args, "preview", None) or reuse_dir:
        return None
    src = Path(path).expanduser().resolve()
    inbox = Path(getattr(args, "inbox", "inbox")).expanduser().resolve()
    if inbox not in src.parents:
        return None                       # 不是从 inbox 投进来的，别替人搬家
    dest_dir = Path(getattr(args, "archive", "./archive"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():                     # 同一篇重跑过，别覆盖上一份
        dest = dest_dir / ("%s-%s%s" % (src.stem, time.strftime("%H%M%S"), src.suffix))
    try:
        src.replace(dest)
    except OSError:                       # 跨设备 rename 会 EXDEV
        try:
            shutil.move(str(src), str(dest))
        except OSError as e:
            log("  ⚠ 原件没能归档（还在原处）：%s" % e)
            return None
    log("  📦 原件 → %s" % dest)
    return dest


def process(path, args):
    raw = Path(path).read_text(encoding="utf-8")
    art = T.parse_article(raw)
    log("▶ %s · %d 段" % (art["title"], len(art["blocks"])))

    # 口述稿要把大纲、删改清单落在成品目录里，所以目录名得在花钱之前定下来，
    # 也才能在「已经做过」的时候提前收手，不白烧一次 LLM。
    spoken_out = spoken_outdir(args, art, path) if args.spoken else None
    if spoken_out is not None and not args.dry_run and not args.force and not args.resynth \
            and not args.preview and spoken_done(spoken_out):
        log("  已存在，跳过：%s（--force 重做产物，--resynth 全部重烧）" % spoken_out)
        archive_source(args, path, is_rewritten_source(path, args))
        return spoken_out

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
    elif args.spoken:
        if args.dry_run:
            log("  --dry-run：跳过口述稿（大纲和落地各要 LLM 调用），下面按原文估算")
        elif is_rewritten_source(path, args):
            # 从 out/xxx-口述/script.md 重跑：稿子已经改写过一遍，别再改第二遍
            log("  输入已经是改过稿的 %s，跳过改写直接合成" % Path(path).name)
        else:
            art2, stopped = spoken_pass(art, args, spoken_out, path)
            if stopped:
                return spoken_out
            art = art2

    lost = art.get("dropped") or []
    if verbalize_on(args):
        segs = split_plan(art)
        moved = verbalize_segs(segs)
        finish_plan(segs)
        if moved:
            log("  读法层：摘出 %d 处念不出来的内容 → 原文附录" % len(moved))
    else:
        segs, moved = build_plan(art), []
        log("  读法层已关闭（--no-verbalize 或 CAST_VERBALIZE=0）")
    if lost:
        log("  解析期摘出 %d 处整块内容（代码块/表格）→ 原文附录" % len(lost))

    print(plan_report(segs))
    if args.dry_run:
        if args.verbose:
            for s in segs:
                print("  %-3d %s %4d字 %5d计费  %s"
                      % (s["i"], "H" if s["kind"] == "h" else " ",
                         s["chars"], s["billed"], s["text"][:28]))
        warn_reheat(args, art, segs, path)
        return None

    # 目录名就是这一期的身份。标题改一个字就换目录、换 work/、把整篇重新烧一遍，
    # 所以「从已有的 out/<目录>/script.md 重跑」必须沿用原目录名，不能重新 slugify。
    src = Path(path).expanduser().resolve()
    reuse_dir = (src.name in ("script.md", "口述稿.md")
                 and (src.parent / "index.md").is_file()
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
    if spoken_out is not None:
        # 口述稿的目录名在花钱之前就定了（用原文标题 + -口述 后缀），这里只是沿用
        name, outdir = spoken_out.name, spoken_out
        date = args.date or name[:10]
    # 判「做过没有」看成品，不看目录：目录在建完之后、合成之前被打断（切后台被杀、
    # 手滑 Ctrl-C）会留下一个空壳，只看 exists() 的话这一期就被永远跳过，只能靠 --force 救。
    done = outdir.is_dir() and (any(outdir.glob("*.m4a")) or (outdir / "index.md").is_file())
    if done and not (args.force or args.resynth or args.preview):
        log("  已存在，跳过：%s（--force 重做产物，--resynth 全部重烧）" % outdir)
        archive_source(args, path, reuse_dir)
        return outdir
    if outdir.exists() and not done:
        if args.spoken and (outdir / "大纲.md").is_file():
            log("  继续上次：大纲和口述稿已在，这次补成品：%s" % outdir)
        else:
            log("  目录在、成品不在（上次中途断了），这次重做：%s" % outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    workdir = Path(args.work) / name
    workdir.mkdir(parents=True, exist_ok=True)

    voice = normalize_voice(args.voice)
    _, p = provider()           # 注意：别叫 name，上面的 name 是产出目录名
    spent, skipped = synth_all(segs, workdir, voice, resynth=args.resynth)

    # 先算时间轴和字幕，再拼装：字幕要内嵌的话得在同一趟 ffmpeg 里完成
    total, chapters = build_timeline(segs)
    if moved or lost:
        write_appendix(outdir, moved, segs, lost)
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
    archive_source(args, path, reuse_dir)
    return outdir


def check():
    ok = True
    name, p = provider()
    print("Provider : %s（%s）" % (name, p["label"]))
    if name == "local":
        url = (CFG.get("LOCAL_TTS_URL") or "http://127.0.0.1:1919").rstrip("/")
        try:
            h = requests.get(url + "/health", timeout=3).json()
            print("服务     : %s 已连上（zh=%s / en=%s）"
                  % (url, h.get("zh_voice"), h.get("en_voice")))
        except Exception:
            print("服务     : ✗ 连不上 %s（先跑 tools\\local-tts\\start.cmd）" % url)
            ok = False
    else:
        if name == "doubao":
            k, label = CFG.get("DOUBAO_API_KEY", ""), "DOUBAO_API_KEY"
        else:
            k, label = CFG.get("DEEPSEEK_API_KEY", ""), "DEEPSEEK_API_KEY"
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
    p.add_argument("--spoken", action="store_true",
                   help="改写成口述稿（重排结构、允许删枝节），然后照常合成")
    p.add_argument("--stop-at-outline", action="store_true",
                   help="配合 --spoken：只出大纲.md，不合成（零 TTS 费用）")
    p.add_argument("--outline",
                   help="配合 --spoken：复用这份大纲，跳过出大纲那一次调用")
    p.add_argument("--archive", default="./archive", help="跑完把原件挪到这里")
    p.add_argument("--no-archive", action="store_true", help="不移动源文件")
    p.add_argument("--dry-run", action="store_true", help="只出计划，不花钱")
    p.add_argument("--force", action="store_true",
                   help="覆盖已有产出目录（没改过的段仍复用，不重复计费）")
    p.add_argument("--resynth", action="store_true",
                   help="忽略缓存，所有段重新合成（会重新计费，改稿调试时才用）")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--no-normalize", action="store_true", help="不做 -16 LUFS 归一化")
    p.add_argument("--no-verbalize", action="store_true",
                   help="关掉读法层（符号不转换、标记不清理、不做原文附录）")
    p.add_argument("--embed-sub", action="store_true", help="把字幕轨也塞进 m4a")
    p.add_argument("--check", action="store_true")
    p.add_argument("--all", action="store_true", help="扫 --inbox 时连已做过的也重跑")
    args = p.parse_args(argv)

    if args.spoken and args.rewrite:
        p.error("--spoken 和 --rewrite 只能选一个：前者重排成口述稿，后者只改句子")
    if (args.outline or args.stop_at_outline) and not args.spoken:
        p.error("--outline / --stop-at-outline 只在 --spoken 下有意义")

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
