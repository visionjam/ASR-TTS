# ASRTTS

录音 → 文稿，文章 → 播客。

两条方向相反的管线，共用一个投递口、一个 API Key、一份账单：

- **asr-notes**：录音、口述、现成字幕 → DeepSeek ASR 转写 → 清洗 → 结构化 Markdown
- **tts-cast**：Markdown / txt → DeepSeek 或豆包 TTS → m4a + srt + lrc + 带时间戳的口播稿

这不是一个 App，是一个数据目录加两个技能（`SKILL.md` + Python 脚本）。手机上丢文件、发一句话，PC 上双击 `.cmd`，底下是同一套脚本。

> 这是个人工作流的导出快照，不是通用产品。手机端 `/skills/` 是源，本仓库是 PC 侧使用的副本；两个 `.sh` 加了 `agent-skill/` 的路径回退，clone 下来可以直接跑。

## 目录

```text
ASRTTS/
├─ inbox/            投递口：录音、字幕、文章
├─ out/              一条一个文件夹
│  ├─ 2026-11-18-xxx/
│  │  ├─ index.md        元数据卡片
│  │  ├─ transcript.md   逐字稿（ASR 原样）
│  │  ├─ cleaned.md      清洗稿
│  │  └─ notes.md        AI 整理稿
│  └─ daily/             碎片日结
├─ archive/          处理完的原件
├─ work/             切片缓存（重跑省钱，别删）
├─ 00-Inbox/         一句话的碎片
├─ agent-skill/      两个技能的代码与提示词
│  ├─ asr-notes/
│  └─ tts-cast/
├─ windows/          Windows 壳（asrtts.ps1）
├─ tests/            离线回归测试
├─ config.env        Key（不同步、不提交）
├─ 一键处理.sh/.cmd  录音 + 字幕
├─ 一键开播.sh/.cmd  文章 → 播客
├─ cast-find.sh      字幕反查
├─ check-sync.sh     手机技能与导出件比对
├─ 自检.cmd          环境检查
└─ ASRTTS.cmd        交互菜单
```

`out/`、`archive/`、`work/`、`logs/`、`00-Inbox/` 和 `config.env` 不在仓库里。

## 依赖

- Python 3.9+
- ffmpeg（需要 libopus、aac、loudnorm）
- `requests`；没有系统 ffmpeg 时用 `imageio-ffmpeg`
- 一个 DeepSeek API Key；TTS 可选豆包

## 安装

```bash
git clone https://github.com/visionjam/ASR-TTS.git
cd asrtts
pip install requests imageio-ffmpeg
cp config.env.example config.env
# 编辑 config.env，填 DEEPSEEK_API_KEY
```

自检：

```bash
python3 agent-skill/asr-notes/scripts/asr.py --check
```

Windows 上：

```powershell
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 setup
自检.cmd
```

## 用法

### 录音 → 文稿

把音频或字幕丢进 `inbox/`：

```bash
bash 一键处理.sh --dry-run          # 只列清单，不花钱
bash 一键处理.sh                    # 正式跑
bash 一键处理.sh --kind external    # 别人的播客 / 讲座
```

产出在 `out/<日期-标题>/`：`index.md`、`transcript.md`、`cleaned.md`、`notes.md`。

### 已经有字幕

```bash
python3 agent-skill/asr-notes/scripts/asr.py --srt inbox/某期.srt
```

字幕模式不调模型清洗，也不花转写费。

### 文章 → 播客

```bash
bash 一键开播.sh --dry-run          # 几段、多少字、多少钱
bash 一键开播.sh --preview 300      # 试音，约 5 分钱
bash 一键开播.sh                    # 正式跑
```

产出 `m4a`（内嵌章节）、`srt`、`lrc`、`script.md`、`index.md`。

### 碎片与日结

```bash
python3 agent-skill/asr-notes/scripts/asr.py --add "一句话的想法"
python3 agent-skill/asr-notes/scripts/asr.py --daily
```

### 反查

```bash
bash cast-find.sh 关键词 print
bash cast-find.sh 关键词 play 2
```

## 成本

| 动作 | 花费 |
|---|---|
| 转写 | ¥0.39/小时（限时五折，刊例 ¥0.78） |
| 整理 | 约 ¥0.05/万字 |
| 文章转播客（DeepSeek） | 约 ¥1.2/4000 字 |
| 豆包 TTS | ¥3/万计费字符（1 汉字 = 1 字符） |
| 试音 300 字 | 约 ¥0.05 |
| 改稿重跑 | 没改过的段复用，0 元 |
| `--dry-run` / 自检 / 离线测试 | 0 |

价格会变，以官方为准。`index.md` 里同时记「本次」和「累计」。

## Windows

双击 `一键处理.cmd` / `一键开播.cmd` / `自检.cmd`，或：

```powershell
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 process --dry-run
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 cast --preview 300
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 check
```

壳只做三件事：定位目录、强制 UTF-8、转发参数。详见 [windows/README.md](windows/README.md)。

## 跨设备（可选）

手机和 PC 用 Syncthing 同步同一个目录。两条规则：

- `work/` 要同步：`chunks.json` 和分段 wav 是已经付过钱的结果
- `config.env` 不同步：里面有 Key

一次只在一台机器上跑。

## 已知限制

- 没有句子级时间戳：`### mm:ss` 是分片起点，约两分钟一个（字幕模式例外）
- 不支持流式，必须整片上传
- DeepSeek TTS 不能调语速
- 内容审核不过的段会跳过，记在 `index.md`
- AI 整理稿仍需人读一遍
- 抓字幕不归这里管，用 yt-dlp

## 许可

MIT。第三方服务（DeepSeek ASR/TTS、豆包 TTS）与 ffmpeg 各自遵循其条款。
