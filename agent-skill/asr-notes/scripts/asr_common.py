#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日志、时间格式、文件名 slug —— asr.py / asr_render.py / asr_quotes.py 共用。

为什么单拎出来：这几行原来在 asr.py 里，拆模块后如果各抄一份，
格式化就会各说各话（日志前缀差一个空格、mm:ss 进位不一致），
而这种差别在冒烟测试里表现为"看起来都对，就是数字差一点"。
"""

import re
import time


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def fmt(sec):
    s = int(sec)
    return ("%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)
            if s >= 3600 else "%02d:%02d" % (s // 60, s % 60))


def slugify(s, limit=64):
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", s)
    s = re.sub(r"[-\u2013\u2014\s]+", "-", s.strip())
    s = s.strip("-.")
    return s[:limit] or "voice"
