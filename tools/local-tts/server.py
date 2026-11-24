#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 Kokoro TTS 服务，OpenAI 兼容 /v1/audio/speech。

分工（按用户试听结论定的）：
  中文 / 中英混排 → Kokoro-82M-v1.1-zh · zf_001，en_callable 把英文词转成英文音素
  纯英文          → Kokoro-82M（v1.0）· af_heart

长文本自动切段：中文段 ≤150 字、英文段 ≤450 字符，避开 Kokoro 510 音素的静默截断。

    python server.py --port 1919
    curl -X POST http://127.0.0.1:1919/v1/audio/speech \
         -d '{"model":"kokoro","input":"你好，世界。","voice":"auto","response_format":"wav"}' \
         -o out.wav

voice 取值：
  auto（默认，按文本自动选） / zh / en / zf_001 / af_heart
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import soundfile as sf

SR = 24000
ZH_LIMIT = 150          # 汉字/字符数，对应约 465 音素
EN_LIMIT = 450          # 英文字符数，对应约 460 音素
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
SENT_RE = re.compile(r"[^。！？!?；;.\n]+[。！？!?；;.\n]?", re.S)

DEFAULT_ZH_VOICE = os.environ.get("LOCAL_TTS_VOICE_ZH", "zf_001")
DEFAULT_EN_VOICE = os.environ.get("LOCAL_TTS_VOICE_EN", "af_heart")
# 语速：默认 1.0。设成 auto 用官方 make_zh.py 的「长句减速」经验曲线。
SPEED = os.environ.get("LOCAL_TTS_SPEED", "1.0")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def power_hint():
    """Windows「平衡」方案会把 CPU 压到约 1x 实时；切高性能能快 3 倍。"""
    if os.name != "nt":
        return
    try:
        out = subprocess.run(["powercfg", "/getactivescheme"],
                             capture_output=True, timeout=5).stdout.decode("mbcs", "ignore")
        if "381b4222" in out:                       # SCHEME_BALANCED
            log("WARN: power plan = Balanced, CPU is throttled (RTF ~1.0). "
                "Switch to High performance for ~3x faster batch runs.")
    except Exception:
        pass


def has_cjk(text):
    return CJK_RE.search(text) is not None


# Kokoro 的中文 G2P 会把一批符号直接丢掉（音素里变成 ❓），和豆包不一样。
# 这里只做「读法」替换，不改句子结构；在中文/混排文本进模型前过一遍。
# 顺序有讲究：日期、单位、负号要在通用符号之前处理。
KOKORO_FIXES = [
    (re.compile(r"(\d{4})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})"), r"\1年\2月\3日"),
    (re.compile(r"#\s*(\d+)"), r"第\1"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*‰"), r"千分之\1"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:km/h|km／h|kph)", re.I), r"\1公里每小时"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:km²|km2)"), r"\1平方公里"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:m²|m2|㎡)"), r"\1平方米"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:m³|m3)"), r"\1立方米"),
    (re.compile(r"°\s*C"), "摄氏度"),
    (re.compile(r"°"), "度"),
    (re.compile(r"(?<![\w])-(?=\d)"), "负"),
    (re.compile(r"±"), "正负"),
    (re.compile(r"\s*(?:→|⇒|->)\s*"), " 到 "),
    (re.compile(r"≥"), "大于等于"),
    (re.compile(r"≤"), "小于等于"),
    (re.compile(r"\s*&\s*"), " 和 "),
    (re.compile(r"\s*×\s*"), " 乘 "),
    (re.compile(r"(?<=\d)\s*[xX]\s*(?=\d)"), " 乘 "),
    (re.compile(r"\s*[~～]\s*"), " 到 "),
    (re.compile(r"(?<=\w)\s*\+\s*(?=\w)"), " 加 "),
    (re.compile(r"(?<=\w)\s*=\s*(?=\w)"), " 等于 "),
    (re.compile(r"(?<=\w)\s*<\s*(?=\w)"), " 小于 "),
    (re.compile(r"(?<=\w)\s*>\s*(?=\w)"), " 大于 "),
    (re.compile(r"·"), ""),
    (re.compile(r"#(?=[\u4e00-\u9fff])"), "井号"),
    (re.compile(r"@(?=[\u4e00-\u9fff])"), "艾特"),
]


def normalize_for_kokoro(text):
    """把 Kokoro 会丢掉的符号换成读法。只对中文/混排调用。"""
    for pat, rep in KOKORO_FIXES:
        text = pat.sub(rep, text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def split_sentences(text):
    out = []
    for m in SENT_RE.finditer(text):
        s = m.group(0)
        if s and s.strip():
            out.append(s)
    return out or [text]


def chunk_text(text, limit):
    """按句末切，累积到 limit；单句超长再硬切。"""
    out, cur = [], ""
    for s in split_sentences(text):
        while len(s) > limit:
            out.append(s[:limit])
            s = s[limit:]
        if len(cur) + len(s) > limit and cur:
            out.append(cur)
            cur = ""
        cur += s
    if cur.strip():
        out.append(cur)
    return out


def speed_callable(n_ps):
    """官方 make_zh.py 的经验曲线：长音素序列放慢，短句略快。"""
    speed = 0.8
    if n_ps <= 83:
        speed = 1.0
    elif n_ps < 183:
        speed = 1 - (n_ps - 83) / 500
    return speed * 1.1


class Synth:
    def __init__(self, zh_voice=DEFAULT_ZH_VOICE, en_voice=DEFAULT_EN_VOICE, device="auto"):
        from kokoro import KPipeline
        import torch
        self.lock = threading.Lock()
        self.zh_voice = zh_voice
        self.en_voice = en_voice
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("指定了 --device cuda，但 torch.cuda.is_available() 是 False")
        self.device = device
        if device == "cuda":
            log("device=cuda (%s, %.1f GB VRAM)" % (
                torch.cuda.get_device_name(0),
                torch.cuda.get_device_properties(0).total_memory / 1024 ** 3))
        else:
            log("device=cpu (%d threads)" % torch.get_num_threads())
        t0 = time.time()
        # 英文 G2P（只做音素，不加载模型）
        self.en_g2p = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M-v1.1-zh", model=False)
        # 中文：带 en_callable，混排里的英文词才不会被丢成 ❓
        self.zh = KPipeline(lang_code="z", repo_id="hexgrad/Kokoro-82M-v1.1-zh",
                            en_callable=self._en_phonemes, device=device)
        # 英文：v1.0，用户选定的 af_heart
        self.en = KPipeline(lang_code="a", device=device)
        log("models ready in %.1fs (zh=%s / en=%s / device=%s)"
            % (time.time() - t0, zh_voice, en_voice, device))
        # 预热：第一句会触发 torch 编译/缓存，先烧掉
        self._gen("你好。", "zh")
        self._gen("Warm up.", "en")
        log("warm-up done")
        if device == "cpu":
            power_hint()

    def _en_phonemes(self, text):
        return next(self.en_g2p(text)).phonemes

    def _gen(self, text, lang, voice=None):
        if lang == "en":
            speed = 1.0
            chunks = [c[2] for c in self.en(text, voice=voice or self.en_voice, speed=speed)]
        else:
            sp = speed_callable if SPEED == "auto" else float(SPEED)
            chunks = [c[2] for c in self.zh(text, voice=voice or self.zh_voice, speed=sp)]
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    def synth(self, text, voice="auto"):
        text = (text or "").strip()
        if not text:
            raise ValueError("空文本")
        if voice in ("zh", "zf_001", DEFAULT_ZH_VOICE):
            lang = "zh"
        elif voice in ("en", "af_heart", DEFAULT_EN_VOICE):
            lang = "en"
        else:
            lang = "zh" if has_cjk(text) else "en"
        if lang == "zh":
            text = normalize_for_kokoro(text)
        limit = ZH_LIMIT if lang == "zh" else EN_LIMIT
        pieces = chunk_text(text, limit)
        with self.lock:
            audio = np.concatenate([self._gen(p, lang) for p in pieces]) if len(pieces) > 1 \
                else self._gen(pieces[0], lang)
        return audio, lang, len(pieces)


SYNTH = None


class Handler(BaseHTTPRequestHandler):
    server_version = "local-kokoro/1.0"

    def log_message(self, fmt, *args):
        log("%s %s" % (self.address_string(), fmt % args))

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/v1/health"):
            self._json(200, {"ok": True, "zh_voice": SYNTH.zh_voice,
                             "en_voice": SYNTH.en_voice, "sample_rate": SR})
        else:
            self._json(404, {"detail": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        if path not in ("/v1/audio/speech", "/tts"):
            self._json(404, {"detail": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            text = body.get("input") or body.get("text") or ""
            voice = (body.get("voice") or "auto").strip()
            t0 = time.time()
            audio, lang, nseg = SYNTH.synth(text, voice)
            buf = io.BytesIO()
            sf.write(buf, audio, SR, format="WAV", subtype="PCM_16")
            wav = buf.getvalue()
            dur = len(audio) / SR
            log("tts lang=%s chars=%d segs=%d audio=%.2fs gen=%.2fs rtf=%.3f"
                % (lang, len(text), nseg, dur, time.time() - t0,
                   (time.time() - t0) / dur if dur else 0))
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.send_header("X-TTS-Language", lang)
            self.end_headers()
            self.wfile.write(wav)
        except ValueError as e:
            self._json(400, {"detail": str(e)[:300]})
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"detail": "%s: %s" % (type(e).__name__, str(e)[:300])})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("LOCAL_TTS_PORT", 1919)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--zh-voice", default=DEFAULT_ZH_VOICE)
    ap.add_argument("--en-voice", default=DEFAULT_EN_VOICE)
    ap.add_argument("--device", choices=("auto", "cpu", "cuda"),
                    default=os.environ.get("LOCAL_TTS_DEVICE", "auto"),
                    help="auto: 有 CUDA 就用，否则 CPU")
    args = ap.parse_args()

    global SYNTH
    SYNTH = Synth(args.zh_voice, args.en_voice, args.device)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    log("listening on http://%s:%d" % (args.host, args.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("bye")


if __name__ == "__main__":
    main()
