# 豆包语音合成 2.0 接口细节与踩坑

口径核对于 2026-11-18。价格和上限会变，以官网为准；这里记的是「文档没写清、
但会让人卡住半天」的那些。

## 接口

```
POST https://openspeech.bytedance.com/api/v3/tts/unidirectional
X-Api-Key: <新版控制台的 API Key，一串 UUID>
X-Api-Resource-Id: seed-tts-2.0
X-Api-Request-Id: <uuid，每次都要换>
Content-Type: application/json

{"user": {"uid": "tts-cast"},
 "req_params": {"text": "...", "speaker": "zh_male_liufei_uranus_bigtts",
                "audio_params": {"format": "wav", "sample_rate": 24000,
                                 "speech_rate": 0}}}
```

- `format` 支持 `mp3` / `pcm` / `ogg_opus` / `wav`。文档说流式推荐 pcm、不建议 wav，
  但 **wav 能用**，而且保住 RIFF 头，`probe_duration()` 一行都不用改。
- `speech_rate` 取值 `[-50, 100]`，0 是默认，100 = 2 倍速。
- `enable_subtitle: true` 会让响应里带 `words[]` 字级时间戳（仅 2.0 音色、中英文）。

## 响应形态：不是裸音频

这点跟 DeepSeek 完全不同。返回的是一串 JSON 对象（可能带 `data:` 前缀、可能被 TCP 切碎）：

```
{"code":0,"message":"","data":"UklGR..."}          ← base64 音频块，若干条
{"code":0,...,"sentence":{"text":"…","words":[]}}   ← 字幕挂点
{"code":20000000,"message":"OK","data":null}        ← 收尾
```

所以「`b"".join(iter_content())` → `startswith(b"RIFF")`」这一套会直接挂，
拼出来是 JSON 文本。必须先切对象、再 base64 解码、再拼字节（见 `_doubao_stream`）。

`words` 现在空着。将来要把 srt 从「段内插值」升级成字级时间轴，就在这个对象里拿，
不用再发一次请求，也不用另买字幕打轴服务。

## 计费

3 元/万字符，**一个汉字算 1 个字符**（DeepSeek 是 2 个，所以两边单价看着差一倍，
折算到「元/万汉字」其实一样）。资源包：2000 万字 2.7、2 亿字 2.4、20 亿字 2.1。

音色槽位 138 元/个（预付费，含 15 次训练；后付费一次性 138）。复刻音色配
`X-Api-Resource-Id: seed-icl-2.0`，官方 2.0 音色配 `seed-tts-2.0`——配错的表现是
HTTP 200 + `55000000 resource ID is mismatched`，不是 4xx，很容易误判成「没额度」。

## 错误语义

| 现象 | 含义 | 处理 |
|---|---|---|
| 401 / 403 | Key 不对，或服务没开通 | **别当「跳过该段」**，否则整篇会静悄悄变成空音频 |
| 400 | 文本不过审 | 跳过该段，不扣钱 |
| 429 | 频率超限 | 退避重试 |
| 200 + code≠0 | 看 message | 有 data 就当正常，没 data 当文本问题 |

## 实测（2026-11-18，手机 proot）

| 字数 | 音频 | 耗时 |
|---|---|---|
| 315（4 段） | 01:10 | 26 秒 |
| 600（单段） | 01:05 | 173 秒（首测，后端冷启动） |

- 600 字一段能一次合成完（约 5MB wav），不用像 DeepSeek 那样担心 1 万计费字符上限。
- 默认语速约 **300 字/分**，比有声书的 250 偏快；`speech_rate: -20` 约 266 字/分，更耐听。
- 流式 wav 头里长度字段是 `0xFFFFFFFF`，ffmpeg 能正确读出时长；但这个 wav 别直接发给
  播放器，还是过一遍 m4a 转码。
