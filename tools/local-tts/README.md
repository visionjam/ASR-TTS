# 本地 Kokoro TTS 服务

给 ASRTTS 播客线用的本地合成后端：`cast.py --provider local` 调它，费用 0。

- 中文 / 中英混排：`Kokoro-82M-v1.1-zh` · `zf_001`（英文词由 `en_callable` 转音素，不会被丢）
- 纯英文：`Kokoro-82M`（v1.0）· `af_heart`
- 接口：OpenAI 兼容 `POST /v1/audio/speech`，24 kHz 16-bit wav
- 速度：GPU 约 **14× 实时**（RTX 5060 Laptop，RTF 0.07）；纯 CPU 高性能约 3.3×、平衡约 1×
- 设备：启动时自动选，有 CUDA 用 GPU，否则 CPU（`--device cpu|cuda|auto`）

## 安装（只需一次）

venv 放在 ASRTTS 外面，避免 Syncthing 把 1 GB 的环境同步到手机：

```powershell
# Python 3.12（kokoro 0.9.4 不支持 3.13）
& "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" `
  -m venv D:\dsh_dev\text-works\.local-tts\venv
$v = D:\dsh_dev\text-works\.local-tts\venv\Scripts\python.exe
& $v -m pip install --only-binary=:all: numpy "spacy>=3.8,<4" "spacy-curated-transformers<2"
& $v -m pip install "kokoro==0.9.4" soundfile "misaki[zh]"
```

然后按下面「性能」一节装 GPU 版 torch（不装也能跑，只是走 CPU）。

模型首次运行自动从 Hugging Face 下载（约 660 MB，两个模型），之后离线可用。

## 启动

平时**不用手动启动**：`windows\asrtts.ps1 cast --provider local` 会先探 `/health`，
没起就用 WMI 在后台拉起（日志写 `..\..\..\.local-tts\server.out.log` / `server.err.log`），
等就绪后再合成。手动管理用壳的命令：

```powershell
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 service status
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 service start
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 service stop
```

也可以直接双击 `start.cmd`（前台窗口，看日志方便）。首次启动约 40–60 秒
（加载两个模型 + CUDA 预热），之后常驻。

## 用

```powershell
# 健康检查
curl http://127.0.0.1:1919/health

# 合成
curl -X POST http://127.0.0.1:1919/v1/audio/speech `
  -H "Content-Type: application/json" `
  -d "{\"model\":\"kokoro\",\"input\":\"你好，world。\",\"voice\":\"auto\",\"response_format\":\"wav\"}" `
  -o out.wav
```

`voice`：`auto`（默认，按文本选）/ `zh` / `en` / `zf_001` / `af_heart`。

接进播客线：

```powershell
cd D:\dsh_dev\text-works\ASRTTS
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 cast --provider local --dry-run
powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 cast --provider local
```

## 符号处理（Kokoro 特有）

Kokoro 的中文 G2P 会把 `→ ≥ ≤ & × ~ ±`、`m² km/h`、`2026/11/20`、`-5`、`#1` 这类
写法直接丢掉（音素里变成未知 token），和豆包不一样。服务端在合成前有一层
`normalize_for_kokoro()`，把它们换成读法（到 / 大于等于 / 乘 / 正负 / 平方米 /
公里每小时 / 年月日 / 负 / 第），只改读法、不改句子。字幕和 `script.md` 仍按原文
显示——这是刻意的，字幕里保留符号更好认。

读法层（`verbatim.py`）仍然是第一道：代码块、表格、URL、`（原文：…）` 会在
进 TTS 前摘到 `原文附录.md`，这一层和 provider 无关。

## 配置（config.env 或环境变量）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `LOCAL_TTS_URL` | `http://127.0.0.1:1919` | 服务地址 |
| `LOCAL_VOICE` | `auto` | 默认音色；`auto` 按语言选 |
| `LOCAL_TTS_SPEED` | `1.0` | 语速；`auto` 用官方长句减速曲线 |
| `LOCAL_TTS_VOICE_ZH` | `zf_001` | 中文音色 |
| `LOCAL_TTS_VOICE_EN` | `af_heart` | 英文音色 |

## 性能（GPU / CPU）

启动时自动选设备：`torch.cuda.is_available()` 为真就用 GPU，否则 CPU。也可以显式 `--device cpu` 或 `--device cuda`。

| 设备 | 实测 RTF | 4000 字一篇（约 11 分钟音频） |
| --- | ---: | ---: |
| RTX 5060 Laptop（GPU） | 约 0.07 | 约 50 秒 |
| CPU · 高性能 | 约 0.29 | 约 3–4 分钟 |
| CPU · 平衡 | 约 1.08 | 约 12 分钟 |

显存占用约 1.1 GB（两个 82M 模型 + 激活），8 GB 卡绰绰有余。

GPU 版 torch 的装法（Windows 上 2.15 只有 CPU 轮子，要用 2.14.1+cu130）：

```powershell
# 约 1.9 GB；文件名必须是标准 wheel 名，否则 pip 报 not a valid wheel filename
curl -L -o torch-2.14.1+cu130-cp312-cp312-win_amd64.whl `
  https://download.pytorch.org/whl/cu130/torch-2.14.1%2Bcu130-cp312-cp312-win_amd64.whl
D:\dsh_dev\text-works\.local-tts\venv\Scripts\pip install --no-deps --force-reinstall .\torch-2.14.1+cu130-cp312-cp312-win_amd64.whl
```

CUDA 版 torch 也能跑 CPU（`--device cpu`），所以不用装两套。

CPU 模式仍然受电源方案影响，批量跑之前切高性能；GPU 模式影响很小。

## 已知限制

- Kokoro 单次 510 音素，服务端自动切段：中文 ≤150 字、英文 ≤450 字符。切点优先落在句末。
- 无音色克隆、无情绪控制；音色库见 `kokoro` 文档。
- 服务是单进程 + 一把锁，并发请求会排队；播客是串行合成，够用。
- 切段之间是直接拼接（无额外静音），段内韵律连续；如果觉得段间太赶，可以在 `server.py` 里给 `np.concatenate` 之间插 0.05–0.1 秒静音。

## 文件

| 文件 | 用途 |
| --- | --- |
| `server.py` | HTTP 服务 + 语言路由 + 切段 |
| `start.cmd` | 双击启动（找 `D:\dsh_dev\text-works\.local-tts\venv`） |
| `README.md` | 本文件 |
