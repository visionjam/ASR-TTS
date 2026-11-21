"""渲染层与校验层的回归测试（不花钱、不联网）

素材优先用库里那条真实录音的 cleaned.md；没有就现造一份合成稿。
为什么要合成稿：以前这里写的是「没有样例就 sys.exit(0)」，
而 run.sh 只认退出码 0 —— 素材一缺，四组用例静默跳过，屏幕上照样打印
「✓ 全部通过」。测试最容易骗人的就是这个位置。

退出码：0 通过 / 1 有失败 / 2 跳过（没有任何素材，理论上到不了）。
"""
import importlib.util, os, re, sys, tempfile
from pathlib import Path

# 技能目录：手机端在 /skills/asr-notes，PC 上用随库同步的导出件。
# 都不对就用环境变量 ASR_SKILL 指过去——测试不跟你猜路径。
_HERE = Path(__file__).resolve().parent
_VAULT = _HERE.parent
SKILL = Path(os.environ.get("ASR_SKILL") or next(
    (p for p in (Path("/skills/asr-notes"), _VAULT / "agent-skill/asr-notes")
     if (p / "scripts/asr.py").is_file()), "/skills/asr-notes"))

spec = importlib.util.spec_from_file_location("asr", str(SKILL / "scripts/asr.py"))
asr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(asr)

# 素材里必须出现的真句子、必须不出现的假句子（第 4、6 组用例靠它）
REAL = "大多数中国人都承认吸烟有害"
FAKE = "他认为应该全面取消高尔夫球场的一切税收优惠"
STITCHED = "萨尔瓦多远征是一项赔本赚吆喝的买卖，预计很快结束"
QUOTE_SENT = "在讨论健康问题时，大多数中国人都承认吸烟有害，这一点没有争议。"

_FILLER = "这是用来测试分段与校验的示例句子内容本身没有意义只负责凑够长度以便检查行为是否正确"


def _sent(i):
    """每句正好 45 字、以句号收尾——句子定长，reflow 的段落边界才可预测"""
    head = "第%02d句" % i
    return head + _FILLER[:45 - len(head) - 1] + "。"


def build_fixture(root):
    """合成一份 cleaned.md：8 个片段 × 4 句，其中一段埋着 REAL 那句"""
    lines = []
    for b in range(8):
        sents = [_sent(b * 4 + j) for j in range(4)]
        if b == 3:
            sents[2] = QUOTE_SENT
        lines.append("[%02d:00] %s" % (b * 2, "".join(sents)))
    f = root / "cleaned.md"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "transcript.md").write_text("# 逐字稿（合成素材，仅供测试）\n", encoding="utf-8")
    return root


SAMPLE = _VAULT / "out/2026-11-18-sample"
if SAMPLE.is_dir():
    FOLDER = SAMPLE
    print("   素材：库里的真实样例（%s）" % SAMPLE.name)
else:
    FOLDER = build_fixture(Path(tempfile.mkdtemp(prefix="asr-t-core-")))
    print("   素材：合成稿（库里没有样例录音，改用内置素材，用例照跑）")

old_clean = (FOLDER / "cleaned.md").read_text(encoding="utf-8")

ok = True
def chk(name, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + str(extra)) if extra else ""))

print("== 1. reflow ==")
body = asr.read_cleaned(FOLDER / "cleaned.md")
blocks = asr.cleaned_blocks(body)
print("   cleaned 解析出片段:", len(blocks), "个；时间戳:", [b[0] for b in blocks][:4], "...")
chk("片段数与素材一致", len(blocks) >= 8, len(blocks))
long_block = max(blocks, key=lambda b: len(b[1]))
re_ = asr.reflow(long_block[1])
paras = re_.split("\n\n")
chk("最长片段 %d 字 → %d 段" % (len(long_block[1]), len(paras)),
    all(100 <= len(p) <= 260 for p in paras), [len(p) for p in paras])
chk("分段不丢字", re.sub(r"\s", "", re_) == re.sub(r"\s", "", long_block[1]),
    "%d vs %d" % (len(re.sub(r"\s","",re_)), len(re.sub(r"\s","",long_block[1]))))

print("== 2. write_cleaned / read_cleaned 往返 ==")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    meta = {"id": "t", "generated_at": "2026-11-18T00:00:00", "llm_model": "deepseek-chat"}
    asr.write_cleaned(td, meta, body)
    new = (td / "cleaned.md").read_text(encoding="utf-8")
    chk("写出 ### 小标题", len(re.findall(r"^### \d\d:\d\d", new, flags=re.M)) == len(blocks))
    chk("clened 段落数 > 片段数", new.count("\n\n") > len(blocks), new.count("\n\n"))
    back = asr.read_cleaned(td / "cleaned.md")
    chk("读回 [mm:ss] 行数一致", back.count("[") >= len(blocks), back.count("["))
    a = re.sub(r"\s", "", body); b = re.sub(r"\s", "", back)
    chk("往返文字无损（去空白比对）", a.replace("[","").replace("]","") == b.replace("[","").replace("]",""),
        "%d vs %d" % (len(a), len(b)))
    # 旧格式也要能读
    old = td / "old.md"
    old.write_text(old_clean, encoding="utf-8")
    chk("旧版 cleaned 仍可读", asr.read_cleaned(old).startswith("[00:00"))

print("== 3. 时间戳体检 ==")
ts_list = [b[0] for b in blocks]
ratio, missing = asr.check_cleaned(ts_list, body, back)
chk("正常稿 ratio≈1、无缺失", ratio > 0.95 and not missing, "%.2f %s" % (ratio, missing))
broken = "\n\n".join("### %s\n\n%s" % (t, x) for t, x in asr.cleaned_blocks(body)[:5])
r2, m2 = asr.check_cleaned(ts_list, body, broken)
chk("丢片段能被发现", len(m2) == len(ts_list) - 5 and r2 < 0.8, "%d 个缺失 ratio=%.2f" % (len(m2), r2))
chk("体检会打印警告", asr.report_cleaned(ts_list, body, broken) is False)

print("== 4. 摘引校验 ==")
src = asr.read_cleaned(FOLDER / "cleaned.md")
body4 = "\n".join([
    "## 关键论点",
    "- 他认为交税是最有效的自保手段（原文：\"%s\"）" % REAL,
    "- 他主张取消高尔夫球场税收（原文：\"%s\"）" % FAKE,
    "- 他认为高尔夫需求主要来自商务谈判",
    "- 他认为电子设备小型化削弱了球场的私密性优势，这一点非常重要",
])
out, nq, ns = asr.stamp_quotes(body4, src)
print(out)
chk("真摘引 → [原话]", out.splitlines()[1].endswith("[原话]"))
chk("假摘引 → [归纳] 且引文被丢掉", out.splitlines()[2].endswith("[归纳]") and "原文" not in out.splitlines()[2])
chk("无摘引条目走机械判据", out.splitlines()[4].endswith("[归纳]"))
chk("nq/ns 计数", (nq, ns) == (1, 3), (nq, ns))

print("\n== 5. 全角/半角引号、括号混用 ==")
for q, label in [("（原文：“%s”）" % REAL, "中文引号+全角括号"),
                 ("(原文: \"%s\")" % REAL, "英文引号+半角括号"),
                 ("（原文：「%s」）" % REAL, "直角引号")]:
    o, q1, _ = asr.stamp_quotes("## 核心观点\n- 某观点%s" % q, src)
    chk(label + " → [原话]", o.endswith("[原话]"), o.splitlines()[-1][:40])

print("\n== 6. 拼接引文必须被拦下 ==")
o, nq6, ns6 = asr.stamp_quotes('## 关键论点\n- 他判断远征海地赔本（原文：%s）' % STITCHED, src)
chk("半句真半句拼 → [归纳]", o.endswith("[归纳]"), o.splitlines()[-1][:50])

print("\n结论:", "全部通过" if ok else "有失败项")
sys.exit(0 if ok else 1)
