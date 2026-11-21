# Windows 工作流

这套东西只做一件事：**把手机端已有的 `asr-notes` / `tts-cast` 技能包在 Windows 上跑顺**。
业务逻辑仍然在 `agent-skill/` 里，这里只解决 Windows 特有的麻烦：找 Python、找 ffmpeg、
UTF-8 控制台编码、路径含中文/空格、把参数原样转给脚本。

手机端 `/skills/` 仍是源；`agent-skill/` 是导出快照。**不要在这里改业务逻辑**，
改了要拷回手机，否则下次导出会被覆盖。

---

## 一、目录

```
ASRTTS/
├─ ASRTTS.cmd            双击：交互菜单（处理 / 开播 / 自检 / 反查 / 测试 / 安装）
├─ 一键处理.cmd          双击：处理 inbox\ 里的录音和字幕
├─ 一键开播.cmd          双击：把 inbox\ 里的文章转成播客（会计费）
├─ 自检.cmd              双击：检查 Python / ffmpeg / Key / 目录 / 技能
└─ windows/
   ├─ asrtts.ps1         主脚本（所有命令都在这里）
   └─ README.md          本文件
```

数据目录（`inbox/ out/ archive/ work/ 00-Inbox/ config.env`）就是 `ASRTTS/` 根，
和手机端通过 Syncthing 同步的是同一份。**同一时间只在一台机器上跑**：
两端同时跑会抢 `.asr_state.json`、`work/*/segments.json`。

---

## 二、第一次装（每台 Windows 一次）

### 1. Python

要 3.9+（示例用 3.14）。在 PowerShell 里确认：

```powershell
python -V
```

没有就装 Python 3.12/3.13/3.14，安装时勾选 **Add python.exe to PATH**。
不想改 PATH 也可以设环境变量 `ASRTTS_PYTHON` 指向 `python.exe`。

### 2. ffmpeg

`自检.cmd` 会检查三样东西：`libopus`、`aac`、`loudnorm`。
ASR 切片用 `libopus`，播客成品用 `aac`，响度归一化用 `loudnorm`。

如果 PATH 里已经有完整构建（gyan.dev full build 之类），可以直接用。
注意它跟着那个 Python 安装走：删/换 Python313 会一起没了。想稳妥就装系统 ffmpeg，
或用 `FFMPEG_BIN` 指向一个固定位置的 `ffmpeg.exe`。换机器时：

```powershell
# 方案 A：系统装 ffmpeg（winget 有网时）
winget install Gyan.FFmpeg

# 方案 B：让 Python 装一个自带静态 ffmpeg 的包
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 setup --with-ffmpeg
```

两个脚本找 ffmpeg 的顺序都是 `FFMPEG_BIN` → PATH → `imageio-ffmpeg`。
**imageio-ffmpeg 只有 ffmpeg，没有 ffprobe**；`cast.py` 用 `ffmpeg -i` 读时长，不依赖 ffprobe。

### 3. Python 依赖 + config.env

```powershell
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 setup
```

它会：

- 用清华镜像装/升级 `requests`；没有 ffmpeg 时顺带装 `imageio-ffmpeg`
- 缺 `config.env` 就从 `config.env.example` 复制一份（不会覆盖已有文件）
- 建好 `inbox/ out/ archive/ work/ logs/`

然后编辑 `config.env`，填一行：

```
DEEPSEEK_API_KEY=sk-...
```

Key 和手机端共用同一个，账单也是同一份。**不要把 Key 贴进聊天、提交 git 或放进网盘。**
`.stignore` 已排除 `/config.env`；换机器时重新填一份，不要靠同步。

### 4. 自检

```powershell
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 check
# 或者双击 自检.cmd
```

会检查 Python、requests、ffmpeg 编码器、Key、技能目录、数据目录、`.stignore`、
Syncthing 状态，最后调一次 DeepSeek 的模型列表接口（不产生转写/合成费用）。
只想离线检查就加 `--offline`。

### 5. Syncthing（可选，但推荐）

PC 端 Syncthing 把 `<ASRTTS>` 和手机
`/sdcard/ASRTTS` 配对。界面在 `http://127.0.0.1:8384`。

规则和排查见 [../同步说明.md](../同步说明.md)。Windows 这边只要确认：

- 文件夹根就是 `ASRTTS` 这一层，不是它的上一层目录
- `.stignore` 是文件名，不是 `.stignore.txt`
- `work/` 要同步（`chunks.json` / `s_*.wav` 是已经付过钱的结果），`config.env` 和
  `logs/` 不同步
- 换机器/重装时 `.stignore` 从库里的 `stignore.template` 复制（`Copy-Item stignore.template .stignore`），
  别重新敲；核对用 同步说明.md 第三节那段 digest（字节不必相同，Windows 上是 CRLF）

> 2026-11-18 把 `.stignore` 里「整个 `/work/` 排除」改成只排除
> `error.log` / `merged.wav` / `concat.txt` / `chapters.txt`。**两端都已落地**：
> PC 端改完，手机端同日核对过（24 条生效规则）。换机器重装 Syncthing 时，
> 照 [../同步说明.md](../同步说明.md) 第三节抄一遍，再用 `check` 核三行。

---

## 三、日常三条命令

### 录音 → 文稿

1. 录音（`.m4a/.mp3/.wav/...`）或字幕（`.srt/.vtt/.ass`）丢进 `inbox\`
2. 双击 `一键处理.cmd`
3. 看 `out\` 里新出的文件夹

双击时默认按「自己口述」(`self`) 整理。别人的播客/讲座用菜单选 2，或命令行：

```powershell
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 process --kind external
```

常用参数（原样转给 `asr.py`）：

| 参数 | 作用 |
|---|---|
| `--dry-run` | 只列清单 / 解析字幕，不调 API、不写文件 |
| `--kind self\|external` | 自己的口述 / 别人的音频 |
| `--asr-only` | 只转写，不整理 |
| `--ai-only out\<文件夹>` | 只重跑 AI 整理，不重转写（不扫 inbox） |
| `--reclean` | 配合 `--ai-only`，忽略 `cleaned.md` 重新清洗 |
| `--force` | 重跑已处理过的 |
| `--no-archive` | 不把源文件移到 `archive/` |
| `--srt 某期.srt` | 字幕直接整理，不转写（不扫 inbox） |
| `--text note.txt` | 输入法打的一段话，跳过转写（不扫 inbox） |
| `--add "想法"` | 往 `00-Inbox/` 今天的碎片笔记追加一条（不扫 inbox） |
| `--daily` | 聚合当天碎片，出 `out/daily/YYYY-MM-DD-日结.md`（不扫 inbox） |

`--ai-only` / `--srt` / `--text` / `--add` / `--daily` 是「直接调用」模式，
壳不会先扫 `inbox\`，参数原样交给 `asr.py`。注意 `--ai-only` 不认 `--dry-run`：
它会真的调整理模型，跑之前确认文件夹没选错。

一批里同时有音频和字幕时，`--kind` **只套给音频**，字幕默认按 `external` 整理——
这是手机端 `一键处理.sh` 就有的规则，Windows 壳照搬。要让字幕也走 `self`，
把字幕单独放一批再跑。

### 文章 → 播客

1. 文章（`.md/.txt`）丢进 `inbox\`
2. 双击 `一键开播.cmd`（正式跑，**会计费**；双击时会先问一次 Y/N）
3. 产出在 `out\<日期-标题>\`：`.m4a` + `.srt` + `.lrc` + `script.md` + `index.md`

第一次做某篇，建议先估计划、再试音、最后整篇：

```powershell
# 免费：几段、多少字、多少钱、多长
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 cast --dry-run

# 约 5 分钱：只读前 300 字，产物在 out\<日期-标题>-试音\
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 cast --preview 300

# 正式跑
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 cast
```

`--force` 只重做成品，没改过的段从 `work/` 复用，**不重复计费**；
要全部重烧才用 `--resynth`。改稿后从 `script.md` 重跑：

```powershell
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 cast --force "out\2026-11-18-某篇\script.md"
```

### 字幕反查：记住一句话，找回那一刻

```powershell
# 只打印时间戳，不播
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 find 高尔夫 print

# 从第一条命中开始播（有 mpv/vlc 就用它们跳转）
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 find 高尔夫 play
```

本机没装 mpv/vlc 时，脚本会：

- 把时间戳复制到剪贴板
- 用系统默认播放器打开同名 `.m4a`
- 提示你手动跳到那个时间

想装播放器：`winget install mpv` 或 `winget install VideoLAN.VLC`。
mpv 认同名 `.srt`；VLC 对纯音频默认不建视频窗口，字幕画不出来，属正常。

---

## 四、命令速查

下面的命令默认在 `ASRTTS\` 根目录执行；不在根目录就把 `windows\asrtts.ps1`
换成完整路径。壳会自己切到数据目录，所以 `--ai-only out\...` 这类相对路径仍然有效。

所有命令都可以用两种写法：

```powershell
# 直接调脚本（推荐，参数完整）
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 <命令> [参数]

# 双击 .cmd（等价，只是帮你加了 -ExecutionPolicy Bypass 和暂停）
一键处理.cmd [参数]
一键开播.cmd [参数]
自检.cmd
ASRTTS.cmd
```

| 命令 | 作用 |
|---|---|
| `process [参数]` | 处理 `inbox\` 音频 + 字幕，参数转给 `asr.py` |
| `cast [参数]` | 文章转播客，参数转给 `cast.py` |
| `find 关键词 [print\|play] [N]` | 在 `out\` 的 `.srt` 里反查 |
| `check [--offline]` | 自检 |
| `test` | 离线回归（`t_core` + `t_subtitle` + `selftest`） |
| `setup [--with-ffmpeg]` | 装依赖、生成 `config.env` 模板 |
| `menu` | 交互菜单 |

环境变量（可选）：

| 变量 | 用途 |
|---|---|
| `ASRTTS_ROOT` | 数据目录；默认从脚本位置向上找 |
| `ASR_SKILL` / `CAST_SKILL` | 技能目录；默认 `agent-skill\<名字>` |
| `ASRTTS_PYTHON` | 指定 `python.exe` |
| `FFMPEG_BIN` | 指定 `ffmpeg.exe` |

---

## 五、Windows 和手机端的差异

| 事 | 手机端 | Windows |
|---|---|---|
| 壳 | `一键处理.sh` / `一键开播.sh` | `一键处理.cmd` / `一键开播.cmd` + `windows\asrtts.ps1` |
| Shell | bash | Windows PowerShell 5.1（不要用 `&&`、三元表达式） |
| 编码 | UTF-8 | 脚本带 BOM；子进程强制 `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` |
| 播放器 | Pixel Player 等认 `.lrc` | mpv 认 `.srt`；没装播放器就默认程序 + 剪贴板时间戳 |
| 路径 | `/sdcard/...` | `<ASRTTS>\...`，含中文/空格要加引号 |
| 执行策略 | 无 | `.cmd` 已带 `-ExecutionPolicy Bypass`；直接跑 `.ps1` 可能被拦 |

**不要用 PowerShell 5.1 的 `Set-Content -Encoding UTF8` 去写 `config.env`、
`concat.txt` 这类给 Python/ffmpeg 读的文件**：PS 5.1 会加 BOM，ffmpeg 会把 BOM 当成
`unknown keyword '﻿file'`，Python 读 `config.env` 时第一个 Key 名也会带 BOM。
需要写无 BOM 文件时用：

```powershell
[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
```

`windows\asrtts.ps1` 本身是 UTF-8 **with BOM**（PowerShell 5.1 才认中文）；
不要用编辑器把它另存成 ANSI/GBK。

---

## 六、出问题先看这里

| 现象 | 原因 / 处理 |
|---|---|
| `无法加载文件 ... 因为在此系统上禁止运行脚本` | 双击 `.cmd`（已 Bypass）；或 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| 中文乱码 | 用 `.cmd` 启动；不要用 ANSI 保存 `.ps1`；`chcp 65001` 只影响当前控制台 |
| `找不到 Python` | 装 Python 并勾 PATH，或设 `ASRTTS_PYTHON` |
| `找不到 ffmpeg` | `setup --with-ffmpeg`，或装完整 ffmpeg 后重开终端 |
| ffmpeg 缺 `libopus` / `loudnorm` | 换完整构建（gyan.dev / BtbN），不要用精简版 |
| `401` | Key 错/过期；`check` 会直接报 |
| `400 unsupported audio container` | 音频里混了封面图/第二轨；脚本已加 `-vn`，自己调 ffmpeg 时别忘 |
| `413` | 单片超 8MiB；降码率或缩短 `CHUNK_MAX` |
| `config.env` 找不到 | 用 `.cmd` 或先 `cd` 到 `ASRTTS\`；壳会自动切目录 |
| 处理失败 | 看 `work\error.log`；壳的退出码非 0 时也会提示 |
| `merged.wav` 不见了 | `.stignore` 排除了它；下次跑会自动重建 |
| 路径太长 | 开启 Windows 长路径（`LongPathsEnabled=1`），或给文章 `--slug` 起短名 |
| 自检提示「.stignore 把整个 work/ 排除了」 | 删掉 `.stignore` 里的 `(?d)/work/` 一行；只排除 `work/error.log`、`merged.wav`、`concat.txt`、`chapters.txt` |
| 出现 `*.sync-conflict-*` | 两端同时改过；先暂停 PC 端 Syncthing，确认内容后再删 |
| 两端同时跑 | 会抢状态文件；一次只在一台机器上跑 |

更细的 ASR / TTS 报错对照在：

- `agent-skill/asr-notes/SKILL.md` 第八节
- `agent-skill/tts-cast/SKILL.md` 第五节
- `../README.md`、`../播客说明.md`

---

## 七、成本护栏

| 动作 | 花钱吗 |
|---|---|
| `process --dry-run` | 不花 |
| `process` | 转写 ¥0.39/小时（限时五折）+ 整理约 ¥0.05/万字 |
| `cast --dry-run` | 不花（连 `--rewrite` 也不发） |
| `cast --preview 300` | 约 5 分钱 |
| `cast` | 约 1.2 元/4000 字（DeepSeek 五折价） |
| `cast --force` | 没改过的段复用，0 元；改过的段按字数 |
| `cast --resynth` | 全部重烧，整篇计费 |
| `check` | 不花（只查模型列表） |
| `test` | 不花（全离线） |

单次超过 ¥1 先停一下，确认再跑。`index.md` 里同时记「本次」和「累计」。

---

## 八、维护

- **手机端 `/skills/` 是源**。改 `asr.py` / `cast.py` / 提示词，先在手机改，再导出到
  `agent-skill/`。PC 上临时改了，记得拷回手机，否则下次导出被覆盖。
- 手机端有 `check-sync.sh` 逐文件比 md5；PC 上不需要跑它，因为 `agent-skill/` 就是
  本机实际使用的副本。
- `windows/` 和 `.cmd` 是 PC 专用壳，同步到手机也无害；手机不会执行它们。
- 不提交：`config.env`、`archive/`、`out/`、`work/`、`logs/`。
- 改完壳先跑 `test`，再跑 `check --offline`，最后拿一条短录音 `process --dry-run` 验参数。
