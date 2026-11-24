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

**先问清这次跑的是哪个文件、要它干什么，再动手。** 不要从文件名猜用途：
`inbox/` 里录音和文章混着放，`[播客]` 这种前缀也不代表它就是播客。
一句话问清「这篇给谁听、是照念还是改成口述稿」，比跑错一次再重烧便宜得多。
确认那句话里带上字数、估价和时长（`--dry-run` 免费），用户点头再跑真格的。

| 问题 | 怎么查 | 决策 |
|---|---|---|
| Key 有没有 | `--check` | 缺就填，和 asr-notes 用同一个 |
| ffmpeg 有没有 | `--check` | 缺就 `bash scripts/setup.sh` |
| 多少钱 | `--dry-run` | 先估，别闷头跑 |
| 要不要改稿 | 看原文有多"书面" | 想保留原文结构只改句子 → `--rewrite`；想让它像人讲给你听 → `--spoken` |

**跑完原件会进 `archive/`**（`--no-archive` 关）：inbox 是投递口，不是仓库。

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
# 7. 本地 Kokoro（仅 PC，费用 0）：先跑 tools/local-tts/start.cmd 起服务
python3 /skills/tts-cast/scripts/cast.py --provider local inbox/某篇.md
```

数据目录里有一层壳，等价于上面第 3 条。壳在**库根**，没有 `cast/` 这一层
（旧版曾经有过，照着旧文档 `cd cast` 会直接失败）：

```bash
cd /storage/emulated/0/Documents/vault/ASRTTS
bash 一键开播.sh                 # 扫 inbox/，已经做过的不重跑
bash 一键开播.sh --dry-run
bash 一键开播.sh --rewrite
```

参数：`--inbox` `--out` `--work` 默认 `./inbox` `./out` `./work`；`--archive ./archive`；
`--voice stella|tide`；`--date` 目录日期前缀；`--slug` 目录名；
`--rewrite`；`--spoken` `--stop-at-outline` `--outline`（见第七节）；
`--dry-run`；`--preview N`；`--force`；`--resynth`；`--no-normalize`；
`--no-archive`（不搬原件）；`--no-verbalize`；`--embed-sub`；`--verbose`。

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
LOCAL_TTS_URL=http://127.0.0.1:1919   # 可选，--provider local 的服务地址（仅 PC）
LOCAL_VOICE=auto         # 可选，auto 按语言选中文 zf_001 / 英文 af_heart
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
├─ 原文附录.md           代码块/表格/长 URL/公式/夹注（没有就不生成）
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

## 六、读法层（进 TTS 之前那一步）

只做一件事：把引擎念不出来的写法换成该念出来的词，并且在**算段 hash 之前**落地。
默认开；`--no-verbalize` 或 config.env 里 `CAST_VERBALIZE=0` 关掉。

- 规则表在 `scripts/verbatim.py`；库根放 `verbalize.tsv`（一行「原文<TAB>读法」）
  就能加词，不用改代码。
- 先看差异、不写文件、不花钱：`python3 scripts/verbatim.py --diff out/某篇/script.md`。
- 念不出来的东西不念，写进成品目录的 `原文附录.md`，按「第 N 段（接在 mm:ss 之后）」
  定位——它没被念过，所以没有自己的时间点。时间取那一段的**结束**时刻。两类来源：
  解析期整块摘出的代码块和表格（`cast_text.parse_article` 的 `dropped`），
  以及读法层行内摘出的长 URL、`$…$` 公式、`（原文：…）` 夹注。
- 幂等：同一篇跑两遍逐字相同，已经展开过的写法不会被二次处理。

**规则表为什么这么短。** 2026-11-24 拿二十条短句在豆包 2.0 上实测：年份、数量、
百分比、小数、分数、金额、型号、版本、缩写、km/h、m²、℃、范围、±、日期、书名号、
破折号，引擎全都念得对。唯一念错的是 `→`，它读成「右箭头」。所以读数规则只剩一条，
其余全是标记清理。样本和对照表在库的 `tests/verbalize/`，换 provider 之前要重测。

**一个坑。** 老版本写的 `work/<篇>/segments.json` 没有 `v` 字段（provider|音色），
`_cache_hit` 只肯在 `deepseek|tide` 下认它。所以拿豆包重跑旧稿会**全量重烧**，
不是只烧改动的那几段——`--dry-run` 现在按同一口径报「N 段没有可用缓存」。

协议原文存档：`references/verbalization-protocol.md`。里面引用的 `pronunciation.md`、
`spell_literal.py` 没有随附，可执行的规则以 `scripts/verbatim.py` 为准。

## 七、口述稿（--spoken）

`--rewrite` 是**朗读编辑**：只把句子改顺，顺序、篇幅、论点都照原文，产出还是"念文章"。
`--spoken` 是**口述编辑**：重排顺序、合并论点、删掉枝节，目标是"像人在你旁边讲"。
两者互斥，同时给直接 `p.error` 退出。

两遍，都是 LLM 调用，都不花 TTS：

1. **大纲**：整篇进，只回一张图纸（≤800 字）——主线、听众、分块计划（每块保留什么、
   并掉什么、接头怎么接）、删改清单、结尾落点。提示词 `scripts/prompts/spoken-outline.md`。
2. **落地**：按原文小标题切 3–6 千字的块，每块单独发「大纲 + 上一块结尾两段 + 这块原文」，
   第一块出 `# 标题`。块间接头靠大纲里写定的过渡，不回炉重写。
   提示词 `scripts/prompts/spoken-block.md`。

**为什么分块**：管线里其他环节本来就是分段的（合成按 ≤600 字切、ASR 按静音切片），
只有改写曾经整篇一次发。分块之后，超时和静默截断都没了，总 token 还更省。

产出比朗读版多三个文件。目录名带 `-口述` 后缀，而且用**原文**标题定名——改过的标题会被
模型动，拿它当目录名等于每跑一次换一个 `work/`，段缓存全废。

```
out/2026-11-24-某篇-口述/
├─ 大纲.md        图纸，可手改后复用
├─ 删改清单.md    删了什么、并了什么，逐条可查
├─ 口述稿.md      落地后的完整文字（无时间戳）
├─ 体检.md        自动体检：章节数、重复段落、超长句
└─ 其余同朗读版：m4a / srt / lrc / script.md / index.md
```

**落地完自动体检，不花钱、不联网。** 查三样——原文几节、口述稿几节（少了听众没法跳段）；
有没有把同一件事讲两遍（连续十五字重复，报段号）；有没有超过六十字的句子（报段号）。
结果写进 `体检.md`，同时打进日志。这三条都是第一次真跑时踩出来的毛病，
不是假想的：那版丢了全部小标题、开头把平泉讲了两遍、还有一句一百二十三字。
体检只报警不拦，改不改由你；要改就动 `口述稿.md` 再 `--force`，只有改过的段重烧。

三种用法：

```bash
bash 一键开播.sh --spoken                       # 大纲 → 落地 → 合成，一口气
bash 一键开播.sh --spoken --stop-at-outline     # 只出大纲，零 TTS 费用
python3 cast.py --spoken --outline out/2026-11-24-某篇-口述/大纲.md inbox/某篇.md
```

规矩：

- 允许合并论点、删枝节，落到原文六到七成；低于 55% 在 `大纲.md` 尾巴标一句「删得偏多」，
  不硬拦。**默认不中途停**，`--stop-at-outline` 只在你想先审图纸时用。
- 观点归属不变：作者还是"我"，可以直接对听众说"你"；不要主持腔、不要替作者加结论。
- 数字、型号、百分比照抄原文写法（引擎念得对），跟朗读版同一条口径。
- 原文超过 5 万字直接拒：整篇一次发会静默截断，分块又没有全局视野，先切两篇。
- 从 `out/…-口述/script.md` 或 `口述稿.md` 重跑会**自动跳过改写**，不会二次改写。
- 改一个字只重烧一段，还是 `--force` 那条老路；`--force` 不会重跑大纲。

## 八、多篇合并：稿子靠写，不靠跑

几篇文章蒸成一集，**不用新管线**：哪段留、哪段并、讲多少分钟，是写稿那一步的判断；
脚本只负责把成稿烧成声音。写稿的人可以是助手自己——这时它管的不再是"回话风格"，
而是**一份稿子**，规范就是 `references/verbalization-protocol.md`。

**写稿时守四条（协议里对耳朵最要紧的那几条）：**

1. **并列项不超过三个。** 三篇文章各带一套"三个原因"，堆一起就是九项，听众一个都记不住。
   超了就分组，或者砍到两个。
2. **关键概念起稳定名字，前后一个叫法。** 三篇都提到同一件事，不是删掉两次，
   是合成一个锚点只讲一次；后面再出现时要加作用（接证据、落到条件），不是复述。
3. **比较带回基准。** 多篇材料的单位、口径常常不一样：七个百分点、百分之七、翻几倍。
   每次比较都要重报对象和单位，别让听众拿几个裸数字自己配对。
4. **数字照抄原写法，读法交给读法层。** 型号、版本、百分比别手工换算成汉字——
   第六节的 `verbatim.py` 才是读数权威，字幕也跟着同一份文本走。

协议里剩下的（不列表、不写符号公式、结论先说、每两三句完成一个推理环节）照旧适用；
它引用的 `pronunciation.md`、`reasoning.md`、`examples.md` 没有随附，别去找。

**交稿前自己打四个勾**（这几条机器查不出来）：

- 每段能不能只靠听就跟上，有没有"前者""上面那项""如图"这类要回看的指代？
- 三个并列项以上的地方，分组了没有？
- 同一个概念前后换过叫法没有？
- 数字的单位和口径，跨篇一致吗？不一致的地方交代了没有？

**再用量化那一关**，不联网不花钱：

```bash
python3 scripts/check_draft.py inbox/某篇-口述.md inbox/原文1.md inbox/原文2.md
```

查时长（默认 265 字/分钟，超 30 分钟直接报多出多少字）、章节数（对得上原文没有）、
连续十五字重复、超过六十字的长句。退出码 1 就该改，改完再量，通过了再烧。

**烧的时候不要带 `--spoken`**：稿子已经是成稿，再加一遍就是二次改写。
`python3 scripts/cast.py inbox/某篇-口述.md` 或 `bash 一键开播.sh` 直接跑。

字数：三十分钟的一集上限约七千八百字（豆包实测 260–275 字/分钟），目标写六千八到七千三，
留一点余量给报站和过渡。多篇合并时压缩比没有参考价值——它取决于你要几篇拧成一条线，
不取决于你删得狠不狠。

