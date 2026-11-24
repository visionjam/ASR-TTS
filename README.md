# ASRTTS 工作区

录音 → DeepSeek 转写 → DeepSeek 整理 → 本目录 `out/` 里的 Markdown。
反向也有一条：文章 → DeepSeek TTS → `out/` 里的 m4a + 字幕 + 歌词 + 口播稿。
文章这一侧多一条支路：`--spoken` 会先重排成口述稿（大纲 + 分块落地），成品目录带 `-口述`，
多出 `大纲.md`、`删改清单.md`、`口述稿.md`、`体检.md`；朗读与口述二选一，不能同时给。
两条线跑完都把 `inbox/` 里的原件搬进 `archive/`，投递口不堆仓库。
两条线共用同一个 Key、同一份账单。代码在技能里：`/skills/asr-notes/`、`/skills/tts-cast/`。

## 目录

```
ASRTTS/
├─ inbox/              录音、字幕、文章都丢这里（各自认领）
├─ 00-Inbox/           短想法碎片，一天一个 md（首次 --add / --daily 时自动创建）
├─ out/                一条一个文件夹（录音稿 或 播客单集）
│  ├─ 2026-11-18-xxx/
│  │  ├─ index.md        元数据卡片 + 双链
│  │  ├─ transcript.md   逐字稿，ASR 原样
│  │  ├─ cleaned.md      清洗稿，去口水词
│  │  └─ notes.md        AI 整理稿，可随时重生成
│  └─ daily/
│     └─ 2026-11-18-日结.md   当天碎片的聚合
├─ archive/            处理完的原始音频
├─ work/               分片缓存，重跑时省一次钱（别删，会随库同步）
│  └─ error.log       失败堆栈写在这里
├─ logs/              Windows 壳的运行日志（不同步；错误堆栈看 work/error.log）
├─ agent-skill/        技能导出件，给 PC 端 agent 用（不含 Key），见里面 README-PC.md
├─ windows/            Windows 壳：asrtts.ps1 + README.md（PowerShell 5.1）
├─ ASRTTS.cmd          双击菜单（Windows）
├─ 一键处理.cmd        双击处理 inbox/（Windows）
├─ 一键开播.cmd        双击开播（Windows）
├─ 自检.cmd            双击自检（Windows）
├─ check-sync.sh       检查技能与导出件是否一致（逐文件 md5）
├─ 一键处理.sh         inbox/ 里的音频 + 字幕一次跑完（参数转给 asr.py）
├─ 一键开播.sh         inbox/ 里的文章转播客，见 播客说明.md
├─ cast-find.sh        用字幕反查时间点，跳着听
├─ tests/              回归测试：run.sh 跑四套（t_core / t_subtitle / t_cast_spoken / t_check_draft），不花钱，退出码 0 才算过
├─ config.env          DeepSeek / 豆包的 Key（自己建，模板见 config.env.example）
├─ config.env.example  Key 与供应商的模板，照抄一份改名 config.env
├─ README.md           本文件：目录与日常命令
├─ 助手说明.md         粘进 RikkaHub 助手设定的那段（手机版 / PC 版）
├─ 播客说明.md         文章转播客这条线的用法
├─ 同步说明.md         Syncthing 配对、忽略规则、排查
├─ .stignore           Syncthing 忽略规则（不进同步），见 同步说明.md
├─ stignore.template   忽略规则的唯一副本，两端照它复制；check-sync.sh 会比一次
└─ .asr_state.json     处理记录，防重复计费（自动生成）
```

## 第一次用

```bash
# 1. 装依赖（requests + 自带 ffmpeg）
bash /skills/asr-notes/scripts/setup.sh

# 2. 建 config.env，照抄 config.env.example 填一行 Key
#    Key 在 https://platform.deepseek.com/api_keys

# 3. 验一下
cd /storage/emulated/0/Documents/vault/ASRTTS
python3 /skills/asr-notes/scripts/asr.py --check
```

## Windows 端

PC 上不用 bash 壳，用 `windows\asrtts.ps1`（PowerShell 5.1）和根目录的 `.cmd` 双击入口。
安装、日常命令、排错、成本护栏都在 [windows/README.md](windows/README.md)。

```powershell
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 check --offline
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 process --dry-run
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 cast --dry-run
```

## 之后每次：一键处理

文件就在这个目录里，**不用导入到任何工作区**，丢进 `inbox/` 跑一条：

```bash
cd /storage/emulated/0/Documents/vault/ASRTTS
bash 一键处理.sh              # inbox/ 里的音频 + 字幕，一次跑完
bash 一键处理.sh --dry-run    # 只列清单，不调 API、不写文件
bash 一键处理.sh --kind self  # 自己口述的（默认就是 self；播客/讲座用 --kind external）
bash 一键处理.sh --force      # 重跑已处理过的
```

Windows 上等价的是双击 `一键处理.cmd`，或
`powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 process`。

参数原样转给 `asr.py`，所以 `--asr-only`、`--no-archive` 之类都能用。
唯一的例外是 `--kind`：一批里同时有音频和字幕时它只套给音频，字幕按默认
`external` 整理（同一个 `--kind` 两边通吃，会把自己录的字幕当别人的视频处理）。
脚本做三件事：先跑音频，再把 `inbox/` 里的 `.srt/.vtt/.ass` 逐条送进 `--srt`，最后列出 `out/` 里最新的几个文件夹。

不想用壳、只想跑单个文件的，还是直接调脚本：

```bash
python3 /skills/asr-notes/scripts/asr.py            # 处理 inbox 里的新音频
python3 /skills/asr-notes/scripts/asr.py --dry-run  # 只想看看有哪些文件
```

## 常用参数

| 命令 | 作用 |
|---|---|
| `--check` | 验 Key 和 ffmpeg |
| `--dry-run` | 列文件，不调 API |
| `--asr-only` | 只转写，不整理 |
| `--ai-only out/<文件夹>` | 只重跑 AI，不重转写（改 prompt / 改完稿子后用） |
| `--reclean` | 配合 `--ai-only`，忽略 `cleaned.md` 重新清洗 |
| `--text note.txt` | 输入法打的一段话，走同一套整理 |
| `--srt 某期.srt` | 字幕文件（.srt/.vtt/.ass）直接整理，不转写、不花转写费 |
| `--add "想法"` | 往今天的碎片笔记追加一条（带时间） |
| `--daily` | 聚合当天碎片，出 `out/daily/YYYY-MM-DD-日结.md` |
| `--kind external` | 别人的音频（播客/讲座）用另一套模板 |
| `--no-archive` | 不搬源文件 |
| `--force` | 重跑已处理的文件 |

一句话的灵感不用走转写：用 `--add "想法"` 记一条，或者直接用输入法语音发过来。`--text` 只留给已经打成段落的文本。

`--kind` 只有两档：默认 `self`（自己口述），`external`（别人的音频）。
`external` 会输出「论据与数据」「广告与赞助」「提到的人 / 书 / 事」，不输出「下一步」「还没想清楚」。
选择记在 `index.md` 里，之后 `--ai-only` 不用再传。

## 已经有字幕的，不用再转一遍

视频/播客的字幕文件（`.srt` / `.vtt` / `.ass`）直接进同一条管线：

```bash
python3 /skills/asr-notes/scripts/asr.py --srt 某期播客.srt          # 默认按 external 整理
python3 /skills/asr-notes/scripts/asr.py --srt 自己录的.srt --kind self
python3 /skills/asr-notes/scripts/subtitle.py 某期.srt --ts          # 只看正文，不写库
```

产出还是 `index/transcript/cleaned/notes` 四个文件。两处不同，都是刻意的：
字幕自带标点，所以 `cleaned.md` **不调模型清洗**（`llm: 未清洗（字幕原文）`）；
`index.md` 不写转写费，改写「来源 · 文件名 · N 段」。

自动字幕（YouTube / B站 AI 字幕）是滚动式的，脚本会砍掉重叠部分，
不然每句会出现两三遍。抓字幕不归这边管——用 yt-dlp 抓一份字幕（B站/YouTube 需要登录态时
加 `--cookies-from-browser`），拿到 srt 再喂进来。手机上助手工作区里那个 `tools/subs.py`
是包了这一层，但它不在本库里，不会同步到 PC。

逐字稿和 AI 稿是分开的两个文件：改整理模板只花 ¥0.05，不用重新转写。
手改了 `transcript.md` 的错字，再跑 `--ai-only`，AI 会基于你改后的版本。

## 成本

按 23 分钟的录音实测：转写 ¥0.153，整理约 ¥0.05，合计两毛。
10 元预算够每月约 25 小时转写（¥0.39/小时）+ 对应的整理费。

`work/` 里的分片缓存**跟着库一起同步**，所以同一条录音在手机和 PC 上各跑一次，
转写费只付一次。这是刻意的：早先 `work/` 被 `.stignore` 排除，理由是「缓存可以重建」，
但重建就是重新转写——省下的是流量，花了的是钱。

## 出问题

Windows 特有的问题（执行策略、编码、找不到 ffmpeg、路径过长）先看
[windows/README.md](windows/README.md) 第六节。

看 `work/error.log`。常见原因：Key 错（`--check` 会直接说）、
ffmpeg 没装、音频里混了封面图（脚本已用 `-vn` 规避）。
更细的排查表在 `/skills/asr-notes/SKILL.md` 第八节。

## 跨设备同步

手机和 Windows 之间怎么配对、哪些目录同步哪些不同步、`.stignore` 全文，
以及校园网隔离 / Tailscale 走中继太慢的排查，见 [同步说明.md](同步说明.md)。
