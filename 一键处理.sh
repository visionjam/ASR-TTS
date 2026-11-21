#!/bin/bash
# 一键处理：把音频或字幕丢进 inbox/，在共享目录里跑这一条就够
#
#   bash /sdcard/ASRTTS/一键处理.sh
#   bash 一键处理.sh --dry-run              # 只列清单，不调 API、不写文件
#   bash 一键处理.sh --kind self            # 自己口述的录音/视频
#   bash 一键处理.sh --force                # 重跑已处理过的
#
# 所有参数原样转给 asr.py，所以 --asr-only / --no-archive 之类都能用。
# 唯一例外是 --kind：一批里同时有音频和字幕时，它只给音频，字幕按默认走
# （见下面「--kind 只套给音频」那段）。
#
# 不需要先把文件复制到别的工作区：这个目录本身就是数据目录。
set -u

DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR" || exit 1
SKILL=${ASR_SKILL:-/skills/asr-notes}
[ -d "$SKILL" ] || SKILL="$DIR/agent-skill/asr-notes"
PY=${PYTHON:-python3}

[ -f "$SKILL/scripts/asr.py" ] || {
  echo "✗ 找不到技能：$SKILL/scripts/asr.py"
  echo "  PC 上把 ASR_SKILL 指到 agent-skill/asr-notes 的绝对路径"
  exit 1
}
command -v "$PY" >/dev/null 2>&1 || { echo "✗ 没有 $PY"; exit 1; }

shopt -s nullglob nocaseglob
audio=(inbox/*.m4a inbox/*.mp3 inbox/*.wav inbox/*.aac inbox/*.amr \
       inbox/*.ogg inbox/*.opus inbox/*.flac inbox/*.3gp inbox/*.mp4 inbox/*.wma)
subs=(inbox/*.srt inbox/*.vtt inbox/*.ass inbox/*.ssa)

if [ ${#audio[@]} -eq 0 ] && [ ${#subs[@]} -eq 0 ]; then
  md=(inbox/*.md inbox/*.txt)
  if [ ${#md[@]} -gt 0 ]; then
    echo "inbox/ 里只有 ${#md[@]} 个 .md/.txt——那是文章，要走另一条线："
    echo "  bash 一键开播.sh"
    exit 0
  fi
  echo "inbox/ 里既没有音频也没有字幕。"
  echo "丢文件进去再跑；录音机目录已在 EXTRA_DIRS 里的会自动带过来。"
  exit 0
fi

# --kind 只套给音频
# 同一个 --kind 同时套给两种文件会出错：说「这些是我口述的」时，
# 顺手放进来的别人的播客字幕就会被当自述整理（模板正好用反）。
# 只在一批里同时有两种文件时才拆开——只有字幕时，--kind 显然是在说字幕。
sub_args=()
mixed=0
if [ ${#audio[@]} -gt 0 ] && [ ${#subs[@]} -gt 0 ]; then
  mixed=1
  args=("$@"); i=0; dropped=""
  while [ $i -lt ${#args[@]} ]; do
    case "${args[$i]}" in
      --kind)   dropped="${args[$((i+1))]:-}"; i=$((i+2)) ;;
      --kind=*) dropped="${args[$i]#--kind=}"; i=$((i+1)) ;;
      *)        sub_args+=("${args[$i]}"); i=$((i+1)) ;;
    esac
  done
  if [ -n "$dropped" ]; then
    echo "提示：--kind $dropped 只作用于本批音频；字幕按默认 external 整理。"
    echo "      要让字幕也走 self，单独跑：python3 $SKILL/scripts/asr.py --srt 文件 --kind self"
  fi
else
  sub_args=("$@")
fi

rc=0

echo "══ 音频（${#audio[@]} 个）══"
if [ ${#audio[@]} -eq 0 ]; then
  echo "（没有）"
else
  "$PY" "$SKILL/scripts/asr.py" "$@" || rc=1
fi

echo
echo "══ 字幕（${#subs[@]} 个）══"
if [ ${#subs[@]} -eq 0 ]; then
  echo "（没有）"
else
  for f in "${subs[@]}"; do
    "$PY" "$SKILL/scripts/asr.py" --srt "$f" "${sub_args[@]}" || rc=1
    echo
  done
fi

echo "══ 结果 ══"
newest=$(ls -t out 2>/dev/null | head -3)
if [ -n "$newest" ]; then
  echo "out/ 里最近的："
  echo "$newest" | sed 's/^/  /'
fi
[ $rc -eq 0 ] || echo "⚠ 有失败项，看 work/error.log"
exit $rc
