param(
    [string]$RuntimeDir = $PSScriptRoot
)

$ErrorActionPreference = "Stop"
$RuntimeDir = (Resolve-Path -LiteralPath $RuntimeDir).Path
$manifestPath = Join-Path $RuntimeDir "manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "Local SAM manifest 不存在：$manifestPath"
}
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$uv = Join-Path $RuntimeDir "uv.exe"
$python = Join-Path $RuntimeDir "python\python.exe"
$sitePackages = Join-Path $RuntimeDir "python\Lib\site-packages"
if (-not (Test-Path -LiteralPath $uv)) { throw "uv.exe 不存在：$uv" }
if (-not (Test-Path -LiteralPath $python)) { throw "python.exe 不存在：$python" }
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null

$packages = @(
    "torch==$($manifest.torch)",
    "torchvision==$($manifest.torchvision)",
    "numpy==1.26.4",
    "Pillow==12.1.0",
    "timm==1.0.19",
    "tqdm==4.67.1",
    "ftfy==6.1.1",
    "einops==0.8.1",
    "regex==2025.7.34",
    "iopath==0.1.10",
    "typing_extensions==4.15.0",
    "huggingface_hub==0.35.3",
    "psutil==7.1.0",
    "setuptools==75.8.0"
)

& $uv pip install `
    --target $sitePackages `
    --python-version 3.12 `
    --python-platform x86_64-pc-windows-msvc `
    --index-url $manifest.torch_index `
    --extra-index-url "https://pypi.org/simple" `
    @packages
if ($LASTEXITCODE -ne 0) {
    throw "Local SAM 预编译 wheel 安装失败，退出码：$LASTEXITCODE"
}

$checkOutput = & $python -m local_sam_runtime.server --check
if ($LASTEXITCODE -ne 0) {
    throw "Local SAM runtime import smoke 失败，退出码：$LASTEXITCODE"
}
$check = $checkOutput | ConvertFrom-Json
if ($check.torch_cuda -ne "12.8") {
    throw "Local SAM Torch CUDA 版本异常：预期 12.8，实际 $($check.torch_cuda)"
}
Write-Host $checkOutput

$installed = [ordered]@{
    version = $manifest.version
    installed_at = [DateTime]::UtcNow.ToString("o")
    torch = $manifest.torch
    torchvision = $manifest.torchvision
    sam3_commit = $manifest.sam3_commit
    checkpoint = $manifest.checkpoint
}
$installed | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $RuntimeDir "installed.json") -Encoding UTF8
Write-Host "Local SAM runtime 依赖安装与 import smoke 通过：$RuntimeDir"
