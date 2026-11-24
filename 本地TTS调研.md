# 本地 TTS 可行性调研：ZONOS3-Mini 与 Kokoro-82M

> 2026-11-24 实测。机器：i7-14650HX（16C/24T）/ 32 GB RAM / RTX 5060 Laptop 8 GB（Blackwell，驱动 616.92，CUDA UMD 13.4）/ Windows 11。
> 目的：判断能不能把这台机器变成 ASRTTS 播客线的本地 TTS 后端，替代/兜底豆包与 DeepSeek。
> 结论先行：**能，而且余量很大。** 显存和内存都不是瓶颈，真正的成本是装环境和接管线。

---

## 一、结论

| 问题 | 答案 |
| --- | --- |
| 8 GB 显存够不够 | 够。ZONOS3-Mini 最小 GGUF 全套 **1.1 GB**（backbone 632 MB + 解码器 431 MB + 说话人编码器 43 MB）；Kokoro 只有 **330 MB** |
| 不插电/不用显卡行不行 | 行。两个模型都能纯 CPU 跑：Kokoro 约 **3.3× 实时**（高性能电源方案；平衡方案持续推理会掉到约 1×），ZONOS3 约 **2.2–2.8× 实时**；用显卡的话 Kokoro 能到 **14–21× 实时**（RTX 5060 实测） |
| 中文支持 | 都支持。ZONOS3 有 2 个普通话默认音色 + 60 秒参考音频克隆；Kokoro v1.0 有 8 个中文音色，v1.1-zh 有 103 个但会丢英文 |
| 英文支持 | 支持。Kokoro v1.1-zh 自带 3 个英文音色（`af_maple` / `af_sol` 美式女、`bf_vale` 英式女），v1.0 有 54 个；`zf_001` 也能直接读英文（同一音色跨语言）。中英混排要传 `en_callable`，否则英文被静默丢掉 |
| 今天能不能用上 | Kokoro：能，`pip install` 完就能出音频。ZONOS3：Windows 没有预编译包，要自己编（MSVC + CMake + Ninja + Rust，本次约 15 分钟编完） |
| 值不值得替掉云端 | 省钱是确定的（云端约 1.2 元/4000 字，本地 0 元）；听感要你亲自试听后再定，见 `samples/` |

---

## 二、两个模型是什么

### ZONOS3-Mini（Zyphra，2026-11-09 发布）

- 886M 总参数 / **100M 激活**的稀疏 MoE，11 层，Gated DeltaNet + 全局注意力；44.1 kHz 输出，15 Hz 帧率，ZAC-1 编解码器。
- 43 种语言（含普通话），零样本音色克隆（≤60 秒参考音频），可调响度/带宽/语速，另有情绪、口音、耳语等 steering。
- 权重 Apache-2.0，代码 MIT；官方称可在手机/笔记本实时运行：浏览器里 M3 MacBook Air 首包 <150 ms，Pixel 9 <570 ms；H100 上峰值 457 秒音频/秒。
- 27 个默认音色，普通话两个：`Haoran_zh-energetic_M_1`、`Meilin_zh-energetic_F_1`。
- 官方基准 ZTTS2-Eval：55 个语言子集宏平均 WER **6.71%**。
- 部署方式：SGLang-Omni（Linux + CUDA）、MLX（Apple Silicon）、**C++/ggml（CPU / CUDA / Metal / Vulkan）**、浏览器 WebGPU。
- Windows：仓库里有 `start-zonos3.bat/.ps1` 启动器，CI 用 MSVC + Vulkan SDK 构建并打包，但 **GitHub 上没有发布任何 release**，所以只能自己编译。

### Kokoro-82M（hexgrad，2025-01 发布）

- 82M 参数，StyleTTS2 + ISTFTNet 架构，**24 kHz** 输出，纯解码器，无扩散。
- v1.0：8 种语言 / 54 个音色（中文 4 女 4 男）；v1.1-zh：中文专用，103 个音色，但训练时丢了英文能力。
- 无音色克隆、无情绪控制；优点是极小、极快、CPU 就能跑，`pip install kokoro` 即可。
- Apache-2.0，商用无限制；社区部署极多。
- 注意：`kokoro` 0.9.4 要求 **Python < 3.13**（3.13 会被降到 0.7.4，中文支持不全）。

---

## 三、实测数据（本机，2026-11-24）

测试文本为同一段 101 字中文（`本地语音合成终于到了能用的地步……`），另有一段 303 字长文。RTF = 生成耗时 / 音频时长，越小越快。

| 引擎 | 后端 | 文本 | 字数 | 音频时长 | 生成耗时 | RTF |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Kokoro v1.0 `zm_yunxi` | CPU | 中文 | 101 | 15.85 s | 2.96 s | **0.187** |
| Kokoro v1.0 `zf_xiaobei` | CPU | 中文 | 101 | 20.12 s | 3.58 s | **0.178** |
| Kokoro v1.0 `af_heart` | CPU | 英文 | 254 | 16.32 s | 3.10 s | **0.190** |
| Kokoro v1.1-zh `zf_001` | CPU | 中文 | 101 | 19.20 s | 3.56 s | **0.185** |
| ZONOS3 `Meilin` | CPU（server） | 中文 | 101 | 15.87 s | 7.25 s | **0.457** |
| ZONOS3 `Meilin` | CPU（server） | 中文 | 303 | 49.73 s | 17.51 s | **0.352** |
| ZONOS3 `/v1/audio/speech` | CPU（server） | 中文 | 29 | 6.53 s | 4.29 s | 0.657* |
| Kokoro v1.1-zh `zf_001` | **GPU（RTX 5060）** | 中文 | 56 | 10.90 s | 0.69 s | **0.063** |
| Kokoro v1.1-zh `zf_001` | **GPU** | 中文 | 202† | 28.38 s | 1.34 s | **0.047** |
| 本地服务（HTTP） | **GPU** | 中英混排 | 82 | 10.20 s | 0.68 s | **0.067** |

† 202 字触发了 510 音素截断，RTF 只代表实际生成的那部分。

\* 含一次说话人编码和 HTTP 开销，短句摊薄后偏高；长文稳定在 0.35 左右。

> **电源方案的影响（重要）**：同一段文本，Windows「平衡」方案下 Kokoro 的 RTF 约 **1.08**（持续推理被限频），切到「高性能」后约 **0.30**。表里 0.18 是短时突发值；批量跑之前先切高性能，否则一篇 4000 字要等十几分钟。

补充观测：

- ZONOS3 服务端常驻内存 **2.08 GB**（峰值 2.19 GB），backbone 加载 0.58 GiB；Kokoro + torch 估算 1 GB 量级（未单独测）。
- 同一段 101 字，ZONOS3 与 Kokoro 生成的音频时长几乎一样（15.87 s vs 15.85 s），语速都正常。
- 磁盘：Kokoro 模型 330 MB + venv 约 1.2 GB（CPU 版 torch）；ZONOS3 仓库 178 MB + GGUF 1.1 GB + 构建产物约 0.34 GB。
- **GPU 已实测（Kokoro）**：装 `torch 2.14.1+cu130`（Windows 上 2.15 没有 CUDA 轮子）后，RTX 5060 Laptop 上 RTF **0.047–0.07**，约 14–21× 实时；显存占用约 1.1 GB。端到端 388 字 → 84 秒音频，总耗时从 CPU 的 25 秒降到 **8.1 秒**。ZONOS3 的 GPU 构建仍未做（Vulkan SDK 需要管理员权限）。

### 试听样本

样本已复制到 `ASRTTS\samples\本地TTS\`（工作文件仍在 `D:\dsh_dev\.tmp\tts-eval\`，见文末清理说明）：

| 文件 | 内容 |
| --- | --- |
| `ab_kokoro10_zm_yunxi.wav` | Kokoro v1.0 男声，同一句话 |
| `ab_kokoro11_zf_001.wav` | Kokoro v1.1-zh 女声，同一句话 |
| `ab_zonos3_meilin.wav` | ZONOS3 女声 Meilin，同一句话 |
| `zonos3_zh_long.wav` | ZONOS3 303 字长文（49.7 s） |
| `kokoro11_long.wav` | Kokoro v1.1-zh 303 字（**被截断**，28.4 s，用来听截断长什么样） |
| `kokoro_z_zm_yunxi.wav` / `kokoro11_zf_001.wav` | 101 字短段完整版 |

**Kokoro 的硬限制**：单次最多 **510 个音素**，超出部分直接截掉，不报错。中文实测约 **3.1 音素/字**，所以一段最多 **≈160 个汉字**；303 字那次只生成了 28.4 秒（应为约 56 秒），198 字那次只有 28.2 秒（应为约 37 秒），确认是截断。cast.py 现在按 600 字切段，接 Kokoro 必须把段再切到 150 字以内。英文约 1.03 音素/字符（254 字符 → 261 音素），所以英文段可以到 **≈450 字符**。

### 英文与中英混排（补充实测）

用户确认 `zf_001` 中文听感可用后，补测了英文与混排。结论：**英文需求现在就能满足**，但有三种接法，听感不同。

| 场景 | 管线 | 音色 | 实测（254 字符英文） | RTF |
| --- | --- | --- | ---: | ---: |
| 英文单独成篇（美式女） | `lang_code='a'`, v1.1-zh | `af_maple` | 14.43 s | **0.214** |
| 英文单独成篇（美式女） | `lang_code='a'`, v1.1-zh | `af_sol` | 15.95 s | 0.213 |
| 英文单独成篇（英式女） | `lang_code='b'`, v1.1-zh | `bf_vale` | 15.90 s | **0.179** |
| 英文用中文音色读 | `lang_code='a'`, v1.1-zh | `zf_001` | 15.28 s | **0.226** |
| 英文对照（v1.0） | `lang_code='a'`, v1.0 | `af_heart` | 16.32 s | 0.208 |

\* 首次调用含冷启动（实测 0.52），预热后都在 0.18–0.23。

中英混排的关键在 `en_callable`：中文 G2P 遇到拉丁词会调用它，把英文词转成英文音素。不传的话，英文位置直接变成 `❓`（未知 token），**静默丢掉**：

| 混排方式 | 音素数 | 音频时长 | 结果 |
| --- | ---: | ---: | --- |
| v1.1-zh + `en_callable` | 114 | 7.28 s | `API`→A-P-I、`GPU`→G-P-U、`pipeline`/`latency` 正常英文发音 |
| v1.1-zh 不传 | 95 | 6.28 s | 英文位置变 `❓`，被跳过 |
| v1.0 默认 | 117 | 7.58 s | 拉丁词保留为字母，行为与 v1.1 不同 |

官方 `make_zh.py` 的写法（本次实测有效）：

```python
en_g2p = KPipeline(lang_code='a', repo_id='hexgrad/Kokoro-82M-v1.1-zh', model=False)
def en_callable(text):
    return next(en_g2p(text)).phonemes
zh = KPipeline(lang_code='z', repo_id='hexgrad/Kokoro-82M-v1.1-zh', en_callable=en_callable)
```

三种接法的取舍：

1. **全用 `zf_001`**（中文 + 英文 + 混排）：音色完全统一，英文带一点中文口音；适合"中文为主、夹英文术语"的文章。
2. **按语言分段**：中文段 `zf_001`，英文段 `af_maple`；英文更地道，但音色会切换，适合"中文旁白 + 英文引文/术语表"。
3. **英文单独一期**：直接用 `af_maple` / `af_heart`（v1.0 的 54 个音色里挑），质量最好。

新增试听样本（`ASRTTS\samples\本地TTS\`）：

| 文件 | 内容 |
| --- | --- |
| `s_en11_af_maple.wav` | v1.1-zh 原生美式女声读英文 |
| `s_en11_zf001.wav` | `zf_001` 读同一段英文（同音色跨语言） |
| `s_en10_af_heart.wav` | v1.0 的 `af_heart` 读同一段英文（对照） |
| `s_mix_zf001.wav` | 中英混排：pipeline / latency / GPU / GB |
| `demo_episode.m4a` | 端到端成品：388 字文章 → 84 秒播客（8 段、23 条字幕、4 章节、0 元） |

---

## 四、装法（可复现）

### Kokoro（简单，推荐先试）

```powershell
# 必须 Python 3.12（3.13 装不上 kokoro 0.9.4）
py -3.12 -m venv D:\dsh_dev\.tmp\tts-eval\venv312
D:\dsh_dev\.tmp\tts-eval\venv312\Scripts\pip install kokoro==0.9.4 soundfile "misaki[zh]"
```

踩过的坑：

1. `kokoro` 依赖 `spacy-curated-transformers`，最新 2.0 要求 spacy 4.0 dev，会触发 blis 源码编译并失败。先装 `--only-binary=:all: "spacy>=3.8,<4" "spacy-curated-transformers<2"` 再装 kokoro。
2. 不要让它从源码编 numpy（会编出 MinGW 版，导入即警告）。先 `pip install --only-binary=:all: numpy torch`。
3. 中文不需要 espeak-ng；`misaki[zh]` 用 jieba + pypinyin。
4. 模型从 HF 自动下载（约 330 MB）。国内建议 `HTTPS_PROXY=http://127.0.0.1:7890`。

### ZONOS3-Mini（要编译）

前置：MSVC Build Tools（本机已有 14.50）、CMake、Ninja、Rust（`rustup`，本次装的是 1.100.0）。本次实际用的命令：

```powershell
git clone --depth 1 --recurse-submodules --shallow-submodules https://github.com/Zyphra/ZONOS3-Mini.git
cd ZONOS3-Mini\servers\ggml
# 在 vcvars64.bat 环境里：
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF
cmake --build build -j 24
```

模型（最小可用集，1.1 GB）：

```powershell
# backbone-ptq.gguf / zac-keep2.gguf / spk-encoder.gguf
curl -L -x http://127.0.0.1:7890 -O https://huggingface.co/Zyphra/ZONOS3-Mini/resolve/main/gguf/backbone-ptq.gguf
# ……另外两个同理
```

踩过的坑：

1. 构建默认产出共享库（`ggml.dll` 等），直接双击 exe 会报 `0xC0000135`。要么把 `build\bin` 加进 PATH，要么加 `-DBUILD_SHARED_LIBS=OFF` 编静态版。
2. **中文别走命令行参数**：PowerShell 5.1 把参数按 GBK 传给 exe，模型收到乱码，只生成 2 秒就 EOS。走 HTTP + UTF-8 JSON，或用 UTF-8 的请求文件。
3. `language` 用 **`cmn`**，不是 `zh_cn`。
4. 默认 `speaker_mode` 是 `reference`，需要额外 3.8 GB 的 `zac-encoder.gguf` + 1.5 GB 的 `ssl-teacher.gguf`；只用 43 MB 的 `spk-encoder.gguf` 时请求里要写 `"speaker_mode": "embedding"`。
5. 启动：`zonos3-server.exe gguf\backbone-ptq.gguf --port 1919 --cpu`，健康检查 `GET /v2/capabilities`。

---

## 五、接进 ASRTTS 播客线（已落地）

按最终选型（中文 `zf_001`、英文 `af_heart`）实现了本地服务，并端到端跑通了一期：

| 文件 | 作用 |
| --- | --- |
| `ASRTTS\tools\local-tts\server.py` | OpenAI 兼容 `/v1/audio/speech`；中文 / 混排走 v1.1-zh + `en_callable`，纯英文走 v1.0；自动切段；Kokoro 符号归一化 |
| `ASRTTS\tools\local-tts\start.cmd` | 双击启动（venv 在 `D:\dsh_dev\text-works\.local-tts\venv`，不在同步目录里） |
| `ASRTTS\agent-skill\tts-cast\scripts\cast.py` | 新增 `local` provider；`--check` 会 ping 服务；`_tts_local` 走 HTTP |
| `ASRTTS\windows\asrtts.ps1` | `cast --provider local` 自动探活并用 WMI 拉起服务；新增 `service start\|stop\|status` |

实测（388 字文章，4 个小标题，夹 API / GPU / latency 等英文词）：

```
计划：8 段 · 字数 388 · 预估 01:22 · 0.00 元
合成本次 8 段，跳过 0 段，本次计费字符 388
✅ .m4a  时长 01:24 · 23 条字幕 · 4 个章节 · 约 0.00 元
```

从开始合成到成品：CPU 高性能下 25 秒；切到 GPU 后 **8.1 秒**（含 ffmpeg 拼装），音频 84 秒，纯合成 RTF ≈ 0.07。

符号文章（`A→B`、`3≥2`、`3×4`、`3~5`、`±3`、`25℃`、`3000m²`、`120km/h`、`2026/11/20`、`-5℃`，
外加代码块 / 表格 / URL）走 `--provider local`：6 段、120 字 → 33 秒音频，合成 5.5 秒；
代码块、表格、URL 被读法层摘进 `原文附录.md`，符号在服务端换成读法，没有未知 token。

要点：

- **服务生命周期**：`cast --provider local` 先探 `/health`，没起就后台拉起（WMI，完全脱离控制台/管道），
  60 秒内就绪再合成；也可以 `service start|stop|status` 手动管。首次启动约 40–60 秒。
- **符号**：Kokoro 的中文 G2P 会丢掉 `→ ≥ × ~ ± # @ m² km/h`、斜杠日期、负号等，服务端
  `normalize_for_kokoro()` 先换读法；字幕 / `script.md` 仍按原文显示（刻意保留符号）。
- **读法层 / 原文附录**：与 provider 无关，代码块、表格、URL、`（原文：…）` 照旧摘出；
  `--spoken` / `--rewrite` / `--stop-at-outline` 也不受影响，本地只替换最后一步 TTS。
- **计费**：`price=0`，`index.md` 记 0.00，`--dry-run` 照常工作。
- **采样率**：服务端输出 24 kHz wav，`cast_render.py` 统一转 24 kHz AAC，不用改。
- **兜底**：服务没起且自动拉起失败时，cast 直接报错中止，不会静默失败。
- **编码**：走 `windows\asrtts.ps1` 时壳已设 `PYTHONUTF8=1`；直接在 GBK 控制台跑 cast.py 会在 ffmpeg 输出解码上炸，要用壳或自己设环境变量。
- **同步**：`agent-skill/tts-cast/scripts/cast.py` 是手机端技能的导出件；这次改了它，按项目约定要拷回手机 `/skills/tts-cast/`，否则下次导出会被覆盖。`windows/` 和 `tools/local-tts/` 是 PC 专用，同步到手机无害。

### 成本与速度对比（4000 字一篇，约 11 分钟音频）

| 方案 | 生成耗时 | 费用 | 备注 |
| --- | --- | --- | --- |
| 豆包 2.0（当前默认） | 约 1–2 分钟 | **1.2 元** | 170+ 音色，可调语速，可克隆（138 元/音色） |
| DeepSeek TTS | 约 1–2 分钟 | 1.2 元（五折期内） | 只有 2 个音色 |
| Kokoro（GPU） | 约 50 秒 | 0 | 需 cu130 torch；显存约 1.1 GB |
| Kokoro（CPU，高性能） | 约 3–4 分钟 | 0 | 服务端自动切段；无克隆 |
| Kokoro（CPU，平衡） | 约 12 分钟 | 0 | 电源方案限频，批量前先切高性能 |
| ZONOS3（CPU） | 约 3.8 分钟 | 0 | 可克隆；长段一次过 |
| ZONOS3（GPU，预估） | <1 分钟 | 0 | 未实测，需 Vulkan/CUDA 构建 |

电费可以忽略：CPU 跑 4 分钟约 0.004 度电。

---

## 六、风险与限制

- **ZONOS3 的 Windows 构建是“能编但非官方支持”**：CI 有 Windows Vulkan 任务，但没有任何预编译 release；换机器/升级驱动后可能要重编。Q4 量化的 backbone 在官方自测里 WER 11.1%，反而好于 F16 的 22.2%（六个无说话人提示的平均），但量化对中文的实际影响没有独立验证。
- **单次生成上限 10 分钟音频 / 32k 上下文**：cast.py 600 字一段约 1.5–2 分钟，安全。
- **Kokoro 的 510 音素截断**是静默的，服务端已按 150 字 / 450 字符切段；直接用 `KPipeline` 时必须自己切。
- **v1.1-zh 的英文**：不传 `en_callable` 会静默丢掉；传了之后缩写按字母读（API→A-P-I），适合中文里的术语，不适合整段英文——整段英文走 v1.0 的 `af_heart`。
- **听感必须人耳验收**：官方 WER/相似度不等于“适合通勤听”。两个模型都可能出现长句吞字、重复、语气平的问题，建议每期抽听 1–2 段。
- **GPU 路径没验证**：Vulkan SDK 安装需要管理员权限；CUDA 构建需要装 CUDA Toolkit（约 3 GB）。CPU 已经够用，显卡是锦上添花。

---

## 七、建议（已按此执行）

1. **中文 `zf_001`、英文 `af_heart`**（两个都经试听确认），中英混排走 `zf_001` + `en_callable`。
2. 已落地：`tools\local-tts\server.py` + `start.cmd`，`cast.py --provider local` 端到端跑通，388 字 → 84 秒音频、25 秒出成品、0 元。
3. 设备：服务启动自动选 CUDA；GPU 模式下电源方案影响很小，CPU 兜底时才需要切「高性能」。
4. 云端保留兜底；`--provider` 显式切换，互不冲突。
5. ZONOS3 暂不作为默认：它的价值在音色克隆和更强听感，等真需要克隆自己声音时再启用（这次编译的产物在临时目录，收尾时已清理；重编步骤见第四节）。
6. 如果要继续压时间：补 Vulkan/CUDA 构建（需要管理员装 SDK），把 Kokoro 的 CPU 推理换成 GPU。

---

## 八、附：本次实测环境

- Python 3.12.14（DSH 运行时）/ 3.13.17（pyenv）；torch 2.14.1+cu130（GPU）/ 2.15.0+cpu（旧）；kokoro 0.9.4；misaki 0.9.4。
- ZONOS3 仓库 commit `a50e141`，ggml 子模块 `a05c992`；MSVC 14.50.35717；CMake 4.4.5；Ninja 1.13.2；Rust 1.100.0。
- ffmpeg 2026-04-16 gyan build（系统 PATH）。
- 所有临时文件在 `D:\dsh_dev\.tmp\tts-eval\`，收尾时已清理（venv、模型、ZONOS3 构建产物、样本共约 4 GB）；长期保留的只有 `ASRTTS\tools\local-tts\`（代码）和 `D:\dsh_dev\text-works\.local-tts\venv`（1.2 GB 运行环境）。
