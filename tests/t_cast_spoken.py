#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""口述稿（cast.py --spoken）的离线回归：假 LLM + 假合成，不联网、不花钱。

查五件事（这几条是真会坏的地方）：
  1. 分块：不超上限、不切碎、不丢字、不改序
  2. 两遍：停在大纲只花一次调用；复用大纲只跑落地
  3. 幂等：大纲尾巴的「字数」段不会每跑一次多一段
  4. 超长：超过上限直接拒，不做假分块
  5. 整条路：目录带 -口述、三份新产出落盘、重跑不动 LLM、从 script.md 重跑不二次改写

退出码：0 通过 / 1 有失败。
"""
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_VAULT = _HERE.parent
SKILL = Path(os.environ.get("CAST_SKILL") or next(
    (p for p in (Path("/skills/tts-cast"), _VAULT / "agent-skill/tts-cast")
     if (p / "scripts/cast.py").is_file()), "/skills/tts-cast"))

sys.path.insert(0, str(SKILL / "scripts"))
try:
    import cast as C
    import cast_text as T
except (ImportError, SystemExit) as e:                     # requests 没装
    print("✗ 导入失败：%s（pip install requests）" % e)
    sys.exit(1)

FAIL = []
CALLS = []


def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        FAIL.append(name)


def fake_llm(system, body, temperature=0.3):
    CALLS.append(len(body))
    if "改造图纸" in system:
        return ("## 主线\n把检索增强搬到端侧，省云的钱、花显存。\n\n"
                "## 听众\n做客户端的技术人。\n\n"
                "## 分块计划\n### 第 1 块 · 迁移的代价\n- 保留：显存小、带宽窄\n"
                "- 合并或删：删掉历史铺垫\n- 接头：结尾交给「怎么办」\n\n"
                "## 删改清单\n- 删：三段时间线 —— 与结论无关\n"
                "- 并：量化、缓存压缩 并成「压缩有损」\n\n"
                "## 结尾落点\n落在「掉多少准确率还愿意搬」。")
    paras = [p for p in body.partition("】\n")[2].split("\n\n") if p.strip()]
    kept = [p[: int(len(p) * 0.6)] for p in paras]
    if "第 1/" in body:
        kept.insert(0, "# 口述标题")
    return "\n\n".join(kept)


C.llm = fake_llm
C.CFG.update({"LLM_MODEL": "fake", "VOICE": "tide", "TTS_PROVIDER": "deepseek",
              "PRICE_PER_10K": 1.5})


def long_art():
    return {"title": "云端到端侧", "dropped": [], "blocks":
            [{"kind": "h", "text": "迁移的代价"}] +
            [{"kind": "p", "text": "端侧设备的显存和带宽都低于服务器，这带来一系列工程取舍。" * 20}
             for _ in range(4)] +
            [{"kind": "h", "text": "怎么办"}] +
            [{"kind": "p", "text": "工程师只能压缩模型，压缩就有损，有损就掉准确率。" * 20}
             for _ in range(4)]}


def main():
    root = Path(tempfile.mkdtemp())
    try:
        print("[1] 分块")
        art = long_art()
        chunks = C.spoken_blocks(art)
        lens = [sum(len(b["text"]) for b in c) for c in chunks]
        check("每块不超上限", all(n <= C.SPOKEN_BLOCK_MAX for n in lens), lens)
        check("块内容不丢字、不改序",
              [b["text"] for c in chunks for b in c] == [b["text"] for b in art["blocks"]])
        long_art2 = {"title": "t", "dropped": [], "blocks":
                     [{"kind": "p", "text": "这是一句用来凑长度的话。" * 40} for _ in range(12)]}
        lens2 = [sum(len(b["text"]) for b in c) for c in C.spoken_blocks(long_art2)]
        check("没有小标题时也不切碎（除末块）",
              all(n >= C.SPOKEN_BLOCK_MIN - 200 for n in lens2[:-1]), lens2)

        print("\n[2] 两遍")
        out = root / "cast"
        CALLS.clear()
        args = types.SimpleNamespace(outline=None, stop_at_outline=True, preview=None)
        new, stopped = C.spoken_pass(art, args, out, "inbox/x.md")
        check("严格停在大纲（不落地、不合成）", stopped and new is None)
        check("只花了一次调用", len(CALLS) == 1, CALLS)
        check("大纲和删改清单都落盘",
              (out / "大纲.md").is_file() and (out / "删改清单.md").is_file())
        check("删改清单里有内容",
              "三段时间线" in (out / "删改清单.md").read_text(encoding="utf-8"))

        CALLS.clear()
        args2 = types.SimpleNamespace(outline=str(out / "大纲.md"),
                                      stop_at_outline=False, preview=None)
        new, stopped = C.spoken_pass(art, args2, out, "inbox/x.md")
        check("复用大纲只跑落地", len(CALLS) == len(C.spoken_blocks(art)), CALLS)
        check("第一块出标题", bool(new) and new["title"] == "口述标题", new and new["title"])
        check("口述稿落盘", (out / "口述稿.md").is_file())

        print("\n[3] 幂等")
        txt = (out / "大纲.md").read_text(encoding="utf-8")
        check("字数段有且只有一段", txt.count("## 字数") == 1, txt.count("## 字数"))
        C.spoken_pass(art, args2, out, "inbox/x.md")
        txt2 = (out / "大纲.md").read_text(encoding="utf-8")
        check("再跑一次也不叠罗汉", txt2.count("## 字数") == 1, txt2.count("## 字数"))

        print("\n[4] 进现有管线")
        art3 = T.parse_article((out / "口述稿.md").read_text(encoding="utf-8"))
        segs = C.finish_plan(C.split_plan(art3))
        check("能切成段、算得出计费", len(segs) > 0 and all(s["billed"] >= 0 for s in segs))
        try:
            C.spoken_pass({"title": "t", "dropped": [],
                           "blocks": [{"kind": "p", "text": "字" * (C.SPOKEN_MAX_CHARS + 1)}]},
                          args2, out, "inbox/y.md")
            check("超长会拒跑", False, "没拒")
        except RuntimeError:
            check("超长会拒跑", True)

        print("\n[4b] 落稿体检")
        src_qc = {"title": "t", "dropped": [], "blocks":
                  [{"kind": "h", "text": "第一节"}, {"kind": "h", "text": "第二节"}]}
        dup_s = "这件事他后来又原原本本讲了一遍给大家听。"
        new_qc = {"title": "t", "dropped": [], "blocks": [
            {"kind": "p", "text": dup_s},
            {"kind": "p", "text": "中间隔一段别的。"},
            {"kind": "p", "text": dup_s},
            {"kind": "p", "text": "这一句被故意写得特别长，" * 6 + "一直不停顿。"},
        ]}
        found = dict(C.spoken_qc(src_qc, new_qc))
        check("查得出章节丢了", "章节" in found, found)
        check("查得出同一件事讲两遍", "重复" in found, found)
        check("重叠片段只报一次（不刷屏）",
              sum(1 for k, _ in C.spoken_qc(src_qc, new_qc) if k == "重复") == 1, found)
        check("查得出念不完的长句", "长句" in found, found)
        check("干净稿不误报",
              C.spoken_qc({"title": "t", "dropped": [], "blocks": []},
                          {"title": "t", "dropped": [],
                           "blocks": [{"kind": "p", "text": "短句子。又一句。"}]}) == [])

        print("\n[5] 整条路（假合成）")
        C.provider = lambda: ("deepseek", {"price": 1.5, "cjk": 2, "model": "m",
                                           "label": "x", "limit": 10000})
        C.synth_all = lambda s, workdir, voice, resynth=False: (0, [])

        def fake_timeline(s):
            for i, x in enumerate(s):
                x["start"], x["dur"] = i * 2.0, 2.0
            return len(s) * 2.0, [(0.0, "开篇")]
        C.build_timeline = fake_timeline
        C.build_cues = lambda s, max_cue=18.0: [(x.get("start", 0) + 0.1,
                                                x.get("start", 0) + 1.9, x["text"][:10])
                                               for x in s]

        def fake_assemble(s, workdir, outdir, name, total, chapters,
                          srt=None, normalize=True, embed_sub=False):
            p = Path(outdir) / (name + ".m4a")
            p.write_bytes(b"fake")
            return p
        C.assemble = fake_assemble

        work = root / "lib"
        (work / "inbox").mkdir(parents=True)
        (work / "inbox" / "blog.md").write_text(
            "# 云端到端侧\n\n## 开篇\n\n" + "端侧显存低于服务器。" * 60
            + "\n\n" + "压缩就有损。" * 60, encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(work)                                     # cast.py 的默认路径都相对 cwd
        try:
            cli = types.SimpleNamespace(
                files=[], inbox="inbox", out="out", work="work", config=None,
                provider=None, voice=None, speech_rate=None, date=None, slug=None,
                preview=None, rewrite=False, spoken=True, stop_at_outline=False,
                outline=None, archive="archive", no_archive=False,
                dry_run=False, force=False, resynth=False, verbose=False,
                no_normalize=True, no_verbalize=False, embed_sub=False, check=False,
                all=False)
            CALLS.clear()
            got = C.process("inbox/blog.md", cli)
            names = sorted(p.name for p in got.iterdir())
            check("目录带 -口述 后缀", got.name.endswith("-口述"), got.name)
            for f in ("大纲.md", "删改清单.md", "口述稿.md", "script.md", "index.md",
                      got.name + ".srt", got.name + ".lrc", got.name + ".m4a"):
                check("产出 %s" % f, f in names, names)
            check("script.md 出自口述稿",
                  "口述标题" in (got / "script.md").read_text(encoding="utf-8"))
            check("原件归档，inbox 空了",
                  not (work / "inbox" / "blog.md").exists()
                  and (work / "archive" / "blog.md").is_file())

            CALLS.clear()
            again = C.process("archive/blog.md", cli)
            check("重跑：已存在直接跳过，零调用", again == got and not CALLS, CALLS)
            check("跳过时不重复归档",
                  sorted(p.name for p in (work / "archive").iterdir()) == ["blog.md"],
                  sorted(p.name for p in (work / "archive").iterdir()))

            CALLS.clear()
            forced = types.SimpleNamespace(**{**cli.__dict__, "force": True})
            C.process(str(got / "script.md"), forced)
            check("从 script.md 重跑不二次改写", not CALLS, CALLS)
            check("从 out/ 重跑不搬原件",
                  sorted(p.name for p in (work / "archive").iterdir()) == ["blog.md"])
        finally:
            os.chdir(cwd)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if FAIL:
        print("\n✗ 失败 %d 项：%s" % (len(FAIL), "、".join(FAIL)))
        return 1
    print("\n✓ 口述稿全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
