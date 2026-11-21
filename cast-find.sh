#!/bin/bash
# 字幕即索引：记住一句话，找回那一刻。
#
#   bash cast-find.sh 高尔夫          列出命中，从第一条开始播
#   bash cast-find.sh 高尔夫 play 3   从第三条命中开始播
#   bash cast-find.sh 高尔夫 print    只打印时间戳和原文，不播
#
# 原理：.srt 是纯文本，grep 拿到行号，再往**上**找最近的 "-->" 那行取时间戳。
# 注意不能只往上看一行：一条字幕的正文可能折成两行，命中的是第二行时，
# 上一行是正文而不是时间戳。
set -u

DIR=$(cd "$(dirname "$0")" && pwd)
Q=${1:-}
MODE=${2:-play}
PICK=${3:-1}

[ -n "$Q" ] || { echo "用法: bash cast-find.sh 关键词 [play|print] [第几条]"; exit 1; }

HITS=$(grep -rn --include='*.srt' -F -- "$Q" "$DIR/out" 2>/dev/null)
[ -n "$HITS" ] || { echo "没找到：$Q"; exit 1; }

# 从第 n 行往上找 "-->"，返回 "时间戳<TAB>正文起始行号"
locate() {
  local f=$1 n=$2 k cand up
  for k in 1 2 3 4 5; do
    cand=$((n - k))
    [ "$cand" -lt 1 ] && break
    up=$(sed -n "${cand}p" "$f")
    case "$up" in
      *" --> "*)
        printf '%s\t%s\n' "${up%% --> *}" "$((cand + 1))"
        return 0 ;;
    esac
  done
  printf '\t\n'
}

i=0
SEL_F=""; SEL_TS=""; SEL_TXT=""
while IFS= read -r line; do
  i=$((i + 1))
  f=${line%%:*}; rest=${line#*:}
  n=${rest%%:*}

  info=$(locate "$f" "$n")
  ts=${info%%$'\t'*}
  txtline=${info#*$'\t'}
  # 正文可能折成两三行，一直读到空行为止
  if [ -n "$txtline" ]; then
    txt=$(sed -n "${txtline},$((txtline + 3))p" "$f" | sed '/^$/q' | paste -sd '' - | sed 's/ *$//')
  else
    txt=${rest#*:}
  fi

  printf '%2d) %-12s %s\n      %s\n' "$i" "${ts:-?}" "$(basename "$f")" "$txt"
  if [ "$i" -eq "$PICK" ]; then SEL_F=$f; SEL_TS=$ts; SEL_TXT=$txt; fi
done <<< "$HITS"

[ -n "$SEL_F" ] || { echo "只有 $i 条命中，没有第 $PICK 条"; exit 1; }
[ -n "$SEL_TS" ] || { echo "✗ 找不到时间戳，srt 可能被手改坏了：$SEL_F"; exit 1; }
echo "—— 共 $i 条，选第 $PICK 条 ——"

[ "$MODE" = "print" ] && exit 0

AUDIO="${SEL_F%.srt}.m4a"
[ -f "$AUDIO" ] || { echo "✗ 找不到音频：$AUDIO"; exit 1; }

SEC=$(echo "$SEL_TS" | awk -F'[:,]' '{print $1*3600+$2*60+$3+$4/1000}')
echo "跳转: $SEL_TS（${SEC}s）  $SEL_TXT"

if command -v mpv >/dev/null 2>&1; then
  exec mpv --force-window=yes --sub-file="$SEL_F" --start="$SEC" "$AUDIO"
elif command -v vlc >/dev/null 2>&1; then
  exec vlc --start-time="$SEC" --sub-file="$SEL_F" "$AUDIO"
else
  echo "没有 mpv / vlc，用你的播放器手动打开："
  echo "  音频 $AUDIO"
  echo "  字幕 $SEL_F"
fi
