# 技能导出件 · PC 端使用说明

手机端 `/skills/` 下两个技能的导出副本，给 PC 上的 agent（Claude Code、Codex、
其他读 `SKILL.md` 的工具）用。目录里只有**代码和提示词**，
没有 Key、没有录音、没有文稿。

```
agent-skill/
├─ README-PC.md              ← 本文件
├─ config.env.example        PC 用的模板（不含 Key）
├─ asr-notes/                录音 → 文稿
│  ├─ SKILL.md
│  ├─ references/providers.md
│  └─ scripts/  asr.py · asr_render.py · asr_quotes.py · asr_common.py
│                subtitle.py · split_md.py · setup.sh · prompts/
└─ tts-cast/                 文章 → 播客（反向）
   ├─ SKILL.md
   ├─ references/  deepseek-tts.md · sample.md · verbalization-protocol.md
   └─ scripts/  cast.py · cast_text.py · cast_render.py · cast_common.py
                 verbatim.py · check_draft.py · make_samples.py · selftest.py · setup.sh
                 prompts/rewrite.md · spoken-outline.md · spoken-block.md
```

`tts-cast/scripts/verbatim.py` 是进 TTS 之前的读法层，默认开，`--no-verbalize` 关。
它的回归样本（二十条短句和对照表）**不在导出件里**，在 Syncthing 同步下来的库根：
`ASRTTS/tests/verbalize/`。换了 TTS 供应商要重测时，从那里取，别另起一份。

两个技能**共用一个 `DEEPSEEK_API_KEY`**、共用一份账单。asr 那边是转写 + 整理，
cast 这边是改写（二选一：`--rewrite` 只改句子、保住原文结构；`--spoken` 重排成口述稿，
先出大纲再分块落地）+ 合成。

## 一、装到 PC

技能目录和数据目录是**两回事**，别混：

| | 放哪 | 内容 |
|---|---|---|
| 技能目录 | 任意位置，建议 `~/skills/{asr-notes,tts-cast}`（Windows `D:\skills\...`） | 上面那两套代码 |
| 数据目录 | 本文件夹的**上一级**，也就是 Syncthing 同步下来的 `ASRTTS/` | inbox/ out/ archive/ work/ config.env |

**放置**：把 `asr-notes/` 和 `tts-cast/` 两个目录都拷到 PC 上你放技能的地方。

- Claude Code：`~/.claude/skills/<名字>/`（用户级）或项目内 `.claude/skills/<名字>/`
- 其他 agent：只要机制是「读 `SKILL.md` 的 name/description 决定是否加载」，
  目录结构保持原样即可
- 只想手动跑脚本：随便放哪都行，不用管技能机制
- **本库的 Windows 工作流**直接用同步下来的 `agent-skill/{asr-notes,tts-cast}`，
  不用再拷到 `D:\skills`；`windows\asrtts.ps1` 默认就指向这里。

**路径替换**：`SKILL.md` 里的命令写的是手机上的 `/skills/<名字>/…`，PC 上换成实际路径。

```bash
for s in asr-notes tts-cast; do
  sed -i.bak "s#/skills/$s#$HOME/skills/$s#g" ~/skills/$s/SKILL.md
done
```

不替换也能用：两份 `SKILL.md` 开头都有「移植说明」，已经告诉 agent 这个路径指什么。

## 二、装依赖（每台机器一次）

两个技能装的是同一套：`requests` + 带静态 ffmpeg 的 `imageio-ffmpeg`。

```bash
cd ~/skills/asr-notes && bash scripts/setup.sh
cd ~/skills/tts-cast  && bash scripts/setup.sh    # 幂等，重复装没事
```

不想用脚本：`pip install requests imageio-ffmpeg`，或者系统装 ffmpeg。

两个脚本找 ffmpeg 的顺序都是 `$FFMPEG_BIN` → PATH → imageio-ffmpeg。
**imageio-ffmpeg 只有 ffmpeg，没有 ffprobe**——所以 `cast.py` 是用 `ffmpeg -i`
解析出时长的，不是调 ffprobe，别去"优化"成 ffprobe。

Windows 上不用 Git Bash 跑 `setup.sh`，直接用库根的工作流壳：

```powershell
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 setup
```

它会装 `requests`（没有 ffmpeg 时顺带装 `imageio-ffmpeg`）、生成 `config.env` 模板、
建好数据目录。详见 [../windows/README.md](../windows/README.md)。

## 三、配 Key（每台机器各配一份）

`config.env` 不在导出件里，手机那份也不会同步过来（`.stignore` 里明确排除了）。
在**数据目录**（`ASRTTS/` 根）建一个：

```bash
cp agent-skill/config.env.example config.env   # 然后填自己的一行 Key
```

Windows PowerShell：

```powershell
Copy-Item agent-skill\config.env.example config.env
```

或者不建文件，直接给环境变量 `DEEPSEEK_API_KEY`。

读取优先级：

| | |
|---|---|
| asr-notes | `--config` > `$ASR_CONFIG` > `./config.env` > `~/.config/asr-notes/env` > 环境变量 |
| tts-cast | `--config` > `$CAST_CONFIG` > `./config.env` > `../config.env` > `~/.config/asr-notes/env` > 环境变量 |

（cast 多查一层 `../config.env`，是它以前住在 `cast/` 子目录时留下的，无害。）

Key 在 https://platform.deepseek.com/api_keys 。**别提交进 git、别丢进网盘、别贴进聊天记录。**
手机和 PC 共用一个 Key 就共用一份账单，看花费时别算重。

## 四、跑起来

**Windows（推荐）**：用库根的壳，它负责切到数据目录、强制 UTF-8、把参数原样转发：

```powershell
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 check
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 process --dry-run
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 process --kind external
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 cast --dry-run
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 cast --preview 300
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 find 高尔夫 print
```

或者双击根目录的 `一键处理.cmd` / `一键开播.cmd` / `自检.cmd` / `ASRTTS.cmd`。
下面这些裸命令仍然可用，适合排查问题或在 Linux/macOS 上跑。

先 `cd` 到数据目录（两个脚本都把 `inbox/ out/ work/ archive/` 当相对当前目录）：

**录音 → 文稿**

```bash
python3 ~/skills/asr-notes/scripts/asr.py --check              # 验 Key 和 ffmpeg，不花钱
python3 ~/skills/asr-notes/scripts/asr.py --dry-run            # 看会处理哪些音频
python3 ~/skills/asr-notes/scripts/asr.py                      # 正式跑 inbox/
python3 ~/skills/asr-notes/scripts/asr.py --kind external      # 播客/讲座，换一套模板
python3 ~/skills/asr-notes/scripts/asr.py --srt 某期播客.srt    # 字幕文件，跳过转写
python3 ~/skills/asr-notes/scripts/asr.py --ai-only out/<文件夹> # 只重跑整理，不重转写
```

**文章 → 播客**

```bash
python3 ~/skills/tts-cast/scripts/cast.py --check              # 验 Key 和 ffmpeg，不联网
python3 ~/skills/tts-cast/scripts/cast.py --dry-run --verbose inbox/某篇.md
python3 ~/skills/tts-cast/scripts/cast.py --preview 300 inbox/某篇.md   # 试音，5 分钱
python3 ~/skills/tts-cast/scripts/cast.py inbox/某篇.md        # 出 m4a + srt + lrc
python3 ~/skills/tts-cast/scripts/cast.py --rewrite inbox/某篇.md
python3 ~/skills/tts-cast/scripts/cast.py --force out/<文件夹>/script.md   # 改稿重跑，0 元
```

`--force` **不重烧**，没改过的段从 `work/` 复用；要全部重烧才用 `--resynth`。
`out/<文件夹>/` 存在、但里面既没 m4a 也没 `index.md` 时不算做过（上次建完目录就断了），
脚本会自动重做，不必加 `--force`。
`--dry-run` 保证免费，连 `--rewrite` 都不会真发出去。

离线自检（不联网、不花钱，改完代码先跑这个）：

```bash
python3 ~/skills/tts-cast/scripts/selftest.py
```

自己写稿、要多篇合成一集时，烧音频前先量一遍（也不花钱）：

```bash
python3 ~/skills/tts-cast/scripts/check_draft.py 稿子.md 原文1.md 原文2.md
```

报时长（三十分钟上限，超了说多出多少字）、章节数、重复段落、超长句；退出码 1 就该改。
成稿送进 `cast.py` 时**不要加 `--spoken`**，那是二次改写。
写稿的组织方式见 `tts-cast/SKILL.md` 第八节。

`--spoken` 那条路还有两个开关：`--stop-at-outline` 只出大纲（零 TTS 费用），
`--outline 大纲.md` 复用改过的大纲只跑落地。两条路跑完都会把 `inbox/` 里的原件
搬进 `archive/`（`--no-archive` 关）；从 `out/…/script.md` 重跑不搬。

手机端库里有 `一键处理.sh`（音频 + 字幕）和 `一键开播.sh`（文章）；
Windows 上对应 `windows\asrtts.ps1 process` / `cast` 和根目录的 `.cmd`。
两边壳做的是同一件事：切目录、转发参数、最后列出产出。

参数细节、产出结构、成本护栏见各自的 `SKILL.md`，这里不重复。

## 五、和手机端的关系

- **手机端 `/skills/` 是源**，这份是快照。改了手机上的提示词或脚本，回手机跑一条
  `bash check-sync.sh --fix` 就同步过来了（逐文件比 md5，缺的补、改的覆盖，两个技能一起）。
  别手动 cp：漏一个新文件，PC 上就会 `ImportError`。
- **数据是同一份**：`ASRTTS/` 整个目录由 Syncthing 双向同步，PC 上跑出来的 `out/`
  手机上也看得到。
- **一次只在一台机器上跑**。两端同时跑会抢状态文件：asr 是 `.asr_state.json`，
  cast 是 `work/<期>/segments.json` 和 `meta.json`。
- **`work/` 在同步范围**：转写分片 `chunks.json` 是已付过的钱，合成分片 `s_*.wav`
  也是。换机器重跑会直接读缓存，不重新计费。
  `merged.wav`（一期十几 MB）和 `concat.txt` 不同步，它们每次都能重算。

## 六、PC 上可能遇到的差异

| 现象 | 原因 | 处理 |
|---|---|---|
| `无法加载文件 ... 因为在此系统上禁止运行脚本` | PowerShell 执行策略 | 双击 `.cmd`（已带 Bypass），或 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `bash: setup.sh: No such file` | Windows 没 Git Bash | 用 `windows\asrtts.ps1 setup`，或 `pip install requests imageio-ffmpeg` |
| 中文乱码 | 终端代码页 / `.ps1` 存成了 ANSI | 用 `.cmd` 启动；`.ps1` 必须是 UTF-8 with BOM |
| 写 `config.env` 后 Key 读不到 | 用 PS 5.1 的 `Set-Content -Encoding UTF8` 写入了 BOM | 用记事本/VSCode 存 UTF-8 无 BOM，或用 `[IO.File]::WriteAllText(..., UTF8Encoding($false))` |
| 路径里有空格报错 | Windows 路径带空格 | 命令里的路径加引号 |
| `--add` 写进 `00-Inbox/` 报错 | 当前目录不是 `ASRTTS/` | 用 `.cmd` 或先 `cd` 到数据目录 |
| cast 报"找不到 config.env" | 同上 | 用 `.cmd` 或先 `cd` 到数据目录 |
| `找不到 ffmpeg` / 缺 `libopus` | PATH 里没有完整构建 | `setup --with-ffmpeg`，或装 gyan.dev/BtbN 完整版 |
| 处理比手机快但上传久 | 切片快，瓶颈在带宽 | 正常，转写是串行的 |
| `merged.wav` 不见了 | `.stignore` 排除了它 | 正常，下次跑会自动重建 |
