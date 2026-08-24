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

Copy-Item -Recurse -LiteralPath (Join-Path $projectRoot "local_sam_runtime") -Destination (Join-Path $stage "local_sam_runtime")
Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $stage "manifest.json")
Copy-Item -LiteralPath (Join-Path $projectRoot "scripts\install-local-sam-runtime.ps1") -Destination (Join-Path $stage "install.ps1")

$archive = Join-Path $output "modal-3D-local-sam-bootstrap-windows-x86_64-v$($manifest.version).zip"
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $archive -CompressionLevel Optimal
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
"$hash  $([IO.Path]::GetFileName($archive))" | Set-Content -LiteralPath "$archive.sha256" -Encoding ASCII
Write-Host "Local SAM bootstrap：$archive"
Write-Host "SHA256：$hash"
