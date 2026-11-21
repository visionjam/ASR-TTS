#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日志、时间格式、文件名、计费字符 —— cast.py / cast_text.py / cast_render.py 共用。

为什么单拎出来：mm:ss 的进位口径一旦各抄一份，字幕、章节、文稿三处就会差一秒，
而这种差别在冒烟测试里表现为「看着都对，就是时间对不上」。
"""

import hashlib
import re
import time
import unicodedata

# 汉字、全角标点、中文兼容区 —— 判断「算 2 个计费字符」用
_CJK = re.compile(
    "[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff"
    "\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]"
)


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def fmt(sec):
    """给人看的时间：03:07，超过一小时才 1:03:07"""
    s = int(round(sec))
    if s >= 3600:
        return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)
    return "%02d:%02d" % (s // 60, s % 60)


def srt_ts(sec):
    """字幕时间戳：00:03:07,200"""
    ms = int(round(sec * 1000))
    return "%02d:%02d:%02d,%03d" % (
        ms // 3600000, ms % 3600000 // 60000, ms % 60000 // 1000, ms % 1000)


def ms(sec):
    return int(round(sec * 1000))


def slugify(s, limit=60):
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", s)
    s = re.sub(r"[-\u2013\u2014\s]+", "-", s.strip())
    return s.strip("-.")[:limit] or "cast"


def digest(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def is_cjk(ch):
    return bool(_CJK.match(ch))


def billed_chars(text, cjk=2):
    """计费字符估算。`cjk` 是「一个汉字算几个计费字符」。

    DeepSeek 和豆包的口径不同，这是比价时最容易翻车的一条：
      deepseek 汉字算 2 个计费字符（官方口径）
      doubao   汉字算 1 个计费字符（官方计费说明明写）
    同一段文字，两家的 billed 差一倍，所以这个参数必须跟着 provider 走，
    不能写死——写死的话 plan_report 报出来的价会正好差一倍。

    这是**估算**，用来在下单前估钱；实际以账单为准。
    """
    return sum(cjk if is_cjk(c) else 1 for c in text)


def visual_len(s):
    """排版宽度：汉字算 1，半角字符算 0.5。

    字幕断行、句子切分都按它算，不能按 len()——「iPhone 15 Pro」和
    「十五个字的中文」字节数差一倍，显示宽度却相反。
    """
    return sum(1.0 if is_cjk(c) else 0.5 for c in s if not c.isspace())


def secs_per_char(chars_per_min=250.0):
    """预估朗读速度：默认 250 字/分钟（有声书常用 240–280）。"""
    return 60.0 / chars_per_min
