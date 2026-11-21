import os, sys
from pathlib import Path

# 技能目录：手机端在 /skills/asr-notes，PC 上用随库同步的导出件。
# 都不对就用环境变量 ASR_SKILL 指过去——测试不跟你猜路径。
_HERE = Path(__file__).resolve().parent
_VAULT = _HERE.parent
SKILL = Path(os.environ.get("ASR_SKILL") or next(
    (p for p in (Path("/skills/asr-notes"), _VAULT / "agent-skill/asr-notes")
     if (p / "scripts/asr.py").is_file()), "/skills/asr-notes"))
import importlib.util, re, tempfile, json
from types import SimpleNamespace

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

sub = load("subtitle", str(SKILL / "scripts/subtitle.py"))
asr = load("asr", str(SKILL / "scripts/asr.py"))

ok = True
def chk(name, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + str(extra)) if extra else ""))

T = Path(tempfile.mkdtemp())

# ---------- 1. 普通中文 srt ----------
srt = T / "普通中文.srt"
srt.write_text("""1
00:00:01,000 --> 00:00:04,000
今天我们聊一个问题。

2
00:00:04,100 --> 00:00:07,500
就是字幕文件能不能直接变成文稿。

3
00:00:07,600 --> 00:00:11,000
我的答案是能，而且不用重新转写。

4
00:00:20,000 --> 00:00:24,000
停顿超过一秒半，这里应该另起一段。

5
00:00:24,100 --> 00:00:27,000
<i>斜体标签</i>和 &amp; 实体都要清掉。

6
00:00:27,100 --> 00:00:30,000
最后一句没有句号
""", encoding="utf-8")

print("== 1. 解析与分段 ==")
cues = sub.parse(srt)
chk("6 条 cue", len(cues) == 6, len(cues))
chk("时间轴解析正确", cues[0][0] == 1.0 and abs(cues[3][0] - 20.0) < 1e-6, cues[0][:2])
chk("html 标签与实体已清", "斜体标签" in cues[4][2] and "&amp;" not in cues[4][2], cues[4][2])
chunks = sub.to_chunks(cues)
print("   分块:", [(round(s), t[:14] + "…") for s, d, t in chunks])
chk("停顿处切块（9 秒空档）", len(chunks) == 2, [(s, t[:10]) for s, d, t in chunks])
chk("段落长度合理（≤ 目标字数）", all(len(t) <= 560 for _, _, t in chunks), [len(t) for _, _, t in chunks])
chk("分块时间戳单调", all(chunks[i][0] < chunks[i+1][0] for i in range(len(chunks)-1)))
chk("分块不丢字", re.sub(r"\s", "", "".join(t for _, _, t in chunks)) ==
    re.sub(r"\s", "", "".join(t for _, _, t in cues)), "字数一致")
chk("时长合理", all(d > 0 for _, d, _ in chunks), [d for _, d, _ in chunks])

# ---------- 2. YouTube 滚动自动字幕 ----------
print("== 2. 滚动重复去重 ==")
yt = T / "rolling.srt"
yt.write_text("""1
00:00:00,000 --> 00:00:02,000
so today we are

2
00:00:02,000 --> 00:00:04,000
so today we are going to talk

3
00:00:04,000 --> 00:00:06,000
going to talk about subtitles

4
00:00:06,000 --> 00:00:08,000
about subtitles and transcripts.
""", encoding="utf-8")
raw = sub._parse_blocks(yt.read_text(encoding="utf-8"))
ded = sub.dedupe(raw)
joined = " ".join(t for _, _, t in ded)
print("   去重后:", joined)
chk("无重复片段", joined.count("so today we are") == 1 and joined.count("going to talk") == 1)
chk("保留全部新内容",
    all(w in joined for w in ("so today we are", "going to talk", "about subtitles", "transcripts.")))
chk("去重后没有重复内容", len(joined) < sum(len(t) for _, _, t in raw) and
    joined == "so today we are going to talk about subtitles and transcripts.", len(joined))

# ---------- 3. WebVTT ----------
print("== 3. VTT（WEBVTT 头 / NOTE / 省略小时）==")
vtt = T / "a.vtt"
vtt.write_text("""WEBVTT

NOTE 这是注释块
不该出现在正文里

cue-1
00:01.000 --> 00:03.000 align:start position:0%
第一句。

00:03.500 --> 00:05.000
第二句。
""", encoding="utf-8")
vc = sub.parse(vtt)
chk("跳过 NOTE 与 cue id", len(vc) == 2 and vc[0][0] == 1.0, [(round(c[0],1), c[2]) for c in vc])
chk("NOTE 内容没混进来", "注释" not in "".join(c[2] for c in vc))

# ---------- 4. ASS ----------
print("== 4. ASS（正文含逗号）==")
ass = T / "a.ass"
ass.write_text("""[Script Info]
Title: t

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,第一条，这里有逗号，也有\\N换行
Dialogue: 0,0:00:04.50,0:00:08.00,Default,,0,0,0,,{\\i1}第二条带样式{/i1}
""", encoding="utf-8")
ac = sub.parse(ass)
chk("2 条 dialogue", len(ac) == 2, len(ac))
chk("逗号与换行处理正确", ac[0][2].count("，") == 2 and "\n" not in ac[0][2] and "也有换行" in ac[0][2], ac[0][2])
chk("ass 内联标签已清", "{" not in ac[1][2] and "第二条带样式" in ac[1][2], ac[1][2])

# ---------- 5. asr.py --srt 全链路（chat 打桩，不花钱）----------
print("== 5. from_subtitle 全链路（离线）==")
work = T / "run"; work.mkdir()
outdir = work / "out"; (work / "archive").mkdir()
asr.ARGS = SimpleNamespace(
    inbox=work, out=outdir, archive=work / "archive", work=work / "work",
    state=work / ".asr_state.json", no_archive=True, force=False,
    dry_run=False, reclean=False, no_llm=False,
    kind=None, prompts="", notes_dir=work / "00-Inbox", files=[], srt=None)
asr.CFG = {"LLM_MODEL": "deepseek-chat", "DEEPSEEK_API_KEY": "stub",
           "LLM_BASE": "https://api.deepseek.com/v1"}
asr.chat = lambda system, user, max_tokens=6000: (
    "## 这期在讲什么\n字幕文件可以直接变成文稿。\n\n"
    "## 关键论点\n"
    "- 他认为字幕能直接进管线（原文：\"我的答案是能，而且不用重新转写\"）\n"
    "- 他提到停顿处要分段（原文：\"这里应该另起一段\"）\n\n"
    "## 论据与数据\n- 无\n\n## 广告与赞助\n- 无\n\n"
    "## 值得回听的段落\n- [00:00] 开场\n\n## 提到的人 / 书 / 事\n- 无\n\n## 我的标注\n")
folder, cost = asr.from_subtitle(srt, {})
files = sorted(p.name for p in folder.iterdir())
print("   产出:", files)
chk("四个文件齐全", files == ["cleaned.md", "index.md", "notes.md", "transcript.md"], files)
idx = (folder / "index.md").read_text(encoding="utf-8")
chk("index 标记为字幕来源", "| 来源 | 普通中文.srt" in idx and "input: subtitle" in idx)
chk("index 显示无转写费", "未走 ASR" in idx)
cl = (folder / "cleaned.md").read_text(encoding="utf-8")
chk("cleaned 未标模型清洗", "未清洗" in cl)
chk("cleaned 带 mm:ss 小节", re.search(r"^### 00:0", cl, flags=re.M) is not None)
nt = (folder / "notes.md").read_text(encoding="utf-8")
chk("摘引被核对并标 [原话]", nt.count("[原话]") == 2 and "[归纳]" not in nt, nt.count("[原话]"))
tr = (folder / "transcript.md").read_text(encoding="utf-8")
chk("transcript 是字幕原文（去重后）", "斜体标签" in tr and "asr_cost_cny: 0" in tr)
chk("state 记录零成本", all(v["cost"] == 0.0 for v in json.loads((work/".asr_state.json").read_text()).values()))
chk("重复跑会跳过", asr.from_subtitle(srt, {}) == (None, 0.0))
asr.ARGS.force = True
chk("--force 可重跑", asr.from_subtitle(srt, {})[0] is not None)

print("\n结论:", "全部通过" if ok else "有失败项")
sys.exit(0 if ok else 1)
