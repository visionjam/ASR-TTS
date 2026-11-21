# DeepSeek TTS 接口细节与踩坑

口径核对于 2026-11-18。价格和上限会变，以官网为准；这里记的是「文档没写清、
但会让人卡住半天」的那些。

## 接口

```
POST https://api.deepseek.com/v1/tools/tts
Authorization: Bearer <DEEPSEEK_API_KEY>
Content-Type: application/json

{"text": "...", "voice_id": "tide", "format": "wav", "filter_markdown": true}
```

- `voice_id`：只有 `stella`（海星，女声）和 `tide`（暗潮，男声）。
- `format`：`wav`（默认，audio/wav）或 `pcm`（24 kHz / 16 bit / 单声道裸流）。
- `filter_markdown`：默认 true，会去掉 Markdown 标记。
- 音频**边合成边返回**，所以要 `stream=True` 边收边写；整块 `resp.content`
  在手机上遇到 30 分钟音频会占 80MB 内存。
- WebSocket 版 `wss://api.deepseek.com/v1/tools/tts` 支持分段送文本，
  但单次会话仍是 30 分钟 / 10,000 计费字符。

## 硬限制

| 项 | 值 | 后果 |
|---|---|---|
| 单次计费字符 | 10,000 | 超了直接报错，得自己切 |
| 单次音频时长 | 30 分钟 | 同上 |
| 请求体 | 64 KiB | 长文本先切 |
| 并发 | 有上限 | 429 时退避重试 |
| 语速 | **不支持** | 没有参数，只能改稿 |
| 断点续传 | **不支持** | 中断即失败，从头来 |

## 计费

3 元/万计费字符，限时五折 **1.5 元/万**（2026 年内）。

**汉字算 2 个计费字符**，半角字符算 1 个。所以一篇 4000 汉字 ≈ 8000 计费字符
≈ 1.2 元。估钱一律用 `cast_common.billed_chars()`，别用 `len()`。

只对 `text` 字段计费，`voice_id` / `format` 不计费。

## 错误码

| 码 | 原因 | 怎么处理 |
|---|---|---|
| 400 | 没有可朗读文本，或**内容审核不过**（`Content Exists Risk`） | 跳过该段，记日志，继续 |
| 429 | 并发超限 | 退避重试（1s / 2s / 4s） |
| 503 | 服务繁忙 | 同上 |
| 504 | 合成超时 | 同上；连续失败就切小段 |

**关键一条**：文档明确说「如果音频已经开始返回后合成失败，响应会中途断开，
请把提前结束的响应视为失败」。所以要校验返回字节以 `RIFF` 开头，
并且用 ffmpeg 探一遍时长——别只看 HTTP 200。

## 和 asr-notes 的关系

同一个 Key、同一份账单、同一套配置读取顺序。两条线共用库根那一份 `config.env`
（不再单独建 `cast/` 目录），所以壳和 `config.env` 永远在同一层。

## 为什么最后出 m4a 而不是 wav

30 分钟 wav（24 kHz / 16 bit / 单声道）≈ 86 MB，AAC 64 kbps ≈ 13 MB，差 6 倍。
而且 m4a 支持 `chpl` 章节原子，播客 App 能显示章节点；wav 什么元数据都挂不住。

章节不要用 `mp4chaps` / `AtomicParsley`，ffmpeg 直接吃 ffmetadata：

```
ffmpeg -i merged.wav -i chapters.txt -map 0:a -map_metadata 1 -map_chapters 1 \
       -c:a aac -b:a 64k -ac 1 -ar 24000 -movflags +faststart out.m4a
```

`chapters.txt` 的格式见 `cast_render.render_ffmeta()`。

## 内嵌字幕（--embed-sub）的两个坑

**一、不要先出 m4a 再二次 remux 加字幕。** `-c copy` 会把 srt 的 `text` tag
带进 mov 封装器，直接报 `Tag text incompatible with output codec id`。
必须一趟做完：音频和章节和字幕同时喂给 ffmpeg。

**二、m4a 里常会多出一条 `bin_data (text)` 轨。**
那不是 bug，是 QuickTime 的章节标题轨——ffmpeg 写章节时可能把标题同时放进
text 轨，而不是只写 chpl。实测**开不开 `--embed-sub` 都可能出现**（首次实战
3:43 那条就没有字幕但照样有这条轨）。内容就是小标题，播放器忽略即可。

旁挂 `.srt` 没有这两个问题，所以默认走旁挂。
