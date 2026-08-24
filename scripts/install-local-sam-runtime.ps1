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

# Torch is installed from exact, hash-pinned cu128 Windows wheels. Do not allow a
# mixed-index resolver to substitute the same-version CPU wheels from PyPI.
& $uv pip install `
    --target $sitePackages `
    --python-version 3.12 `
    --python-platform x86_64-pc-windows-msvc `
    --no-deps `
    $manifest.torch_wheel `
    $manifest.torchvision_wheel
if ($LASTEXITCODE -ne 0) {
    throw "Local SAM CUDA Torch wheel 安装失败，退出码：$LASTEXITCODE"
}

# Explicit inference dependency closure. --no-deps is intentional: none of these
# packages may cause uv to re-resolve/replace the hash-pinned CUDA torch wheels.
$packages = @(
    "certifi==2026.7.22",
    "charset-normalizer==3.5.1",
    "colorama==0.4.6",
    "einops==0.8.1",
    "filelock==3.32.4",
    "fsspec==2026.7.0",
    "ftfy==6.1.1",
    "hf-xet==1.6.0",
    "huggingface_hub==0.35.3",
    "idna==3.19",
    "iopath==0.1.10",
    "jinja2==3.1.6",
    "markupsafe==3.0.3",
    "mpmath==1.3.0",
    "networkx==3.6.1",
    "numpy==1.26.4",
    "packaging==26.3",
    "Pillow==12.1.0",
    "portalocker==4.2.0",
    "psutil==7.1.0",
    "PyYAML==6.0.3",
    "regex==2025.7.34",
    "requests==2.34.2",
    "safetensors==0.8.0",
    "setuptools==75.8.0",
    "sympy==1.14.0",
    "timm==1.0.19",
    "tqdm==4.67.1",
    "typing_extensions==4.15.0",
    "urllib3==2.7.0",
    "wcwidth==0.8.2"
)
& $uv pip install `
    --target $sitePackages `
    --python-version 3.12 `
    --python-platform x86_64-pc-windows-msvc `
    --index-url "https://pypi.org/simple" `
    --no-deps `
    @packages
if ($LASTEXITCODE -ne 0) {
    throw "Local SAM inference 依赖安装失败，退出码：$LASTEXITCODE"
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
    torch = $check.torch
    torchvision = $check.torchvision
    torch_cuda = $check.torch_cuda
    sam3_commit = $manifest.sam3_commit
    checkpoint = $manifest.checkpoint
}
$installed | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $RuntimeDir "installed.json") -Encoding UTF8
Write-Host "Local SAM runtime 依赖安装与 import smoke 通过：$RuntimeDir"
