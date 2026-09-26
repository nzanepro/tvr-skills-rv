<#
Open images in OpenRV as one sequence (one frame per image, flip with Left/Right arrows).
If an RV started by this script (network tag) is already running, its contents are replaced
via rvpush set; otherwise a new RV is launched detached with networking on.

  powershell -NoProfile -ExecutionPolicy Bypass -File rv_review.ps1 a.png b.png c.png [-Tag claude] [-InfoStrip] [-Marks "1,4"]
  powershell -NoProfile -ExecutionPolicy Bypass -File rv_review.ps1 -FramesJson DIR\frames.json   (from sheet_panels.py split)
#>
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)][string[]]$Images,
    [string]$Tag = 'claude',
    [switch]$InfoStrip,
    [string]$Marks = '',
    [string]$FramesJson = '',
    [string]$RvBin = 'C:\Program Files\OpenRV\bin'
)
$ErrorActionPreference = 'Stop'
if ($FramesJson) {
    # frames.json from sheet_panels.py split: all frames in order + a mark at each view's first frame
    $j = Get-Content -LiteralPath $FramesJson -Raw | ConvertFrom-Json
    $Images = @($j.frames)
    if (-not $Marks) { $Marks = (@($j.views | ForEach-Object { $_.frame }) -join ',') }
}
if (-not $Images) { throw "no images given" }
# locate RV: -RvBin, then $env:RV_BIN, then rv.exe on PATH, then the usual install folders
if (-not $RvBin) { $RvBin = $env:RV_BIN }
if (-not $RvBin) {
    $cmd = Get-Command rv.exe -ErrorAction SilentlyContinue
    if ($cmd) { $RvBin = Split-Path $cmd.Source }
}
if (-not $RvBin) {
    $cands = @("$env:ProgramFiles\OpenRV\bin") +
        @(Get-ChildItem "$env:ProgramFiles\Autodesk", "$env:ProgramFiles\ShotGrid", "$env:ProgramFiles\Shotgun" -Directory -Filter 'RV*' -ErrorAction SilentlyContinue |
          Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'bin' })
    $RvBin = $cands | Where-Object { Test-Path (Join-Path $_ 'rv.exe') } | Select-Object -First 1
}
if (-not $RvBin -or -not (Test-Path (Join-Path $RvBin 'rv.exe'))) { throw "rv.exe not found: pass -RvBin or set RV_BIN" }
$rv = Join-Path $RvBin 'rv.exe'
$rvpush = Join-Path $RvBin 'rvpush.exe'
$files = @($Images | ForEach-Object { (Resolve-Path -LiteralPath $_).Path })

# never let rvpush spawn RV itself: that child is tied to the calling shell and can die with it
$env:RVPUSH_RV_EXECUTABLE_PATH = 'none'

function Invoke-RvPush([string[]]$a) {
    $ErrorActionPreference = 'Continue'
    $out = & $rvpush -tag $Tag @a 2>&1
    return @{ code = $LASTEXITCODE; out = ($out -join "`n") }
}

# after loading: sequence view, stopped on frame 1, slow fps (space = 1 image/s), -InfoStrip = F7 strip on (RV then saves show_infoStrip=true to its preferences on exit),
# (py-exec runs in separate globals/locals: comprehensions cannot see local imports, so always
#  spell out rv.commands / rv.runtime, which are globals there)
$post = "rv.commands.setViewNode('defaultSequence'); rv.commands.stop(); rv.commands.setFPS(1.0); rv.commands.setFrame(rv.commands.frameStart())"
if ($InfoStrip) { $post +=
        "; rv.runtime.eval('if (rvui.infoStripShown() == 0) rvui.toggleInfoStrip();', ['rvui'])"}
if ($Marks) {
    # timeline marks at each view start: Alt+Right / Alt+Left jump view to view,
    # Ctrl+Right / Ctrl+Left set in/out to the next / previous view (loop one view)
    $post += "; [rv.commands.markFrame(f, True) for f in [" + (($Marks -split ',' | ForEach-Object { [int]$_ }) -join ',') + "]]"
}

$r = Invoke-RvPush (@('set') + $files)
if ($r.code -eq 0) {
    Start-Sleep -Milliseconds 500
    $q = Invoke-RvPush @('py-exec', $post)
    "replaced contents of running RV (tag $Tag) post=$($q.code)"
    exit 0
}

# no running RV with this tag: launch it detached (Start-Process, not rvpush auto-launch)
$quoted = $files | ForEach-Object { '"' + $_ + '"' }
$rvArgs = @('-network', '-networkTag', $Tag) + $quoted
$p = Start-Process -FilePath $rv -ArgumentList $rvArgs -PassThru
$sw = [Diagnostics.Stopwatch]::StartNew()
$ok = $false
while ($sw.Elapsed.TotalSeconds -lt 60) {
    if ((Invoke-RvPush @('py-eval-return', 'len(rv.commands.sources())')).out -match '^\d+$') { $ok = $true; break }
    Start-Sleep -Milliseconds 500
}
if (-not $ok) { throw "RV pid $($p.Id) started but did not answer rvpush within 60 s" }
$q = Invoke-RvPush @('py-exec', $post)
"launched RV pid $($p.Id) (tag $Tag) in $([int]$sw.Elapsed.TotalMilliseconds) ms post=$($q.code)"
