# 接口细节与已踩过的坑

DeepSeek 在 2026-11-13 开放了独立 ASR 接口（和 Search / Fetch / OCR / TTS 一起），
转写和整理现在共用一个 Key、一个账单。

## ASR：POST /v1/tools/asr

```
curl https://api.deepseek.com/v1/tools/asr \
  -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
  -F file=@speech.wav
```

| 限制 | 值 |
|---|---|
| 格式 | 16-bit PCM WAV 或 Ogg Opus（按文件内容判断，不看扩展名） |
| 采样率 | 8000 / 16000 / 22050 / 24000 / 32000 / 44100 / 48000 Hz |
| 声道 | 单声道或立体声；**立体声只识别左声道** |
| 时长 | ≤ 120 秒 |
| 大小 | ≤ 8 MiB |
| 上传 | 10 分钟内完成 |
| 并发 | 账号内最多 50 个请求在跑 |

响应只有三个字段：

```json
{"request_id":"...","text":"...","duration_ms":3200}
```

**没有句子级时间戳，也没有分段。** 想要逐句对齐只能靠切片起点近似，或者换引擎。
语言也不传参，接口自己检测（zh / yue / en / ja / ko / de / fr / es / pt / it / ru / ar / hi / id / vi / th / tr / ms / fil / pl / hu）。

错误码：

| 码 | 含义 |
|---|---|
| 400 | 不是 16-bit PCM WAV / Ogg Opus，或缺 file 字段 |
| 408 | 上传超 10 分钟 |
| 413 | 超过 8 MiB |
| 429 | 并发超限 |
| 503 | 服务繁忙，重试 |
| 504 | 识别超时 |

计费：按音频时长到秒，不足一秒按一秒。刊例 $0.116/小时，限时 5 折 $0.058/小时
（人民币刊例 ¥0.78，折后 ¥0.39）。

## 切片策略

接口 120 秒上限，脚本按 110 秒切，留 10 秒余量：

1. `silencedetect=noise=-35dB:d=0.6` 扫一遍静音区间
2. 每段目标 110 秒，在 [60s, 110s] 里找静音中点，挑最靠后的那个当切点
3. 没有合适静音就硬切在 110 秒

切成 Ogg Opus 24kbps 单声道：110 秒约 330 KB，远低于 8 MiB。
23 分钟音频切成 13 片，总上传约 4 MB。

## 三个真实踩过的坑

**1. 封面图会让容器不合法。**
从播客/视频里抽出来的 m4a 常带一张 mjpeg 封面，ffmpeg 转 ogg 时会把封面也塞进容器，
接口报 `400 unsupported audio container: expected Ogg Opus`。加 `-vn` 解决。

**2. Ogg Opus 必须是 48 kHz。**
libopus 内部固定 48 kHz，写成 `-ar 16000` 会产出非法流。要么 48k opus，要么 16k WAV。

**3. 切点断句会丢字。**
硬切在句子中间，ASR 会把半个词识别成另一个词。静音检测能解决大部分情况，
剩下的一两处交给清洗 prompt 合并，别为它调参调到天亮。

## 整理：POST /v1/chat/completions

```
POST https://api.deepseek.com/v1/chat/completions
Authorization: Bearer $DEEPSEEK_API_KEY
{"model":"deepseek-chat","messages":[...],"temperature":0.3,"max_tokens":6000}
```

- 账号下可见模型：`deepseek-chat`（兼容别名）、`deepseek-flash`（V4.1，1M 上下文）、`deepseek-v4-pro`
- `max_tokens` 给 6000，默认 4096 容易把长稿截断
- 温度 0.3：整理要稳，不要发散
- 23 分钟口述约 8000 字，单次调用足够，不用分块
