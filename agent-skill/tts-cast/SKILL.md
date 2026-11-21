---
name: tts-cast
description: >-
  把文章（Markdown / txt / 公众号稿）变成可以通勤时听的播客单集：DeepSeek TTS 逐段合成，
  ffmpeg 拼成 m4a，同时产出句级 .srt 字幕、.lrc 歌词、内嵌章节和带时间戳的口播稿，
  改一个字只重烧那一段。可选用 LLM 先把书面语改写成口播稿。
  当用户说"把这篇做成播客""文章转成音频""我想听这篇文章""生成一期播客"
  "把稿子读出来""配上字幕方便定位""播放器看不到字幕""要能显示歌词"
  "文章转 m4a/mp3""上学路上听"时使用。
  不适用：把录音转成文字（用 asr-notes）、边说边上屏的实时朗读、自己打轴做字幕、
  音视频剪辑、把整篇文章总结成 AI 双人对谈（那是另一类产品，不是朗读）。
compatibility: >-
  python3 + requests + ffmpeg（可 pip install imageio-ffmpeg），需联网。
  只需要一个 DEEPSEEK_API_KEY，和 asr-notes 共用。
---

> **移植说明（PC 上第一次用先读这段）**
>
> 1. 下文命令里的 `/skills/tts-cast/` 指**本文件所在目录**。PC 上换成本机的绝对路径：
>    Windows 直接用同步下来的 `ASRTTS\agent-skill\tts-cast\`——`windows\asrtts.ps1` 就指向这里，
>    不用再拷到 `D:\skills`；部署到别处时才换成 `~/skills/tts-cast/` 之类（含空格要加引号）。
> 2. 数据目录（`inbox/ out/ work/ config.env`）是**库根**，跟技能目录是分开的两处。
>    先 `cd` 到库根再跑，否则脚本的默认路径会落到别处。
>> 3. 装一次依赖：`pip install requests imageio-ffmpeg`（后者自带静态 ffmpeg）。
> 4. Key 不进这份导出件。库根放 `config.env`（和 asr-notes 共用同一个），
>    或设环境变量 `DEEPSEEK_API_KEY`。

# 文章转播客

一条命令：文章 → 逐段 TTS → 带字幕和章节的 m4a。

关键设计只有一句：**朗读粒度是段，字幕粒度是句，数据只有一份。**

- 按段合成：整段一口气念下来，韵律连贯，不会像播报机一句一顿。
- 按句出字幕：段内按字数比例插值时间，误差 ±0.3 秒，够定位、看着不出戏。
- 一份数据四个视图：`.srt` / `script.md` / `index.md` / m4a 里的 `chpl`，
  全部从同一张带时间的段落表渲染，改样式只改 `cast_render.py`。

## 一、动手前先确认

| 问题 | 怎么查 | 决策 |
|---|---|---|
| Key 有没有 | `--check` | 缺就填，和 asr-notes 用同一个 |
| ffmpeg 有没有 | `--check` | 缺就 `bash scripts/setup.sh` |
| 多少钱 | `--dry-run` | 先估，别闷头跑 |
| 要不要改稿 | 看原文有多"书面" | 千禧年前后的评论、专栏，建议加 `--rewrite` |

## 二、跑法

```bash
# 0. 装依赖（每台机器一次）
bash /skills/tts-cast/scripts/setup.sh

# 1. 自检，不联网
python3 /skills/tts-cast/scripts/cast.py --check

# 2. 出计划：几段、多少字、多少钱、多长，一分钱不花
python3 /skills/tts-cast/scripts/cast.py --dry-run inbox/某篇.md

# 3. 正式跑
python3 /skills/tts-cast/scripts/cast.py inbox/某篇.md

# 4. 先改稿再读（书面语直读容易"念经"）
python3 /skills/tts-cast/scripts/cast.py --rewrite inbox/某篇.md

# 5. 改完稿重跑：只有改过的段会重烧
python3 /skills/tts-cast/scripts/cast.py out/2026-11-18-某篇/script.md --force

# 6. 换豆包合成（默认仍是 DeepSeek；两家账单各自独立）
python3 /skills/tts-cast/scripts/cast.py --provider doubao inbox/某篇.md
python3 /skills/tts-cast/scripts/cast.py --provider doubao --speech-rate -20 \
        --voice zh_male_liufei_uranus_bigtts inbox/某篇.md
```

数据目录里有一层壳，等价于上面第 3 条。壳在**库根**，没有 `cast/` 这一层
（旧版曾经有过，照着旧文档 `cd cast` 会直接失败）：

```bash
cd /sdcard/ASRTTS
bash 一键开播.sh                 # 扫 inbox/，已经做过的不重跑
bash 一键开播.sh --dry-run
bash 一键开播.sh --rewrite
```

参数：`--inbox` `--out` `--work` 默认 `./inbox` `./out` `./work`；
`--voice stella|tide`；`--date` 目录日期前缀；`--slug` 目录名；
`--rewrite`；`--dry-run`；`--preview N`；`--force`；`--resynth`；`--no-normalize`；
`--embed-sub`；`--verbose`。

**`--preview N` 是生产流程的第一步。** 只合成前 N 个字，切点落在句末，
产物落在 `<日期-标题>-试音/`，不占正式目录。300 字约 1 分钟、5–8 分钱。
整篇烧完才发现音色不对或语速太快，那才叫浪费。

**`--force` 不重烧。** 它只做两件事：允许覆盖已有产出目录、把成品重做一遍。
没改过的段一律从 `work/` 复用，本次计费字符为 0。
真要全部重烧（改了 prompt、怀疑音质）才用 `--resynth`，那个才花钱。

**`--dry-run` 一定免费。** 连 `--rewrite` 也不会真发出去——那也是一次付费调用。

## 三、Key 从哪来

优先级：`--config` → `$CAST_CONFIG` → `./config.env` → `../config.env`
→ `~/.config/asr-notes/env` → 环境变量。

```
DEEPSEEK_API_KEY=sk-...
CAST_VOICE=tide          # 可选，stella 女声 / tide 男声
DOUBAO_API_KEY=...       # 可选，用豆包合成才需要（新版控制台的 X-Api-Key）
TTS_PROVIDER=doubao      # 可选，把默认供应商切成豆包；不写就是 deepseek
```

Shell 脚本在库根，`config.env` 就在同一层，**不用再建一份**。
Key 不进脚本、不进导出件，任何时候不要打印或回显。

## 四、产出

```
out/2026-11-18-某篇/
├─ 2026-11-18-某篇.m4a   AAC 64k 单声道 24k，内嵌 chpl 章节，约 13MB/30分钟
├─ 2026-11-18-某篇.srt   句级字幕（mpv 同名自动加载）
├─ 2026-11-18-某篇.lrc   歌词（安卓播放器认这个，一行一句、按 18 字切）
├─ script.md             带 [mm:ss] 的口播稿
└─ index.md              元数据卡片（和 asr-notes 的 out/ 同形）
```

`srt` 不只是给人看的，是给检索用的：

```bash
bash cast-find.sh 高尔夫     # grep 字幕 → 拿到时间戳 → mpv 从那句开始播
```

## 五、几个必须知道的坑

1. **单次请求上限 10,000 计费字符**（汉字算 2），且单次最多返回 30 分钟音频。
   脚本已经把段压到 3,600 计费字符，超了会截断并提示。
2. **内容审核**。文本不过审返回 400 `Content Exists Risk`，**该段跳过、不扣钱、
   继续跑后面的**，跳过的段会写进 `index.md`。整批不会因此中断。
3. **返回的是 wav，没有 mp3**。所以最后一定要过一遍 ffmpeg 转 AAC，别直接把
   wav 塞进播放器——30 分钟 wav 有 86MB。
4. **不支持调语速**。书面语偏快或偏慢时，用改稿阶段的标点密度调，不要指望参数。
5. **字幕必须手动断行**。`mov_text` 和部分播放器不自动折行，一行 40 字会横穿
   屏幕。`cast_text.wrap_lines` 负责这件事，别绕过它。
6. **三种播放器认三种东西，所以要生成两份**。mpv 认同名 `.srt`；安卓本地播放器
   （Pixel Player 一类）把 `.lrc` 当歌词读、不认 srt；VLC 对纯音频默认不建视频
   窗口，srt 画不出来，而且它不解析 lrc。播客 App 只认 `chpl` 章节。
   `--embed-sub` 能把字幕塞进容器，但会多出一条章节标题用的 text 轨（正常现象），
   且必须一趟做完，见 references。
7. **判「做过没有」看成品，不看目录**。`out/<日期-标题>/` 存在、但里面既没有 m4a
   也没有 `index.md`，说明上次在建完目录、开烧之前就被打断了（切后台被杀、手滑
   Ctrl-C）。这种空壳不算做过，脚本照常重做，并打印「目录在、成品不在（上次中途
   断了），这次重做」。只有目录里真有成品才跳过。想强行重做仍然用 `--force`；空壳
   重跑时没改过的段照样从 `work/` 复用，计费为 0。
