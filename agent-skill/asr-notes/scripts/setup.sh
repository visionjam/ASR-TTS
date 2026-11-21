#!/bin/bash
# 一次性装依赖：requests + 自带静态 ffmpeg 的 imageio-ffmpeg
# 用法: bash /skills/asr-notes/scripts/setup.sh
set -u

command -v python3 >/dev/null 2>&1 || { echo "✗ 没有 python3"; exit 1; }

MIRROR=${PIP_MIRROR:-https://pypi.tuna.tsinghua.edu.cn/simple}
echo "→ 从 $MIRROR 安装 requests / imageio-ffmpeg"
python3 -m pip install -q --break-system-packages -i "$MIRROR" requests imageio-ffmpeg 2>/dev/null \
  || python3 -m pip install -q -i "$MIRROR" requests imageio-ffmpeg

python3 - <<'EOF'
import os, sys
try:
    import requests
except ImportError:
    sys.exit("✗ requests 装不上，试试 pip install requests")
try:
    import imageio_ffmpeg
    p = imageio_ffmpeg.get_ffmpeg_exe()
    print("✓ ffmpeg:", p, "(%d MB)" % (os.path.getsize(p) // 1048576))
except Exception as e:
    sys.exit("✗ ffmpeg 不可用: %s（也可以 apt install ffmpeg）" % e)
print("✓ requests", requests.__version__)
EOF
