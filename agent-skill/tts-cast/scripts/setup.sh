#!/bin/bash
# 装依赖：requests + 自带静态 ffmpeg 的 imageio-ffmpeg
set -eu
PY=${PYTHON:-python3}
echo "== 检查 $PY =="
"$PY" -V

echo "== 装依赖 =="
"$PY" -m pip install -q --upgrade requests imageio-ffmpeg

echo "== 验一下 =="
"$PY" - <<'EOF'
import requests, imageio_ffmpeg
print("requests     ok")
print("ffmpeg       ", imageio_ffmpeg.get_ffmpeg_exe())
EOF
echo
echo "下一步：在数据目录（库根，和 一键开播.sh 同一层）放一份 config.env，"
echo "里面一行 DEEPSEEK_API_KEY=...   （和 asr-notes 共用同一个 Key）"
echo "然后跑：python3 $(dirname "$0")/cast.py --check"
