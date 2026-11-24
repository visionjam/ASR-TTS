---
name: asr-notes
description: >-
  把录音、口述音频或现成字幕转成可编辑的结构化 Markdown：DeepSeek ASR 转写（自动切片、
  逐段打时间戳）+ 两段式清洗与结构化，直接写进 Obsidian 库。也支持 .srt/.vtt/.ass
  字幕文件直接进管线（不重复转写、不花转写费）。附可执行脚本、提示词模板和成本护栏。
  当用户说"把这段录音转成文字""录音整理成文稿""语音转笔记""跑一下 ASR""这段口述帮我整理"
  "m4a 转 markdown""会议录音转纪要""把刚才说的话整理一下""录音文件在这里"
  "字幕转文稿""srt 转文字""把这个字幕文件整理一下""vtt/ass 转 markdown"时使用。
  不适用：边说边上屏的实时输入（用输入法自带语音）、纯文本排版与中英文空格（用 cn-typography）、
  自己制作字幕文件（打轴/校对时间）、音视频剪辑、把文字转成语音。
compatibility: >-
  python3 + requests + ffmpeg（可 pip install imageio-ffmpeg），需联网。
  只需要一个 DEEPSEEK_API_KEY，转写和整理都用它。字幕模式不必装 ffmpeg（不碰音频）。
  无工作区时只能给命令，无法代跑。
---

> **移植说明（从手机端导出，PC 上第一次用先读这段）**
>
> 1. 下文命令里的 `/skills/asr-notes/` 指**本文件所在目录**。PC 上换成本机的绝对路径：
>    Windows 直接用同步下来的 `ASRTTS\agent-skill\asr-notes\`——`windows\asrtts.ps1` 就指向这里，
>    不用再拷到 `D:\skills`；部署到别处时才换成 `~/skills/asr-notes/` 之类（路径含空格要加引号）。
> 2. 数据目录（`inbox/ out/ archive/ work/ config.env`）跟技能目录是分开的两处，
>    默认都相对**当前工作目录**，所以先 `cd` 到 ASRTTS 文库根目录再跑脚本。
> 3. 装一次依赖：`pip install requests imageio-ffmpeg`（自带静态 ffmpeg），或系统装 ffmpeg。
> 4. Key 不进这份导出件。每台机器自己在文库根目录建 `config.env`（模板在 `agent-skill/config.env.example`），
>    或设 `DEEPSEEK_API_KEY` 环境变量。

# 录音转文稿

一条命令走完：音频 → DeepSeek 转写 → 清洗 → 结构化 → Markdown。

转写在 `POST /v1/tools/asr`，单次只收 **≤120 秒 / ≤8MiB 的 16-bit PCM WAV 或 Ogg Opus**，
所以长音频必须先切片。脚本已经处理好了：ffmpeg 解码、按静音挑切点、逐片上传。

## 一、动手前先确认三件事

| 问题 | 怎么查 | 决策 |
|---|---|---|
| Key 和 ffmpeg 有没有 | `--check` | 缺任何一样先修，别急着跑 |
| 音频多长 | 脚本会打印 | 23 分钟 ≈ 13 片 ≈ 1.5 分钟跑完 |
| 要不要保留原音 | 问用户，或看目录 | 默认移到 `archive/`，不删除 |

## 二、跑法

数据目录就是共享目录本身，**文件不用先复制到别的工作区**。库里有一条一键入口：

```bash
cd /storage/emulated/0/Documents/vault/ASRTTS
bash 一键处理.sh              # inbox/ 里的音频 + 字幕，一次跑完
bash 一键处理.sh --dry-run    # 只列清单，不调 API、不写文件
```

它先跑音频，再把 `inbox/` 里的 `.srt/.vtt/.ass` 逐条送给 `--srt`，参数原样转发。
例外只有 `--kind`：一批里同时有音频和字幕时它只套给音频（字幕默认 external），
否则「这些是我口述的」会连带把别人的播客字幕也当自述整理。
PC 上没有这个壳，直接调下面的命令。

```bash
# 0. 首次装依赖（requests + 自带 ffmpeg 的 imageio-ffmpeg）
bash /skills/asr-notes/scripts/setup.sh

# 1. 验 Key 和 ffmpeg，不花钱
python3 /skills/asr-notes/scripts/asr.py --check

# 2. 看会处理哪些文件
python3 /skills/asr-notes/scripts/asr.py --inbox <音频目录> --dry-run

# 3. 正式跑
python3 /skills/asr-notes/scripts/asr.py --inbox <音频目录> --out <输出目录>

# 只转写，不整理（之后再补 AI 稿）
python3 /skills/asr-notes/scripts/asr.py --asr-only

# 只重跑 AI，不碰音频（改完 prompt 或手改 cleaned.md 后用）
python3 /skills/asr-notes/scripts/asr.py --ai-only "out/2026-11-18-xxx"

# 别人的音频（播客、讲座、访谈）换一套模板
python3 /skills/asr-notes/scripts/asr.py --kind external
python3 /skills/asr-notes/scripts/asr.py --ai-only "out/2026-11-18-xxx" --kind external

# 把旧版单文件输出拆成 index / transcript / notes（不调 API）
python3 /skills/asr-notes/scripts/split_md.py "out/2026-11-18-xxx.md"

# 纯文本走同一套整理（输入法打的一段话）
python3 /skills/asr-notes/scripts/asr.py --text note.txt

# 字幕文件直接整理：不转写、不花转写费（.srt / .vtt / .ass）
python3 /skills/asr-notes/scripts/asr.py --srt 某期播客.srt
python3 /skills/asr-notes/scripts/asr.py --srt 自己录的.srt --kind self

# 只看字幕转出来的正文（不写库、不调模型）
python3 /skills/asr-notes/scripts/subtitle.py 某期播客.srt --ts

# 短想法：记一条 / 聚合当天
python3 /skills/asr-notes/scripts/asr.py --add "一句话的想法"
python3 /skills/asr-notes/scripts/asr.py --daily
```

参数：`--inbox` 默认 `./inbox`（不存在则用当前目录）；`--out` 默认 `./out`；
`--archive` 默认 `./archive`；`--work` 默认 `./work`（切片缓存）；`--no-archive` 不搬源文件；
`--force` 重跑已处理过的文件。

处理过的文件按「文件名 + 大小」记在 `.asr_state.json`，重复跑自动跳过。
中途失败重跑时，已经转写好的分片会从 `work/<文件名-大小>/chunks.json` 复用，不重复计费。

## 三、Key 从哪来

优先级：`--config` 指定文件 → `$ASR_CONFIG` → `./config.env` → `~/.config/asr-notes/env` → 环境变量。

```
DEEPSEEK_API_KEY=sk-...
LLM_MODEL=deepseek-chat
EXTRA_DIRS=/storage/emulated/0/Music/Recordings
```

一个 Key 管两件事。**不要写进 SKILL.md 或脚本。** Key 出现在聊天记录里时，提醒用户用完去控制台重置。

## 四、产出长什么样

**一条录音一个文件夹**，四个文件各管一件事：

```
out/2026-11-18-产品想法/
├─ index.md         元数据卡片 + 双链
├─ transcript.md    逐字稿，ASR 原样输出，只在转写阶段写一次
├─ cleaned.md       清洗稿，去了口水词、修了同音字，顺序不变
└─ notes.md         AI 整理稿，可随时重生成
```

`index.md`（Obsidian 里当卡片看）：

```markdown
---
id: 2026-11-18-产品想法
title: 产品想法
type: voice-index
date: 2026-11-18
captured_at: 2026-11-18 14:35
duration: "23:35"
duration_sec: 1415
source: 录音_20261118.m4a
audio: ../archive/录音_20261118.m4a
transcript: "[[transcript]]"
notes: "[[notes]]"
asr_model: deepseek-asr
asr_chunks: 14
asr_cost: 0.1533
llm_model: deepseek-chat
status: draft
tags: [语音]
---

| 项 | 值 |
|---|---|
| 时长 | 23:35 |
| 转写 | deepseek-asr · 14 片 · ¥0.153 |

## 一句话
（从 notes.md 抽的首句）

## 文稿
- [[transcript|逐字稿]]
- [[notes|整理稿]]
```

`transcript.md` 按分片切成 `### 00:00` 小标题，Obsidian 大纲面板可以直接跳。
`cleaned.md` 把口水词、无意义重复删掉，修了同音错字；同样带 `### mm:ss` 标题，
标题下的长段落由脚本按句末标点重排成 120–220 字一段（纯本地处理，不花 token）——
分片边界对机器有意义，对眼睛没有，800 字一段没人读。
`notes.md` 是 AI 稿，`status: draft`，你随便改。

**读哪个**：

- 想快速过一遍 → `cleaned.md`（1400 字的稿子读 3 分钟）
- 要核对某句话到底怎么说的 → `transcript.md`，带 `[mm:ss]` 可回听
- 要拿去用、发给别人 → `notes.md`

**解耦的实际用法**：

| 场景 | 命令 | 花钱吗 |
|---|---|---|
| 改整理模板，重新出一版 | `--ai-only out/<文件夹>` | 只花结构化的钱（约 ¥0.03，8 秒） |
| 手动修了清洗稿里的错字 | 改 `cleaned.md`，再 `--ai-only` | 同上；AI 直接读 cleaned，改哪读哪 |
| 逐字稿改了，要重新清洗 | `--ai-only --reclean` | 多花一次清洗的钱，会覆盖 `cleaned.md` |
| 只想先拿到文字 | `--asr-only` | 只花转写费 |
| 整篇重来 | `--force` | 两笔都花 |

`--ai-only` 默认**读 `cleaned.md`**，不重跑清洗：省一次调用、快 10 倍，而且你手改过的清洗稿会被原样采纳。
没有 `cleaned.md` 时才回退到「读 transcript → 清洗 → 结构化」。

转写好的分片缓存在 `work/<文件名-大小>/chunks.json`，重跑同一文件不会重新上传。

**来源标注**：`核心观点` / `关键论点` 每条的末尾会有一个 `[原话]` 或 `[归纳]`，
条内还会附一句 `（原文："……"）`。

两步，模型摘句 + 脚本逐字核对：

1. 提示词要求模型每条附一句从原文**逐字复制**的话。脚本拿这句去原文里找：
   整句连续命中 → 保留引文，标 `[原话]`；找不到（模型改写过、或前半句真后半句拼）
   → **删掉引文**，标 `[归纳]`，并在日志里报一句。
2. 模型没给摘引 → 退回机械判据：本条正文里有没有一段连续 60%（至少 12 字）
   能在原文里原样找到。

以前只有第 2 步，实测在播客上 7 条论点全是 `[归纳]`——模型必然转述，比例阈值必然
不达标，"可直接引用"等于没有。现在两侧都有：模型负责摘，脚本负责验，验不过就删。

**引用时引哪句**：引 `[原话]` 条目里括号中的那一段，不要引整条——
论点本身可能仍是模型转述的，只有括号里那句保证是原声逐字。

- 括号里的引文 = 已逐字核对，可直接引用（仍建议对一下上下文）
- 只有 `[归纳]`、没有括号 = 模型合并转述的，引用前必须回原声
- 整节全是 `[归纳]` = 这节不适合直接引用，或者模型没按格式摘

**`kind` 区分这是谁的嘴**：

| kind | 用于 | 模板 | 输出关心什么 |
|---|---|---|---|
| `self`（默认） | 自己的口述、想法、会议里的自己 | `structure.md` | 我想清楚没有、下一步做什么 |
| `external` | 播客、讲座、访谈、别人的录音 | `external.md` | 他说了什么、有没有论据、以后怎么查 |

`external` 不输出"下一步""还没想清楚的地方"——讲话人不是使用者，那两个字段等于替别人做自我检讨。
它会多出「论据与数据」「广告与赞助」「提到的人 / 书 / 事」「我的标注」四节，最后那节留空给你填。

「广告与赞助」只列 `[mm:ss] 产品名 + 品类`，不复述文案、不引价格，广告里的销量和好评率
也不许写进「论据与数据」——否则一半的"数据"会是口播话术，检索的时候全是噪音。
口播广告在 `cleaned.md` 里仍然原样保留，折叠只发生在 AI 稿，逐字稿不动。

`kind` 写进 `index.md`，之后 `--ai-only` 不用再传。

**来源以用户的话为准，不要从文件名推断。** 用户会主动说明"这是我口述的""这是播客"，
照他说的设 `--kind` 即可；没说明时默认 `self`，不要追问第二次。

## 五、字幕文件：不转写，直接进管线

手上已经有字幕（自己用别家工具转的、下载的、视频平台导出的），就不用再花一遍转写费。
`.srt` / `.vtt` / `.ass` 都认，走同一个 `--srt`：

```bash
python3 /skills/asr-notes/scripts/asr.py --srt 某期播客.srt
python3 /skills/asr-notes/scripts/asr.py --srt 自己录的.srt --kind self
```

产出还是那四个文件，跟录音完全一样，所以用法不用另记。区别只有三处，都是刻意的：

| 处 | 录音 | 字幕 |
|---|---|---|
| `transcript.md` | ASR 原样 | 字幕原样，只去了滚动重复 |
| `cleaned.md` | 模型清洗过 | **不调模型**（`llm: 未清洗（字幕原文）`） |
| `index.md` | 「转写 · N 片 · ¥x.xx」 | 「来源 · 文件名 · N 段（未走 ASR，无转写费）」 |

**为什么跳过清洗**：人工字幕的标点和断句通常比模型好，拿模型去"清洗"只会把它改坏，
还白花一次调用。要真的洗（例如自动字幕没有标点），加 `--force` 之前先手动改 `cleaned.md`，
或者把 `from_subtitle` 里那句 `write_cleaned(..., llm="未清洗")` 换成走 `clean.md`。

**字幕会做什么处理**（`scripts/subtitle.py`）：

- 去滚动重复：YouTube/B站自动字幕是滚动式的，每屏 = 上一屏尾巴 + 新内容。
  不砍重叠，转出来每句会重复两三遍。
- 去标签：`<i>`、`<c.karaoke>`、`{\i1}{/i1}` 都清掉；`&amp;` 之类反转义。
- 拼句：两秒一屏的 cue 拼成句子，遇句末标点收一句。自动字幕没标点，
  攒到 200 字也强行切断——不然会攒出几千字一块。
- 分段：停顿 ≥1.8 秒且已攒够 30 字就另起一段（`--gap` / `--target` 可调）。
- 中文字幕被换行断成两行的，中间那个半角空格会吃掉。

**这条路径没有 ASR 时间戳的束缚**：cue 本身就是精确时间，`### mm:ss` 取的是每段起点，
比录音那套「约 2 分钟一片」准得多。

**抓字幕不归这里管**。要 URL → 字幕，用 yt-dlp 抓一份字幕出来（B站/YouTube 需要
登录态时用 `--cookies-from-browser`），拿到 srt 再喂给 `--srt`。
分工是：抓取归下载工具，整理成文稿归这边。

`--kind` 对字幕默认 **external**（字幕基本都来自别人的视频），自己的视频加 `--kind self`。

## 六、短想法：碎片与日结

长录音走上面的管线；一句话的灵感不该走 ASR，打字更快。这条路径只做「攒」和「合」：

```bash
# 随手记一条（追加到 00-Inbox/YYYY-MM-DD.md，带时间）
python3 /skills/asr-notes/scripts/asr.py --add "这个方案的问题是……"

# 晚上或第二天早上聚合
python3 /skills/asr-notes/scripts/asr.py --daily
python3 /skills/asr-notes/scripts/asr.py --daily --date 2026-11-17
```

输出 `out/daily/YYYY-MM-DD-日结.md`，结构：今天在想什么 / 要点（按主题合并）/ 待办 / 还悬着的 / 原话。

「还悬着的」是重点：说了半句、自己也没结论、前后矛盾的话，原样留着。
日结的价值不在总结，在让第二天早上的你看见昨天卡在哪。

手机上的用法：输入法语音说完，把文字发过来让我 `--add`，或者自己存进 `00-Inbox`。
碎片少于 2 条时脚本会拒绝聚合，避免为一条话花一次调用。

## 七、跑完怎么汇报

三行，不要把全文贴进对话：

```
✅ 2026-11-18-产品想法.md（23:35，13 片，转写 ¥0.15）
摘要：<一句话概括那行>
需要你确认：[听不清] 2 处 / 术语可能错的词 3 个
```

## 八、失败对照

错误堆栈写在 `work/error.log`（根目录的 `logs/` 是 Windows 壳的运行日志，手机端用不到；
`logs/legacy-*` 是旧版遗留，可以删）。

| 现象 | 原因 | 处理 |
|---|---|---|
| `401` | Key 错或过期 | `--check` 先确认 |
| `400 unsupported audio container` | 音频里混了封面图/第二条流 | 脚本已加 `-vn`；自己调 ffmpeg 时别忘 |
| `413` | 单片超 8MiB | 降码率（默认 24k opus），或缩短单片时长 |
| `504` 识别超时 | 单片太长或服务忙 | 重试；持续失败把 `CHUNK_MAX` 调小 |
| `429` | 并发超 50 | 脚本是串行的，正常不会触发；多开任务时会 |
| 切片处缺字/串词 | 切点落在句子中间 | 脚本优先切在静音；仍出现就缩短 `CHUNK_MAX` |
| 找不到 ffmpeg | 没装 | `bash scripts/setup.sh` 或 `pip install imageio-ffmpeg` |
| `第 N 片…返回空文本` | 那一段是静音，或接口这次返回了空串 | 脚本没把空片写进缓存，重跑只重试这一片（前面的照样走缓存）。听一下片头 `work/` 里留下的 `0NN.ogg` 再决定是放弃还是重跑 |
| `--ai-only` 报找不到 transcript.md | 传的是 md 文件路径或上一层目录 | 传那条录音的文件夹，脚本也接受直接传 md |
| `--ai-only` 解析出 0 段 | 逐字稿被改成了别的格式 | 每段必须是 `### mm:ss` 小标题 + 正文 |
| 日志报「清洗稿只有原文的 xx%」 | 模型清洗时顺手删了句子，或 ASR 真吞了字 | 拿 `cleaned.md` 对 `transcript.md`，缺哪段补哪段，再 `--ai-only` |
| 日志报「n 个片段没时间戳」 | 清洗模型丢了 `[mm:ss]` 行 | 同上；顺手在 clean.md 里把"保留每段开头的 [mm:ss]"加粗 |
| 摘引被降级成 `[归纳]` | 模型改写了引文，逐字核对没通过 | 这是护栏在工作，不是 bug；想提高通过率就在提示词里强调"逐字复制、宁短勿改" |
| `--srt` 报「没解析出字幕」 | 不是真的字幕文件、编码是 GBK、或格式是 `.sub/.idx` | 先 `python3 scripts/subtitle.py 文件` 看能不能解析；GBK 用 `iconv -f gbk -t utf-8` 转一遍 |
| 字幕转出来每句重复两三遍 | 自动字幕的滚动重复没被砍掉 | 脚本默认去重；仍有残留在 `DEDUP` 逻辑里看这一条是不是跨句重复（换行断在不同位置） |
| 字幕段落碎或太整块 | 停顿阈值不合适 | `subtitle.py x.srt --gap 2.5`（碎）/ `--gap 1.2`（整块）先试，再定 |
| 字幕正文里混进 `[音乐]` | 字幕轨自带的音效标记 | 段落开头的已清（`strip_sounds`）；句中的保留，那是原字幕的信息 |

## 九、成本护栏

| 项目 | 单价 | 30 分钟 | 备注 |
|---|---|---|---|
| DeepSeek ASR | ¥0.39/小时（限时 5 折，刊例 ¥0.78） | ≈¥0.20 | 按秒计费，不足一秒按一秒 |
| DeepSeek 整理 | 约 ¥0.05/万字 | ≈¥0.05 | 30 分钟口述约 8k 字 |

单次超过 ¥1 就先告诉用户再跑。脚本每次都会打印实际花费和总时长。

## 十、边界

- 语言不用传参，接口自己检测（中/粤/英/日/韩等 21 种）
- 接口不支持流式，必须整片上传后等结果
- 支持 WAV 与 Ogg Opus；m4a/mp3 一律先进 ffmpeg
- **没有句子级时间戳**：`### mm:ss` 是分片起点，约每 2 分钟一个，够定位回听，做不到逐句对齐
  （字幕模式例外：cue 本身是精确时间，段落起点就是准确时刻）
- 字幕模式只接受**已经存在的**字幕文件；打轴、做字幕不归这里
- `.sub/.idx`（图形字幕）不支持，它本质是图片，得先 OCR
- 文件夹名 = `日期-slug`，重名时加后缀；`--force` 会复用同名文件夹
- 不自动删除 `archive/`，那是唯一的原始凭证
- 输入法实时语音不走这里，那条路径用系统输入法更省事

## 十一、改提示词

`/skills/asr-notes/scripts/prompts/clean.md` 管清洗（去口水词、修同音字、中英之间加空格），
`/skills/asr-notes/scripts/prompts/structure.md` 管自己口述的结构，
`/skills/asr-notes/scripts/prompts/external.md` 管别人的音频（播客/讲座），
`/skills/asr-notes/scripts/prompts/fragments.md` 管碎片的日结。

- 要文章体不要纪要体：只换 structure.md，脚本不动
- 错字集中在某类术语：在 clean.md 末尾追加术语表，比换引擎便宜
- 嫌 AI 稿太整齐、把犹豫抹平了：改 structure.md 里那条「保留毛边」，这是整套里最影响成品质感的一行
- 想换整理模型：改 `LLM_MODEL`（账号下有 `deepseek-chat`、`deepseek-flash`、`deepseek-v4-pro`）

改提示词时的两条硬约束：

- `（原文："……"）` 是脚本核对引文的接口，格式不能动。措辞可以改，这个括号加引号的写法
  不能删、不能换成别的形式，否则 `stamp_quotes` 认不出来，全部退回机械判据——
  在播客上就等于全灭（实测 7 条全 `[归纳]`）。
- 段落切分不归提示词管。`cleaned.md` 的分段是脚本按句末标点做的，
  提示词里让模型"注意分段"是白花钱，模型分得比脚本差。

接口细节、已踩过的坑见 [providers.md](references/providers.md)。

## 十二、改代码

脚本分四层，改之前先确认落在哪一层（都在 `scripts/` 下）：

| 文件 | 管什么 | 什么时候动它 |
|---|---|---|
| `asr.py` | 配置、ffmpeg 切片、ASR / LLM 调用、主流程 | 换接口、改切片策略、加参数 |
| `asr_render.py` | front matter 与四个产出文件的写法、清洗稿重排 | 改产出长什么样（加字段、调段落长度） |
| `asr_quotes.py` | 清洗稿体检 + 摘引逐字核对 | 调阈值、改判据 |
| `asr_common.py` | log / mm:ss / slugify | 基本不用动 |

这四层是照「测试能不能离线跑」切的：渲染和校验不联网、不读配置，
所以 `tests/t_core.py` 可以拿它们当纯函数压。往 `asr_render.py` 里塞网络调用
就会把这个属性破坏掉。

改完跑：

```bash
bash /storage/emulated/0/Documents/vault/ASRTTS/tests/run.sh
```

退出码 0 才算通过；**2 表示有跳过项（素材缺失），不能算通过**。
库里的样例录音被清理掉时，t_core 会改用内置合成稿继续跑，不会静默跳过。
