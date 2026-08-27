param(
    [string]$TargetTriple,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$windowsEnvScript = Join-Path $PSScriptRoot "ensure-windows-env.ps1"
. $windowsEnvScript

if (-not $TargetTriple) {
    $hostLine = rustc -vV | Where-Object { $_ -like "host: *" } | Select-Object -First 1
    if (-not $hostLine) {
        throw "无法确定 Rust 目标平台，请确认已安装 rustc。"
    }
    $TargetTriple = $hostLine.Substring(6).Trim()
}

if ($TargetTriple -notmatch "^[a-zA-Z0-9_.-]+$") {
    throw "Rust 目标平台无效：$TargetTriple"
}
if ($TargetTriple -notlike "*-windows-*") {
    throw "不支持的本地代理目标平台：$TargetTriple（必须为 Windows）。"
}

$outputRoot = Join-Path $projectRoot "src-tauri\binaries"
$outputName = "modal-3d-agent-$TargetTriple"
$outputDirectory = Join-Path $outputRoot $outputName
$outputPath = Join-Path $outputDirectory "$outputName.exe"
$legacyOutputPath = Join-Path $outputRoot "$outputName.exe"
$workDirectory = Join-Path $projectRoot "build\pyinstaller"
$hookDirectory = Join-Path $projectRoot "scripts\pyinstaller-hooks"

# 0.3.x 的 onefile Agent 会在 binaries 根目录留下约 2 GiB 的旧 EXE。
# 当前 Tauri 只打包 onedir 子目录，安全移除这个精确命名的历史生成物。
if (Test-Path -LiteralPath $legacyOutputPath -PathType Leaf) {
    $resolvedLegacy = (Resolve-Path -LiteralPath $legacyOutputPath).Path
    $resolvedOutputRoot = (Resolve-Path -LiteralPath $outputRoot).Path
    if ((Split-Path -Parent $resolvedLegacy) -ne $resolvedOutputRoot) {
        throw "拒绝删除 binaries 目录之外的旧 Agent：$resolvedLegacy"
    }
    Remove-Item -LiteralPath $resolvedLegacy -Force
    Write-Host "已清理旧 onefile Agent：$resolvedLegacy"
}

$inputPaths = @(
    (Join-Path $projectRoot "pyproject.toml"),
    (Join-Path $projectRoot "uv.lock"),
    $PSCommandPath,
    $windowsEnvScript
)
$inputPaths += Get-ChildItem -LiteralPath (Join-Path $projectRoot "agent") -Recurse -File -Filter "*.py" |
    Select-Object -ExpandProperty FullName
$inputPaths += Get-ChildItem -LiteralPath $hookDirectory -File -Filter "*.py" |
    Select-Object -ExpandProperty FullName

if (-not $Force -and (Test-Path -LiteralPath $outputPath)) {
    $outputTime = (Get-Item -LiteralPath $outputPath).LastWriteTimeUtc
    $newestInputTime = ($inputPaths | Get-Item | Measure-Object -Property LastWriteTimeUtc -Maximum).Maximum
    if ($outputTime -ge $newestInputTime) {
        Write-Host "本地代理已是最新版本：$outputPath"
        exit 0
    }
}

if (Test-Path -LiteralPath $outputPath) {
    try {
        $outputLock = [System.IO.File]::Open(
            $outputPath,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $outputLock.Dispose()
    }
    catch {
        throw "本地代理可执行文件正在使用中，请先关闭 Modal 3D 客户端或代理进程后再构建：$outputPath"
    }
}

New-Item -ItemType Directory -Force -Path $outputDirectory, $workDirectory | Out-Null

Push-Location $projectRoot
try {
    uv sync --frozen --group build
    if ($LASTEXITCODE -ne 0) {
        throw "uv 依赖同步失败，退出码：$LASTEXITCODE"
    }

    uv run --frozen --group build pyinstaller `
        --noconfirm `
        --clean `
        --onedir `
        --name $outputName `
        --distpath $outputRoot `
        --workpath $workDirectory `
        --specpath $workDirectory `
        --paths $projectRoot `
        --additional-hooks-dir $hookDirectory `
        --exclude-module rembg `
        --exclude-module pymatting `
        --exclude-module numba `
        --exclude-module skimage `
        --exclude-module nvidia `
        (Join-Path $projectRoot "agent\server.py")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 打包失败，退出码：$LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $outputPath)) {
    throw "本地代理打包完成，但没有生成预期文件：$outputPath"
}

$smokeToken = [Guid]::NewGuid().ToString("N")
$smokeHandshake = Join-Path $workDirectory "smoke-$smokeToken.port"
$env:MODAL_3D_AGENT_TOKEN = $smokeToken
$env:MODAL_3D_AGENT_HANDSHAKE = $smokeHandshake
$env:MODAL_3D_AGENT_SMOKE = "1"
$agentProcess = Start-Process -FilePath $outputPath -WindowStyle Hidden -PassThru
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    while (-not (Test-Path -LiteralPath $smokeHandshake) -and [DateTime]::UtcNow -lt $deadline) {
        if ($agentProcess.HasExited) {
            throw "本地代理在启动冒烟测试期间意外退出。"
        }
        Start-Sleep -Milliseconds 100
    }
    if (-not (Test-Path -LiteralPath $smokeHandshake)) {
        throw "本地代理启动冒烟测试超时。"
    }

    $smokePort = Get-Content -Raw -LiteralPath $smokeHandshake
    $health = Invoke-RestMethod `
        -Uri "http://127.0.0.1:$smokePort/health" `
        -Headers @{ "X-Modal-3D-Session" = $smokeToken } `
        -TimeoutSec 10
    if (-not $health.ok) {
        throw "本地代理未通过健康状态检查。"
    }
}
finally {
    if (-not $agentProcess.HasExited) {
        & taskkill /PID $agentProcess.Id /T /F | Out-Null
    }
    if (Test-Path -LiteralPath $smokeHandshake) {
        $resolvedHandshake = (Resolve-Path -LiteralPath $smokeHandshake).Path
        if (-not $resolvedHandshake.StartsWith($workDirectory, [StringComparison]::OrdinalIgnoreCase)) {
            throw "拒绝删除非预期的冒烟测试握手文件：$resolvedHandshake"
        }
        Remove-Item -LiteralPath $resolvedHandshake -Force
    }
}

Write-Host "本地代理已打包并通过健康检查：$outputPath"
