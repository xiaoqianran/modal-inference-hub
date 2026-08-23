param(
    [string]$TargetTriple,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ($env:OS -ne "Windows_NT") {
    throw "当前仅支持在 Windows 上构建本地代理。"
}

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

$outputDirectory = Join-Path $projectRoot "src-tauri\binaries"
$outputName = "modal-3d-agent-$TargetTriple"
$outputPath = Join-Path $outputDirectory "$outputName.exe"
$workDirectory = Join-Path $projectRoot "build\pyinstaller"

$inputPaths = @(
    (Join-Path $projectRoot "pyproject.toml"),
    (Join-Path $projectRoot "uv.lock"),
    $PSCommandPath
)
$inputPaths += Get-ChildItem -LiteralPath (Join-Path $projectRoot "agent") -Recurse -File -Filter "*.py" |
    Select-Object -ExpandProperty FullName

if (-not $Force -and (Test-Path -LiteralPath $outputPath)) {
    $outputTime = (Get-Item -LiteralPath $outputPath).LastWriteTimeUtc
    $newestInputTime = ($inputPaths | Get-Item | Measure-Object -Property LastWriteTimeUtc -Maximum).Maximum
    if ($outputTime -ge $newestInputTime) {
        Write-Host "本地代理已是最新版本：$outputPath"
        exit 0
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
        --onefile `
        --name $outputName `
        --distpath $outputDirectory `
        --workpath $workDirectory `
        --specpath $workDirectory `
        --paths $projectRoot `
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
$agentProcess = Start-Process -FilePath $outputPath -WindowStyle Hidden -PassThru
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
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
