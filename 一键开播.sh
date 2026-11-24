#!/bin/bash
# 一键开播：把文章丢进 inbox/，在这里跑这一条就够
#
#   bash 一键开播.sh                 inbox/ 里的 .md/.txt 全部跑一遍
#   bash 一键开播.sh --dry-run       只出计划、估钱、估时长，不花钱
#   bash 一键开播.sh --rewrite       先让 LLM 改写成口播稿再读（只改句子，不动结构）
#   bash 一键开播.sh --spoken        改写成口述稿再读（重排、合并、删枝节，多出大纲和删改清单）
#   bash 一键开播.sh --spoken --stop-at-outline   只出大纲，零 TTS 费用
#   bash 一键开播.sh --force         重跑（没改过的段仍复用，不重复计费）
#
# 参数原样转给 cast.py。Key 用上一层的 config.env，和 asr-notes 同一个。
set -u

DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR" || exit 1
SKILL=${CAST_SKILL:-/skills/tts-cast}
PY=${PYTHON:-python3}

[ -f "$SKILL/scripts/cast.py" ] || {
  echo "✗ 找不到技能：$SKILL/scripts/cast.py"
  echo "  PC 上把 CAST_SKILL 指到 tts-cast 的绝对路径"
  exit 1
}
command -v "$PY" >/dev/null 2>&1 || { echo "✗ 没有 $PY"; exit 1; }

exec "$PY" "$SKILL/scripts/cast.py" --inbox inbox --out out --work work "$@"
