# ASRTTS Windows 工作流入口
# 兼容 Windows PowerShell 5.1。本文件必须保存为 UTF-8 with BOM，否则中文会乱码。
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File windows\asrtts.ps1 process [参数]
#   powershell -NoProfile -ExecutionPolicy Bypass -File windows\asrtts.ps1 cast    [参数]
#   powershell -NoProfile -ExecutionPolicy Bypass -File windows\asrtts.ps1 find 关键词 [print|play] [第几条]
#   powershell -NoProfile -ExecutionPolicy Bypass -File windows\asrtts.ps1 check [--offline]
#   powershell -NoProfile -ExecutionPolicy Bypass -File windows\asrtts.ps1 test
#   powershell -NoProfile -ExecutionPolicy Bypass -File windows\asrtts.ps1 setup [--with-ffmpeg]
#   powershell -NoProfile -ExecutionPolicy Bypass -File windows\asrtts.ps1 menu
#
# 设计原则：只做 Windows 这一层壳（定位目录、找 Python、UTF-8、转发参数），
# 业务流程仍在 agent-skill 里的 asr.py / cast.py，改逻辑改技能，不在这里复制。

$ErrorActionPreference = 'Continue'
$script:ResultCode = 0
$script:LastPyCode = 0

# ---------- 控制台编码：Python 子进程输出 UTF-8，PowerShell 也按 UTF-8 解码 ----------
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
} catch { }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

# ---------- 定位 ASRTTS 根目录（本脚本在 windows\ 下） ----------
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$Root = $null
if ($env:ASRTTS_ROOT -and (Test-Path -LiteralPath $env:ASRTTS_ROOT)) {
    $Root = (Resolve-Path -LiteralPath $env:ASRTTS_ROOT).Path
} else {
    $probe = $ScriptDir
    while ($probe) {
        if ((Test-Path -LiteralPath (Join-Path $probe 'agent-skill\asr-notes\scripts\asr.py')) -and
            (Test-Path -LiteralPath (Join-Path $probe 'config.env.example'))) {
            $Root = $probe
            break
        }
        $parent = Split-Path -Parent $probe
        if (-not $parent -or $parent -eq $probe) { break }
        $probe = $parent
    }
    if (-not $Root) { $Root = Split-Path -Parent $ScriptDir }
}
if (-not $Root) { $Root = $ScriptDir }
$Root = (Resolve-Path -LiteralPath $Root).Path

$AsrSkill = if ($env:ASR_SKILL) { $env:ASR_SKILL } else { Join-Path $Root 'agent-skill\asr-notes' }
$CastSkill = if ($env:CAST_SKILL) { $env:CAST_SKILL } else { Join-Path $Root 'agent-skill\tts-cast' }
$LogDir = Join-Path $Root 'logs'
$LogFile = Join-Path $LogDir ('windows-' + (Get-Date -Format 'yyyyMMdd') + '.log')

# ---------- 小工具 ----------
function Write-Head([string]$Text) {
    Write-Host ''
    Write-Host ('== ' + $Text + ' ==') -ForegroundColor Cyan
}
function Write-Ok([string]$Text) { Write-Host ('  [OK] ' + $Text) -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host ('  [!!] ' + $Text) -ForegroundColor Yellow }
function Write-Err([string]$Text) { Write-Host ('  [XX] ' + $Text) -ForegroundColor Red }
function Write-Info([string]$Text) { Write-Host ('  ' + $Text) }

function Write-RunLog([string]$Message) {
    try {
        if (-not (Test-Path -LiteralPath $LogDir)) {
            New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
        }
        $line = '[' + (Get-Date -Format 'HH:mm:ss') + '] ' + $Message + [Environment]::NewLine
        [System.IO.File]::AppendAllText($LogFile, $line, (New-Object System.Text.UTF8Encoding($false)))
    } catch { }
}

function Ensure-Dirs {
    foreach ($n in @('inbox', 'out', 'archive', 'work', 'logs')) {
        $p = Join-Path $Root $n
        if (-not (Test-Path -LiteralPath $p)) {
            New-Item -ItemType Directory -Path $p -Force | Out-Null
        }
    }
}

function Get-InboxFiles([string[]]$Exts) {
    $inbox = Join-Path $Root 'inbox'
    if (-not (Test-Path -LiteralPath $inbox)) { return @() }
    $items = @(Get-ChildItem -LiteralPath $inbox -File -ErrorAction SilentlyContinue |
        Where-Object { $Exts -contains $_.Extension.ToLowerInvariant() } |
        Sort-Object Name)
    return $items
}

function Show-RecentOut {
    $out = Join-Path $Root 'out'
    if (-not (Test-Path -LiteralPath $out)) { return }
    $dirs = @(Get-ChildItem -LiteralPath $out -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 3)
    if ($dirs.Count -eq 0) { return }
    Write-Head '结果'
    Write-Info 'out\ 里最近的：'
    foreach ($d in $dirs) { Write-Info ('  ' + $d.Name) }
}

function Convert-TsToSeconds([string]$Ts) {
    if (-not $Ts) { return 0 }
    $p = $Ts -split '[:,.]'
    $h = 0; $m = 0; $s = 0; $ms = 0
    if ($p.Count -ge 1) { [void][int]::TryParse([string]$p[0], [ref]$h) }
    if ($p.Count -ge 2) { [void][int]::TryParse([string]$p[1], [ref]$m) }
    if ($p.Count -ge 3) { [void][int]::TryParse([string]$p[2], [ref]$s) }
    if ($p.Count -ge 4) { [void][int]::TryParse([string]$p[3], [ref]$ms) }
    return ($h * 3600 + $m * 60 + $s + $ms / 1000.0)
}

# ---------- Python / ffmpeg ----------
function Resolve-Python {
    $cands = New-Object System.Collections.ArrayList
    if ($env:ASRTTS_PYTHON) { [void]$cands.Add(@{ Exe = $env:ASRTTS_PYTHON; Pre = @() }) }
    if ($env:PYTHON) { [void]$cands.Add(@{ Exe = $env:PYTHON; Pre = @() }) }
    $c = Get-Command python -ErrorAction SilentlyContinue
    if ($c) { [void]$cands.Add(@{ Exe = $c.Source; Pre = @() }) }
    $c = Get-Command py -ErrorAction SilentlyContinue
    if ($c) { [void]$cands.Add(@{ Exe = $c.Source; Pre = @('-3') }) }
    foreach ($cand in $cands) {
        $exe = [string]$cand.Exe
        if (-not $exe) { continue }
        if (-not (Test-Path -LiteralPath $exe)) {
            $g = Get-Command $exe -ErrorAction SilentlyContinue
            if (-not $g) { continue }
            $exe = $g.Source
        }
        $pre = @($cand.Pre)
        $out = & $exe @pre -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) {
            return @{ Exe = $exe; Pre = $pre; Version = [string]$out }
        }
    }
    return $null
}

function Get-Ffmpeg {
    if ($env:FFMPEG_BIN -and (Test-Path -LiteralPath $env:FFMPEG_BIN)) { return $env:FFMPEG_BIN }
    $c = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    if ($PyExe) {
        $pre = $PyPre
        $p = & $PyExe @pre -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())" 2>$null
        if ($LASTEXITCODE -eq 0 -and $p) {
            $p = @($p)[-1]
            if (Test-Path -LiteralPath $p) { return $p }
        }
    }
    return $null
}

function Invoke-PyRaw([string[]]$AllArgs) {
    if (-not $PyExe) {
        Write-Err '找不到 Python。装 Python 3.9+ 并加入 PATH，或设 ASRTTS_PYTHON。'
        $script:LastPyCode = 127
        return
    }
    if (-not $AllArgs) { $AllArgs = @() }
    $pre = $PyPre
    & $PyExe @pre @AllArgs
    $script:LastPyCode = $LASTEXITCODE
}

function Invoke-PyScript([string]$Path, [string[]]$PyArgs) {
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Err ('找不到脚本：' + $Path)
        $script:LastPyCode = 127
        return
    }
    if (-not $PyArgs) { $PyArgs = @() }
    $all = @($Path) + @($PyArgs)
    Invoke-PyRaw $all
}

# ---------- process：inbox 音频 + 字幕 ----------
function Split-KindArg([string[]]$Argv) {
    $kept = New-Object System.Collections.ArrayList
    $kind = $null
    $i = 0
    while ($i -lt $Argv.Count) {
        $a = [string]$Argv[$i]
        if ($a -eq '--kind') {
            if ($i + 1 -lt $Argv.Count) { $kind = [string]$Argv[$i + 1]; $i += 2; continue }
            $i++
            continue
        }
        if ($a -like '--kind=*') { $kind = $a.Substring(7); $i++; continue }
        [void]$kept.Add($a)
        $i++
    }
    return @{ Kept = @($kept.ToArray()); Kind = $kind }
}

function Test-DirectAsrArg([string[]]$Argv) {
    foreach ($a in $Argv) {
        $s = [string]$a
        if ($s -in @('--ai-only', '--text', '--add', '--daily', '--srt', '--check')) { return $true }
        if ($s -like '--ai-only=*' -or $s -like '--text=*' -or $s -like '--add=*' -or $s -like '--srt=*') { return $true }
    }
    return $false
}

function Invoke-Process([string[]]$Argv) {
    $script:ResultCode = 0
    if (-not $Argv) { $Argv = @() }
    Ensure-Dirs
    $base = @('--inbox', (Join-Path $Root 'inbox'),
              '--out', (Join-Path $Root 'out'),
              '--archive', (Join-Path $Root 'archive'),
              '--work', (Join-Path $Root 'work'),
              '--state', (Join-Path $Root '.asr_state.json'),
              '--notes-dir', (Join-Path $Root '00-Inbox'),
              '--config', (Join-Path $Root 'config.env'))

    # --ai-only / --text / --add / --daily / --srt / --check 不扫 inbox，
    # 直接转给 asr.py；否则 inbox 为空时壳会提前返回，这些模式永远跑不到。
    if (Test-DirectAsrArg $Argv) {
        Write-Head 'ASRTTS · 直接调用 asr.py'
        Write-Info ('数据目录：' + $Root)
        Invoke-PyScript (Join-Path $AsrSkill 'scripts\asr.py') (@($base) + @($Argv))
        if ($script:LastPyCode -ne 0) { $script:ResultCode = 1 }
        Show-RecentOut
        return
    }

    $audioExt = @('.m4a', '.mp3', '.wav', '.aac', '.amr', '.ogg', '.opus', '.flac', '.3gp', '.mp4', '.wma', '.webm')
    $subExt = @('.srt', '.vtt', '.ass', '.ssa')
    $audio = @(Get-InboxFiles $audioExt)
    $subs = @(Get-InboxFiles $subExt)
    Write-Head 'ASRTTS · 处理 inbox'
    Write-Info ('数据目录：' + $Root)
    Write-Info ('音频 {0} 个 · 字幕 {1} 个' -f $audio.Count, $subs.Count)
    if ($audio.Count -eq 0 -and $subs.Count -eq 0) {
        $docs = @(Get-InboxFiles @('.md', '.txt'))
        if ($docs.Count -gt 0) {
            Write-Warn ('inbox 里只有 {0} 个 .md/.txt，那是文章，走「一键开播」' -f $docs.Count)
        } else {
            Write-Warn 'inbox 里没有音频或字幕；丢文件进去再跑'
        }
        return
    }

    $subArgs = @($Argv)
    if ($audio.Count -gt 0 -and $subs.Count -gt 0) {
        $split = Split-KindArg $Argv
        $subArgs = @($split.Kept)
        if ($split.Kind) {
            Write-Warn ('--kind {0} 只作用于本批音频；字幕按默认 external 整理' -f $split.Kind)
            Write-Info '要让字幕也走 self，单独跑：asrtts.ps1 process --kind self'
        }
    }

    Write-Head ('音频（{0} 个）' -f $audio.Count)
    if ($audio.Count -eq 0) {
        Write-Info '（没有）'
    } else {
        Invoke-PyScript (Join-Path $AsrSkill 'scripts\asr.py') (@($base) + @($Argv))
        if ($script:LastPyCode -ne 0) { $script:ResultCode = 1 }
    }

    Write-Head ('字幕（{0} 个）' -f $subs.Count)
    if ($subs.Count -eq 0) {
        Write-Info '（没有）'
    } else {
        foreach ($s in $subs) {
            Write-Info ('▶ ' + $s.Name)
            $a = @('--srt', $s.FullName) + @($base) + @($subArgs)
            Invoke-PyScript (Join-Path $AsrSkill 'scripts\asr.py') $a
            if ($script:LastPyCode -ne 0) { $script:ResultCode = 1 }
            Write-Host ''
        }
    }

    Show-RecentOut
    if ($script:ResultCode -ne 0) { Write-Warn '有失败项，看 work\error.log' }
}

# ---------- cast：文章转播客 ----------
function Invoke-Cast([string[]]$Argv) {
    $script:ResultCode = 0
    if (-not $Argv) { $Argv = @() }
    Ensure-Dirs
    $cast = Join-Path $CastSkill 'scripts\cast.py'
    Write-Head 'ASRTTS · 文章转播客'
    Write-Info ('数据目录：' + $Root)
    if (-not (Test-Path -LiteralPath $cast)) {
        Write-Err ('找不到 ' + $cast)
        $script:ResultCode = 1
        return
    }
    $base = @('--inbox', (Join-Path $Root 'inbox'),
              '--out', (Join-Path $Root 'out'),
              '--work', (Join-Path $Root 'work'))
    Invoke-PyScript $cast (@($base) + @($Argv))
    if ($script:LastPyCode -ne 0) { $script:ResultCode = 1 }
    Show-RecentOut
}

# ---------- find：字幕反查 ----------
function Invoke-Find([string[]]$Argv) {
    $script:ResultCode = 0
    if (-not $Argv) { $Argv = @() }
    $query = ''
    if ($Argv.Count -ge 1) { $query = [string]$Argv[0] }
    $mode = 'play'
    if ($Argv.Count -ge 2) { $mode = ([string]$Argv[1]).ToLowerInvariant() }
    $pick = 1
    if ($Argv.Count -ge 3) { [void][int]::TryParse([string]$Argv[2], [ref]$pick) }
    if (-not $query) {
        Write-Err '用法：asrtts.ps1 find 关键词 [print|play] [第几条]'
        $script:ResultCode = 1
        return
    }

    Write-Head ('ASRTTS · 字幕反查：' + $query)
    $out = Join-Path $Root 'out'
    $files = @(Get-ChildItem -LiteralPath $out -Recurse -File -Filter '*.srt' -ErrorAction SilentlyContinue)
    if ($files.Count -eq 0) {
        Write-Warn 'out\ 里没有 .srt；先做一期播客'
        $script:ResultCode = 1
        return
    }

    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $hits = New-Object System.Collections.ArrayList
    foreach ($f in $files) {
        $lines = @([System.IO.File]::ReadAllLines($f.FullName, $utf8))
        $ts = $null
        for ($i = 0; $i -lt $lines.Count; $i++) {
            $line = $lines[$i]
            if ($line -match '^\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->') {
                $ts = $Matches[1]
                continue
            }
            if ($line.IndexOf($query, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $text = $line.Trim()
                $j = $i + 1
                while ($j -lt $lines.Count -and $j -le ($i + 2) -and $lines[$j].Trim() -ne '') {
                    $text = $text + ' ' + $lines[$j].Trim()
                    $j++
                }
                $text = $text -replace '<[^>]+>', ''
                [void]$hits.Add([pscustomobject]@{ File = $f.FullName; Ts = $ts; Text = $text })
            }
        }
    }

    if ($hits.Count -eq 0) {
        Write-Warn ('没找到：' + $query)
        $script:ResultCode = 1
        return
    }

    $n = 0
    foreach ($h in $hits) {
        $n++
        $t = '?'
        if ($h.Ts) { $t = $h.Ts }
        Write-Host ('{0,3}) {1}  {2}' -f $n, $t, (Split-Path -Leaf $h.File))
        Write-Host ('      ' + $h.Text)
    }

    $idx = $pick - 1
    if ($idx -lt 0 -or $idx -ge $hits.Count) {
        Write-Err ('只有 {0} 条命中，没有第 {1} 条' -f $hits.Count, $pick)
        $script:ResultCode = 1
        return
    }
    $sel = $hits[$idx]
    Write-Info ('—— 共 {0} 条，选第 {1} 条 ——' -f $hits.Count, $pick)
    if ($mode -eq 'print') { return }

    $sec = Convert-TsToSeconds ([string]$sel.Ts)
    $audio = [System.IO.Path]::ChangeExtension([string]$sel.File, '.m4a')
    if (-not (Test-Path -LiteralPath $audio)) {
        Write-Err ('找不到音频：' + $audio)
        $script:ResultCode = 1
        return
    }
    Write-Info ('跳转：{0}（{1:N1}s）' -f $sel.Ts, $sec)

    $mpv = Get-Command mpv -ErrorAction SilentlyContinue
    $vlc = Get-Command vlc -ErrorAction SilentlyContinue
    if ($mpv) {
        & $mpv.Source --force-window=yes --sub-file=$sel.File --start=$sec $audio
    } elseif ($vlc) {
        & $vlc.Source --start-time=$sec --sub-file=$sel.File $audio
    } else {
        Write-Warn '没装 mpv / vlc：用系统默认播放器打开音频，时间戳已复制到剪贴板'
        try { Set-Clipboard -Value ([string]$sel.Ts) } catch { }
        Start-Process -FilePath $audio
        Write-Info ('请手动跳到 ' + $sel.Ts)
    }
}

# ---------- check：自检 ----------
function Invoke-Check([string[]]$Argv) {
    $script:ResultCode = 0
    if (-not $Argv) { $Argv = @() }
    $online = -not ($Argv -contains '--offline')
    $ok = $true
    Write-Head 'ASRTTS · Windows 自检'
    Write-Info ('数据目录：' + $Root)

    if (-not $PyExe) {
        Write-Err '找不到 Python（装 3.9+ 并加入 PATH）'
        $ok = $false
    } else {
        Write-Ok ('Python ' + $Py.Version + ' · ' + $Py.Exe)
    }

    if ($PyExe) {
        $pre = $PyPre
        $req = & $PyExe @pre -c "import requests;print(requests.__version__)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $req) {
            Write-Ok ('requests ' + [string]$req)
        } else {
            Write-Err 'requests 未安装（运行 setup）'
            $ok = $false
        }
        $iio = & $PyExe @pre -c "import imageio_ffmpeg;print(imageio_ffmpeg.__version__)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $iio) {
            Write-Info ('imageio-ffmpeg ' + [string]$iio)
        } else {
            Write-Info 'imageio-ffmpeg 未安装（有系统 ffmpeg 就不需要）'
        }
    }

    $ff = Get-Ffmpeg
    if ($ff) {
        Write-Ok ('ffmpeg：' + $ff)
        $verLine = @(& $ff -hide_banner -version 2>&1 | Select-Object -First 1)
        if ($verLine.Count -gt 0) { Write-Info ('  ' + [string]$verLine[0]) }
        $enc = (& $ff -hide_banner -encoders 2>&1 | Out-String)
        $flt = (& $ff -hide_banner -filters 2>&1 | Out-String)
        $missing = @()
        if ($enc -notmatch '\blibopus\b') { $missing += 'libopus' }
        if ($enc -notmatch '\baac\b') { $missing += 'aac' }
        if ($flt -notmatch 'loudnorm') { $missing += 'loudnorm' }
        if ($missing.Count -eq 0) {
            Write-Ok '编码器 libopus / aac、滤镜 loudnorm 都在'
        } else {
            Write-Err ('ffmpeg 缺少：' + ($missing -join ', '))
            $ok = $false
        }
    } else {
        Write-Err '找不到 ffmpeg（装 ffmpeg，或运行 setup --with-ffmpeg）'
        $ok = $false
    }

    $cfg = Join-Path $Root 'config.env'
    if (Test-Path -LiteralPath $cfg) {
        Write-Ok ('config.env：' + $cfg)
        $keys = @{}
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        foreach ($line in [System.IO.File]::ReadAllLines($cfg, $utf8)) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
                $keys[$Matches[1]] = $Matches[2].Trim()
            }
        }
        $dk = [string]$keys['DEEPSEEK_API_KEY']
        if ($dk -and $dk.Length -gt 0) {
            Write-Ok ('DEEPSEEK_API_KEY 已配置（{0} 位，不显示内容）' -f $dk.Length)
        } else {
            Write-Err 'DEEPSEEK_API_KEY 缺失'
            $ok = $false
        }
        $prov = 'deepseek'
        if ($keys['TTS_PROVIDER']) { $prov = [string]$keys['TTS_PROVIDER'] }
        $llm = 'deepseek-chat（默认）'
        if ($keys['LLM_MODEL']) { $llm = [string]$keys['LLM_MODEL'] }
        Write-Info ('TTS_PROVIDER：' + $prov + ' · LLM_MODEL：' + $llm)
        if ($prov -eq 'doubao') {
            $db = [string]$keys['DOUBAO_API_KEY']
            if ($db) {
                Write-Ok ('DOUBAO_API_KEY 已配置（{0} 位）' -f $db.Length)
            } else {
                Write-Err 'TTS_PROVIDER=doubao 但 DOUBAO_API_KEY 为空'
                $ok = $false
            }
        }
    } else {
        Write-Err 'config.env 不存在（运行 setup 生成模板，或从 config.env.example 复制）'
        $ok = $false
    }

    $skills = @(
        @('asr-notes', (Join-Path $AsrSkill 'scripts\asr.py')),
        @('tts-cast', (Join-Path $CastSkill 'scripts\cast.py'))
    )
    foreach ($pair in $skills) {
        if (Test-Path -LiteralPath $pair[1]) {
            Write-Ok ('技能 ' + $pair[0] + '：' + $pair[1])
        } else {
            Write-Err ('技能 ' + $pair[0] + ' 缺失：' + $pair[1])
            $ok = $false
        }
    }

    foreach ($n in @('inbox', 'out', 'archive', 'work')) {
        $p = Join-Path $Root $n
        if (Test-Path -LiteralPath $p) {
            $count = @(Get-ChildItem -LiteralPath $p -ErrorAction SilentlyContinue).Count
            Write-Ok ('{0}：{1} 项' -f $n, $count)
        } else {
            Write-Err ($n + ' 目录缺失（运行 setup）')
            $ok = $false
        }
    }

    $ignore = Join-Path $Root '.stignore'
    if (Test-Path -LiteralPath $ignore) {
        $lines = @([System.IO.File]::ReadAllLines($ignore, (New-Object System.Text.UTF8Encoding($false))))
        if ($lines -contains '/config.env') {
            Write-Ok '.stignore 已排除 config.env'
        } else {
            Write-Warn '.stignore 没有排除 /config.env（Key 可能被同步）'
        }
        $logsIgnored = @($lines | Where-Object { $_ -match '^\(\?d\)?/logs/$' }).Count -gt 0
        if ($logsIgnored) { Write-Ok '.stignore 已排除 logs/' }
        $workIgnored = @($lines | Where-Object { $_ -match '^\(\?d\)?/work/?$' }).Count -gt 0
        if ($workIgnored) {
            Write-Warn '.stignore 把整个 work/ 排除了：换机器会重新付转写/合成费'
        } else {
            Write-Ok '.stignore 保留 work/（分片缓存会同步，换机器不用重付）'
        }
    } else {
        Write-Warn '.stignore 不存在（Syncthing 会同步 Key）'
    }

    $st = Get-Process syncthing -ErrorAction SilentlyContinue
    if ($st) {
        Write-Ok ('Syncthing 进程在跑（PID ' + (@($st)[0].Id) + '）')
    } else {
        Write-Info 'Syncthing 没在跑（只在需要同步时启动即可）'
    }
    if (Test-Path -LiteralPath (Join-Path $Root '.stfolder')) {
        Write-Ok '.stfolder 存在（文件夹已被 Syncthing 接管）'
    } else {
        Write-Info '.stfolder 不存在（还没用 Syncthing 同步过）'
    }

    if ($online) {
        Write-Head '在线检查（只调 Key / 模型列表，不产生转写或合成费用）'
        Invoke-PyScript (Join-Path $AsrSkill 'scripts\asr.py') @('--check')
        if ($script:LastPyCode -ne 0) { $ok = $false }
        Invoke-PyScript (Join-Path $CastSkill 'scripts\cast.py') @('--check')
        if ($script:LastPyCode -ne 0) { $ok = $false }
    } else {
        Write-Info '跳过在线检查（--offline）'
    }

    if ($ok) {
        Write-Ok '自检通过'
        $script:ResultCode = 0
    } else {
        Write-Err '自检发现问题，按上面 [XX] 修'
        $script:ResultCode = 1
    }
}

# ---------- setup：安装 / 修复 ----------
function Invoke-Setup([string[]]$Argv) {
    $script:ResultCode = 0
    if (-not $Argv) { $Argv = @() }
    Write-Head 'ASRTTS · Windows 安装 / 修复'
    Ensure-Dirs
    Write-Ok '数据目录已就绪（inbox / out / archive / work / logs）'

    if (-not $PyExe) {
        Write-Err '找不到 Python。装 Python 3.9+，勾选 Add to PATH，然后重开终端。'
        $script:ResultCode = 1
        return
    }
    Write-Ok ('Python ' + $Py.Version + ' · ' + $Py.Exe)

    $mirror = 'https://pypi.tuna.tsinghua.edu.cn/simple'
    $pkgs = @('requests')
    $ff = Get-Ffmpeg
    if (-not $ff -or ($Argv -contains '--with-ffmpeg')) { $pkgs += 'imageio-ffmpeg' }
    Write-Info ('安装/升级：' + ($pkgs -join ' ') + '（镜像 ' + $mirror + '）')
    Invoke-PyRaw (@('-m', 'pip', 'install', '--upgrade') + $pkgs + @('-i', $mirror))
    if ($script:LastPyCode -ne 0) {
        Write-Err 'pip 安装失败（检查网络或换镜像）'
        $script:ResultCode = 1
        return
    }
    Write-Ok 'Python 依赖就绪'

    $cfg = Join-Path $Root 'config.env'
    if (-not (Test-Path -LiteralPath $cfg)) {
        $ex = Join-Path $Root 'config.env.example'
        if (Test-Path -LiteralPath $ex) {
            Copy-Item -LiteralPath $ex -Destination $cfg
            Write-Warn '已生成 config.env，请填入 DEEPSEEK_API_KEY（不要提交、不要同步）'
        } else {
            Write-Err '缺少 config.env.example，无法生成 config.env'
            $script:ResultCode = 1
        }
    } else {
        Write-Ok 'config.env 已存在，未覆盖'
    }

    $ff2 = Get-Ffmpeg
    if ($ff2) {
        Write-Ok ('ffmpeg：' + $ff2)
    } else {
        Write-Err '仍然找不到 ffmpeg：装系统 ffmpeg，或重跑 setup --with-ffmpeg'
        $script:ResultCode = 1
    }

    Write-Head '下一步'
    Write-Info '1) 在 config.env 填 DEEPSEEK_API_KEY（和手机共用同一个 Key）'
    Write-Info '2) 运行自检：powershell -File windows\asrtts.ps1 check'
    Write-Info '3) 录音/字幕丢进 inbox\，双击「一键处理.cmd」'
}

# ---------- test：离线回归 ----------
function Invoke-Test([string[]]$Argv) {
    $script:ResultCode = 0
    if (-not $Argv) { $Argv = @() }
    $tests = Join-Path $Root 'tests'
    Write-Head 'ASRTTS · 离线回归测试（不联网、不花钱）'
    if (-not (Test-Path -LiteralPath $tests)) {
        Write-Err ('找不到 ' + $tests)
        $script:ResultCode = 1
        return
    }
    $failed = $false
    $skipped = $false
    Push-Location $tests
    try {
        foreach ($t in @('t_core.py', 't_subtitle.py')) {
            Write-Head $t
            $one = @($t)
            Invoke-PyRaw $one
            if ($script:LastPyCode -eq 2) {
                $skipped = $true
                Write-Warn '有跳过项'
            } elseif ($script:LastPyCode -ne 0) {
                $failed = $true
                Write-Err '失败'
            }
        }
        Write-Head 'selftest.py（tts-cast）'
        Invoke-PyScript (Join-Path $CastSkill 'scripts\selftest.py') @()
        if ($script:LastPyCode -ne 0) {
            $failed = $true
            Write-Err '失败'
        }
    } finally {
        Pop-Location
    }
    if ($failed) {
        Write-Err '有失败项'
        $script:ResultCode = 1
    } elseif ($skipped) {
        Write-Warn '有跳过项，不能算通过'
        $script:ResultCode = 2
    } else {
        Write-Ok '全部通过'
        $script:ResultCode = 0
    }
}

# ---------- 交互菜单 ----------
function Invoke-Menu {
    while ($true) {
        Write-Host ''
        Write-Host '============================================' -ForegroundColor Cyan
        Write-Host '  ASRTTS · Windows 工作流' -ForegroundColor Cyan
        Write-Host '============================================' -ForegroundColor Cyan
        Write-Host '  1  处理 inbox（录音 / 字幕）'
        Write-Host '  2  文章转播客（TTS）'
        Write-Host '  3  自检（Key / ffmpeg / 目录）'
        Write-Host '  4  字幕反查（找一句话的时间点）'
        Write-Host '  5  跑离线测试'
        Write-Host '  6  安装 / 修复依赖'
        Write-Host '  0  退出'
        $choice = Read-Host '选择'
        switch ($choice) {
            '1' {
                $k = Read-Host '来源：1=自己口述（self） 2=别人的（external） 回车=默认'
                $a = @()
                if ($k -eq '1') { $a = @('--kind', 'self') }
                elseif ($k -eq '2') { $a = @('--kind', 'external') }
                Invoke-Process $a
            }
            '2' {
                $m = Read-Host '1=只估计划（dry-run） 2=试音 300 字 3=正式跑 回车=正式跑'
                $a = @()
                if ($m -eq '1') { $a = @('--dry-run') }
                elseif ($m -eq '2') { $a = @('--preview', '300') }
                Invoke-Cast $a
            }
            '3' { Invoke-Check @() }
            '4' {
                $q = Read-Host '关键词'
                if ($q) { Invoke-Find @($q, 'print') }
            }
            '5' { Invoke-Test @() }
            '6' { Invoke-Setup @() }
            '0' { return }
            default { Write-Warn '无效选择' }
        }
        if ($script:ResultCode -ne 0) {
            Write-Warn ('上一步退出码 ' + $script:ResultCode)
        }
    }
}

function Show-Help {
    Write-Host ''
    Write-Host 'ASRTTS · Windows 工作流' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '用法：windows\asrtts.ps1 <命令> [参数]'
    Write-Host ''
    Write-Host '  process [参数]      处理 inbox\ 里的音频和字幕，参数原样转给 asr.py'
    Write-Host '                      --dry-run / --kind self|external / --force / --asr-only ...'
    Write-Host '  cast [参数]         文章转播客，参数原样转给 cast.py'
    Write-Host '                      --dry-run / --preview 300 / --rewrite / --force ...'
    Write-Host '  find 关键词 [print|play] [N]   在 out\ 的 srt 里反查时间点'
    Write-Host '  check [--offline]   自检 Python / ffmpeg / Key / 目录 / 技能'
    Write-Host '  test                离线回归测试（不联网、不花钱）'
    Write-Host '  setup [--with-ffmpeg]  安装依赖、生成 config.env 模板'
    Write-Host '  menu                交互菜单（双击 ASRTTS.cmd 也是这个）'
    Write-Host ''
    Write-Host '示例：'
    Write-Host '  powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 process --dry-run'
    Write-Host '  powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 cast --dry-run'
    Write-Host '  powershell -ExecutionPolicy Bypass -File windows\asrtts.ps1 find 高尔夫 print'
    Write-Host ''
    Write-Host '环境变量（可选）：ASRTTS_ROOT / ASR_SKILL / CAST_SKILL / ASRTTS_PYTHON / FFMPEG_BIN'
    Write-Host ''
}

# ---------- 主入口 ----------
$Py = Resolve-Python
$PyExe = $null
$PyPre = @()
if ($Py) {
    $PyExe = $Py.Exe
    $PyPre = $Py.Pre
}

$cmd = 'menu'
$rest = @()
if ($args.Count -ge 1) { $cmd = ([string]$args[0]).ToLowerInvariant() }
if ($args.Count -ge 2) { $rest = @($args[1..($args.Count - 1)]) }

$oldLocation = Get-Location
try {
    if (Test-Path -LiteralPath $Root) { Set-Location -LiteralPath $Root }
    Write-RunLog ('cmd=' + $cmd)
    switch ($cmd) {
        'process' { Invoke-Process $rest }
        'asr'     { Invoke-Process $rest }
        'cast'    { Invoke-Cast $rest }
        'tts'     { Invoke-Cast $rest }
        'find'    { Invoke-Find $rest }
        'check'   { Invoke-Check $rest }
        'status'  { Invoke-Check $rest }
        'test'    { Invoke-Test $rest }
        'setup'   { Invoke-Setup $rest }
        'menu'    { Invoke-Menu }
        'help'    { Show-Help }
        default   { Show-Help; $script:ResultCode = 2 }
    }
} catch {
    Write-Err ('运行出错：' + $_.Exception.Message)
    Write-RunLog ('ERROR ' + $_.Exception.ToString())
    $script:ResultCode = 1
} finally {
    Set-Location $oldLocation
    Write-RunLog ('exit=' + $script:ResultCode)
}
exit $script:ResultCode
