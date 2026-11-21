#!/bin/bash
# 检查手机端技能（/skills/*）与库里的导出件（agent-skill/*）是否一致
# 另外比一次 .stignore：它是每台设备各一份、不随同步走，所以库里放 stignore.template 当唯一副本
#
#   bash check-sync.sh                 # 两个技能 + .stignore
#   bash check-sync.sh --fix           # 从手机端覆盖导出件
#   bash check-sync.sh <技能目录> <导出件目录>   # 只查一对（老用法，不查 .stignore）
#
# 为什么要有这个：导出件是手动 cp 的，改了手机端忘了导出，PC 会静默跑旧逻辑，
# 而产出上看不出差别。逐文件比 md5，比 mtime 靠谱（cp 会改 mtime，内容没变）。
#
# 退出码 0 = 全部一致（或 --fix 之后已一致），1 = 有漂移。
set -u

ROOT=$(cd "$(dirname "$0")" && pwd)
SKILLS=${SKILLS:-"asr-notes tts-cast"}

FIX=0
args=()
for a in "$@"; do
  case "$a" in
    --fix|-f) FIX=1 ;;
    *) args+=("$a") ;;
  esac
done

# 清单格式：<相对路径>\t<md5 前 8 位>，按路径排序
md5s() {
  ( cd "$1" && find . -type f ! -name '*.pyc' ! -path '*/__pycache__/*' | sort |
    while IFS= read -r f; do
      printf '%s\t%s\n' "${f#./}" "$(md5sum "$f" | cut -c1-8)"
    done )
}

check_one() {
  local A=$1 B=$2
  local TMP drift fixed f ha hb
  [ -d "$A" ] || { echo "  ✗ 找不到技能目录 $A"; return 1; }
  [ -d "$B" ] || { echo "  ✗ 找不到导出件 $B"; return 1; }

  TMP=$(mktemp -d 2>/dev/null || echo /tmp/sync.$$)
  mkdir -p "$TMP"
  md5s "$A" > "$TMP/a"
  md5s "$B" > "$TMP/b"

  # 两边的路径取并集：逐行对齐会把「只在手机端」误报成「不一致」，
  # 而那正是这个脚本要防的事。
  { cut -f1 "$TMP/a"; cut -f1 "$TMP/b"; } | sort -u > "$TMP/union"

  echo "  文件数：手机端 $(wc -l < "$TMP/a") / 导出件 $(wc -l < "$TMP/b")"
  drift=0
  fixed=0
  while read -r f; do
    [ -n "$f" ] || continue
    ha=$(awk -F'\t' -v k="$f" '$1==k{print $2; exit}' "$TMP/a")
    hb=$(awk -F'\t' -v k="$f" '$1==k{print $2; exit}' "$TMP/b")
    if [ -n "$ha" ] && [ -n "$hb" ] && [ "$ha" = "$hb" ]; then
      printf '    %-46s 一致\n' "$f"
      continue
    fi
    drift=1
    if [ -z "$hb" ]; then
      printf '    %-46s 只在手机端\n' "$f"
    elif [ -z "$ha" ]; then
      printf '    %-46s 只在导出件\n' "$f"
    else
      printf '    %-46s ✗ 不一致（%s / %s）\n' "$f" "$ha" "$hb"
    fi
    if [ $FIX -eq 1 ] && [ -n "$ha" ]; then
      mkdir -p "$B/$(dirname "$f")"
      cp -p "$A/$f" "$B/$f" && fixed=$((fixed + 1))
    fi
  done < "$TMP/union"

  if [ $drift -eq 0 ]; then
    rm -rf "$TMP"
    echo "  ✓ 一致"
    return 0
  fi

  if [ $FIX -eq 1 ]; then
    rm -rf "$TMP"
    if [ "$fixed" -gt 0 ]; then
      echo "  → 已覆盖 $fixed 个文件"
      check_one "$A" "$B"
      return $?
    fi
    echo "  ✗ 只有导出件有的文件不会被自动删，手动看一眼"
    return 1
  fi

  rm -rf "$TMP"
  echo "  ✗ 有漂移"
  return 1
}

# 规则行规范化：去掉 // 注释、空行、行尾空白和 CR（Windows 上是 CRLF）。
# 剩下的才是真正生效的东西——注释怎么写、换行符是 LF 还是 CRLF 都不算差异。
rules_of() {
  sed 's/\r$//; s/[[:space:]]*$//' "$1" | grep -vE '^[[:space:]]*(//|$)'
}

# .stignore 不随 Syncthing 同步，漂了没人看得见——所以拿库里的 stignore.template 当唯一副本。
# 两台设备都应当是「模板 → .stignore」的一次复制；这里比规则行（顺序也算），不比字节。
check_stignore() {
  local T=$ROOT/stignore.template F=$ROOT/.stignore n d tmp
  echo "── .stignore ──"
  if [ ! -f "$T" ]; then
    echo "  ⚠ 没有 stignore.template（老库），跳过"
    return 0
  fi
  if [ ! -f "$F" ]; then
    echo "  ✗ 本机没有 .stignore（Syncthing 会把 config.env 一起同步出去）"
    return 1
  fi
  tmp=$(mktemp -d 2>/dev/null || echo /tmp/stig.$$)
  mkdir -p "$tmp"
  rules_of "$T" > "$tmp/t"
  rules_of "$F" > "$tmp/f"
  n=$(wc -l < "$tmp/t")
  d=$(md5sum "$tmp/f" | cut -c1-12)
  if cmp -s "$tmp/t" "$tmp/f"; then
    echo "  ✓ 与模板规则行一致（$n 条，顺序也一致）"
    echo "    digest $d  ← PC 上用 同步说明.md 第三节那段 PowerShell 算出同一个值"
    rm -rf "$tmp"
    return 0
  fi
  echo "  ✗ 与模板规则行不一致（模板 $n 条 / 本机 $(wc -l < "$tmp/f") 条）："
  diff -u "$tmp/t" "$tmp/f" | sed 's/^/    /'
  echo "    本机 digest $d"
  rm -rf "$tmp"
  return 1
}

rc=0
if [ ${#args[@]} -ge 1 ]; then
  # 兼容老用法：bash check-sync.sh <技能目录> <导出件目录>
  A=${args[0]}
  B=${args[1]:-$ROOT/agent-skill/$(basename "$A")}
  echo "── $(basename "$A") ──"
  check_one "$A" "$B" || rc=1
else
  for s in $SKILLS; do
    echo "── $s ──"
    check_one "/skills/$s" "$ROOT/agent-skill/$s" || rc=1
  done
  check_stignore || rc=1
fi

echo
if [ $rc -eq 0 ]; then
  echo "✓ 全部一致"
else
  echo "✗ 有漂移："
  echo "  技能  → bash $(basename "$0") --fix        （手机端 /skills → agent-skill/）"
  echo "  .stignore → cp stignore.template .stignore （先确认模板才是你要的那份）"
fi
exit $rc
