#Requires -Version 7
<#
Build the 消息月报 (ChatMonthly) release files from this repository.

  pwsh tools\build.ps1          ->  dist\ChatMonthly-<version>-Setup.exe     (installer)
                                    dist\ChatMonthly-<version>-Portable.zip  (portable: unzip anywhere, run ChatMonthly.exe)

Needs Python 3.11 or later (only on this machine; the installed program carries its own) and Inno Setup 6
(winget install JRSoftware.InnoSetup). The build environment lives in build\venv and is reused; everything
under build\ and dist\ can be deleted at any time.
#>
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Build = Join-Path $Repo 'build'
$Venv = Join-Path $Build 'venv'
$Python = Join-Path $Venv 'Scripts\python.exe'

function Find-Python {
    $candidates = @(@('py', '-3.12'), @('py', '-3'), @('python'))
    foreach ($c in $candidates) {
        if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
        $exe = & $c[0] @($c | Select-Object -Skip 1) -c 'import sys; print(sys.executable if sys.version_info >= (3, 11) else "")' 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
    }
    throw '没有找到 Python 3.11 或更高版本。'
}

function Find-Iscc {
    $found = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    foreach ($dir in "$env:LOCALAPPDATA\Programs\Inno Setup 6", "${env:ProgramFiles(x86)}\Inno Setup 6", "$env:ProgramFiles\Inno Setup 6") {
        if (Test-Path (Join-Path $dir 'ISCC.exe')) { return Join-Path $dir 'ISCC.exe' }
    }
    throw '没有找到 Inno Setup 6。安装：winget install JRSoftware.InnoSetup'
}

$version = [regex]::Match((Get-Content -Raw (Join-Path $Repo 'src\mr\__init__.py')), "__version__ = '([^']+)'").Groups[1].Value
if (-not $version) { throw '在 src\mr\__init__.py 中没有找到 __version__。' }
$iscc = Find-Iscc

if (-not (Test-Path $Python)) {
    $base = Find-Python
    Write-Host "建立构建环境（$base）…"
    & $base -m venv $Venv
    if ($LASTEXITCODE) { throw '建立构建环境失败。' }
}
Write-Host '安装依赖…'
& $Python -m pip install --disable-pip-version-check -q -r (Join-Path $Repo 'packaging\requirements-build.txt')
if ($LASTEXITCODE) { throw '安装依赖失败，见上方 pip 的输出。' }

Write-Host "打包程序 $version…"
$dist = Join-Path $Build 'dist'
& $Python -m PyInstaller --noconfirm --clean --log-level WARN --distpath $dist --workpath (Join-Path $Build 'pyinstaller') (Join-Path $Repo 'packaging\ChatMonthly.spec')
if ($LASTEXITCODE) { throw 'PyInstaller 打包失败，见上方输出。' }

# The packed command line loads the pipeline and the model client; a missing module shows up here, not after installing.
$check = & (Join-Path $dist 'ChatMonthly\mr.exe') --help 2>&1
if ($LASTEXITCODE) { throw "打包后的 mr.exe 无法运行：`n$($check -join "`n")" }

# The licence travels with the program: the installer and the portable zip both take the folder as it is now.
Copy-Item (Join-Path $Repo 'LICENSE') (Join-Path $dist 'ChatMonthly\LICENSE.txt')

Write-Host '生成安装程序…'
& $iscc /Q "/DAppVersion=$version" (Join-Path $Repo 'packaging\installer.iss')
if ($LASTEXITCODE) { throw "Inno Setup 编译失败（$LASTEXITCODE）。" }
$setup = Join-Path $Repo "dist\ChatMonthly-$version-Setup.exe"
Write-Host "已生成 $setup（$([math]::Round((Get-Item $setup).Length / 1MB, 1)) MB）"

Write-Host '生成绿色版…'
$portable = Join-Path $Repo "dist\ChatMonthly-$version-Portable.zip"
Remove-Item $portable -ErrorAction SilentlyContinue           # a build output of this script, rebuilt every time
# An empty 配置 folder beside the program makes it keep its configuration there instead of in AppData (config.HOME).
# Added only now, after the installer was compiled, so the installed program keeps using AppData.
$beside = New-Item -ItemType Directory -Force (Join-Path $dist 'ChatMonthly\配置')
Compress-Archive -Path (Join-Path $dist 'ChatMonthly') -DestinationPath $portable -CompressionLevel Optimal
Remove-Item $beside                                           # empty, and made by this script just above
Write-Host "已生成 $portable（$([math]::Round((Get-Item $portable).Length / 1MB, 1)) MB）"
