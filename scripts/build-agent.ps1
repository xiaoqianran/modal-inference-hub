param([string]$TargetTriple, [switch]$Force)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $TargetTriple) {
    $hostLine = rustc -vV | Where-Object { $_ -like "host: *" } | Select-Object -First 1
    if (-not $hostLine) { throw "Unable to determine the Rust target triple." }
    $TargetTriple = $hostLine.Substring(6).Trim()
}
if ($TargetTriple -notmatch "^[a-zA-Z0-9_.-]+$" -or $TargetTriple -notlike "*-windows-*") {
    throw "Unsupported Hub Agent target: $TargetTriple"
}

$outputRoot = Join-Path $projectRoot "src-tauri\binaries"
$outputName = "modal-inference-hub-agent-$TargetTriple"
$outputDirectory = Join-Path $outputRoot $outputName
$outputPath = Join-Path $outputDirectory "$outputName.exe"
$workDirectory = Join-Path $projectRoot "build\pyinstaller"
$inputs = @((Join-Path $projectRoot "pyproject.toml"), (Join-Path $projectRoot "uv.lock"), $PSCommandPath)
$inputs += Get-ChildItem -LiteralPath (Join-Path $projectRoot "hub") -Recurse -File -Filter "*.py" | Select-Object -ExpandProperty FullName
$inputs += Get-ChildItem -LiteralPath (Join-Path $projectRoot "agent") -Recurse -File -Filter "*.py" | Select-Object -ExpandProperty FullName

if (-not $Force -and (Test-Path -LiteralPath $outputPath)) {
    $newestInput = ($inputs | Get-Item | Measure-Object LastWriteTimeUtc -Maximum).Maximum
    if ((Get-Item -LiteralPath $outputPath).LastWriteTimeUtc -ge $newestInput) {
        Write-Host "Hub Agent is up to date: $outputPath"
        exit 0
    }
}
New-Item -ItemType Directory -Force -Path $outputDirectory, $workDirectory | Out-Null

Push-Location $projectRoot
try {
    uv sync --frozen --group build
    if ($LASTEXITCODE -ne 0) { throw "uv dependency sync failed." }
    uv run --frozen --group build pyinstaller --noconfirm --clean --onedir --name $outputName --distpath $outputRoot --workpath $workDirectory --specpath $workDirectory --paths $projectRoot (Join-Path $projectRoot "agent\server.py")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
}
finally {
    Pop-Location
}
if (-not (Test-Path -LiteralPath $outputPath)) { throw "Hub Agent output was not found: $outputPath" }
& (Join-Path $PSScriptRoot "smoke-agent.ps1") -Agent $outputPath
