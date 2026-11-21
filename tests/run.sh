#!/bin/bash
# 回归测试：改完 asr.py / asr_render.py / asr_quotes.py / subtitle.py 之后跑一遍
#   bash /sdcard/ASRTTS/tests/run.sh
#
# 退出码约定（别把跳过当通过）：
#   0 = 全部通过   1 = 有失败   2 = 有跳过项（素材缺失，不算通过）
set -u
cd "$(dirname "$0")" || exit 1

pass=0; skip=0; fail=0
for t in t_core.py t_subtitle.py; do
  echo "──── $t"
  python3 "$t"
  case $? in
    0) pass=$((pass + 1)) ;;
    2) skip=$((skip + 1)); echo "   ⚠ 跳过（素材缺失，没跑）" ;;
    *) fail=$((fail + 1)) ;;
  esac
  echo
done

echo "通过 $pass · 跳过 $skip · 失败 $fail"
if [ $fail -gt 0 ]; then
  echo "✗ 有失败项"
  exit 1
elif [ $skip -gt 0 ]; then
  echo "⚠ 有跳过项，不能算通过"
  exit 2
fi
echo "✓ 全部通过"
exit 0
