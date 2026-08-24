param(
    [string]$OutputDirectory = "build\local-sam"
)

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") {
    throw "Local SAM bootstrap 仅在 Windows runner 上构建。"
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$manifestPath = Join-Path $projectRoot "local_sam_runtime\manifest.json"
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$output = Join-Path $projectRoot $OutputDirectory
$stage = Join-Path $output "bootstrap"
$downloads = Join-Path $output "downloads"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $output
New-Item -ItemType Directory -Force -Path $stage, $downloads | Out-Null

$pythonZip = Join-Path $downloads "python-embed.zip"
$pythonUrl = "https://www.python.org/ftp/python/$($manifest.python)/python-$($manifest.python)-embed-amd64.zip"
Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonZip
$pythonDir = Join-Path $stage "python"
Expand-Archive -LiteralPath $pythonZip -DestinationPath $pythonDir

$pth = Get-ChildItem -LiteralPath $pythonDir -Filter "python312._pth" | Select-Object -First 1
if (-not $pth) { throw "Python embedded _pth 文件不存在。" }
@(
    "python312.zip",
    ".",
    "Lib\site-packages",
    "..\sam3-src",
    "..",
    "import site"
) | Set-Content -LiteralPath $pth.FullName -Encoding ASCII

$uvCommand = Get-Command uv -ErrorAction Stop
Copy-Item -LiteralPath $uvCommand.Source -Destination (Join-Path $stage "uv.exe")

$samZip = Join-Path $downloads "sam3.zip"
$samUrl = "https://github.com/facebookresearch/sam3/archive/$($manifest.sam3_commit).zip"
Invoke-WebRequest -Uri $samUrl -OutFile $samZip
$samExtract = Join-Path $downloads "sam3"
Expand-Archive -LiteralPath $samZip -DestinationPath $samExtract
$samRoot = Get-ChildItem -LiteralPath $samExtract -Directory | Select-Object -First 1
if (-not $samRoot) { throw "SAM3 source archive 为空。" }
New-Item -ItemType Directory -Force -Path (Join-Path $stage "sam3-src") | Out-Null
Copy-Item -Recurse -LiteralPath (Join-Path $samRoot.FullName "sam3") -Destination (Join-Path $stage "sam3-src\sam3")
Copy-Item -LiteralPath (Join-Path $samRoot.FullName "LICENSE") -Destination (Join-Path $stage "sam3-src\LICENSE")

# Windows image-only runtime does not use the interactive/video tracker stack. Upstream
# model_builder imports those modules eagerly, which imports Triton even when
# enable_inst_interactivity=False. Triton is not required by our image-text path and
# does not have an official Windows wheel in this pinned stack, so make those imports
# lazy by removing them from module import time. Future annotations keep the unused
# tracker/video return annotations from being evaluated during import.
$modelBuilder = Join-Path $stage "sam3-src\sam3\model_builder.py"
$modelBuilderText = Get-Content -Raw -LiteralPath $modelBuilder
$expectedImports = @(
    "from sam3.model.sam1_task_predictor import SAM3InteractiveImagePredictor",
    "from sam3.model.sam3_tracking_predictor import Sam3TrackerPredictor",
    "from sam3.model.sam3_video_inference import Sam3VideoInferenceWithInstanceInteractivity",
    "from sam3.model.sam3_video_predictor import Sam3VideoPredictorMultiGPU",
    "from sam3.model.video_tracking_multiplex import VideoTrackingDynamicMultiplex"
)
foreach ($importLine in $expectedImports) {
    if (-not $modelBuilderText.Contains($importLine)) {
        throw "SAM3 Windows image-only patch drifted; missing expected import: $importLine"
    }
    $modelBuilderText = $modelBuilderText.Replace("$importLine`n", "")
}
if (-not $modelBuilderText.StartsWith("from __future__ import annotations")) {
    $modelBuilderText = "from __future__ import annotations`n`n" + $modelBuilderText
}
Set-Content -LiteralPath $modelBuilder -Value $modelBuilderText -Encoding UTF8

Copy-Item -Recurse -LiteralPath (Join-Path $projectRoot "local_sam_runtime") -Destination (Join-Path $stage "local_sam_runtime")
Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $stage "manifest.json")
Copy-Item -LiteralPath (Join-Path $projectRoot "scripts\install-local-sam-runtime.ps1") -Destination (Join-Path $stage "install.ps1")

$archive = Join-Path $output "modal-3D-local-sam-bootstrap-windows-x86_64-v$($manifest.version).zip"
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $archive -CompressionLevel Optimal
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
"$hash  $([IO.Path]::GetFileName($archive))" | Set-Content -LiteralPath "$archive.sha256" -Encoding ASCII
Write-Host "Local SAM bootstrap：$archive"
Write-Host "SHA256：$hash"
