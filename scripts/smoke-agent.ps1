$ErrorActionPreference = "Stop"

$agent = (Get-ChildItem -LiteralPath "src-tauri/binaries" -Filter "modal-3d-agent-*.exe" -File |
  Select-Object -First 1).FullName
if (-not $agent) { throw "找不到已构建的本地代理可执行文件。" }
$handshake = Join-Path $env:TEMP ("modal-3d-agent-smoke-" + [guid]::NewGuid().ToString("N") + ".port")
$dataDir = Join-Path $env:TEMP ("modal-3d-agent-smoke-data-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $dataDir | Out-Null
[byte[]]$tokenBytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($tokenBytes)
$token = [BitConverter]::ToString($tokenBytes).Replace("-", "").ToLowerInvariant()
$env:MODAL_3D_AGENT_TOKEN = $token
$env:MODAL_3D_AGENT_HANDSHAKE = $handshake
$env:MODAL_3D_AGENT_DATA_DIR = $dataDir

function Invoke-HttpAllowError {
  param(
    [string]$Uri,
    [string]$Method = "Get",
    [hashtable]$Headers,
    [string]$Body
  )
  $parameters = @{ Uri = $Uri; Method = $Method; Headers = $Headers; UseBasicParsing = $true }
  if ((Get-Command Invoke-WebRequest).Parameters.ContainsKey("SkipHttpErrorCheck")) {
    $parameters.SkipHttpErrorCheck = $true
  }
  if ($Body) {
    $parameters.ContentType = "application/json"
    $parameters.Body = $Body
  }
  try {
    $response = Invoke-WebRequest @parameters
    return [pscustomobject]@{ StatusCode = [int]$response.StatusCode; Content = $response.Content }
  } catch {
    if (-not $_.Exception.Response) { throw }
    $response = $_.Exception.Response
    $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
    try { $content = $reader.ReadToEnd() } finally { $reader.Dispose() }
    return [pscustomobject]@{ StatusCode = [int]$response.StatusCode; Content = $content }
  }
}

$process = Start-Process -FilePath $agent -PassThru -WindowStyle Hidden
try {
  $deadline = (Get-Date).AddSeconds(30)
  while (-not (Test-Path $handshake)) {
    if ($process.HasExited) { throw "本地代理在启动期间退出：$($process.ExitCode)" }
    if ((Get-Date) -ge $deadline) { throw "本地代理启动握手超时。" }
    Start-Sleep -Milliseconds 100
  }

  $port = [int](Get-Content $handshake -Raw).Trim()
  $base = "http://127.0.0.1:$port"
  $unauth = Invoke-HttpAllowError "$base/health"
  if ($unauth.StatusCode -ne 401) { throw "未授权请求应返回 401，实际为 $($unauth.StatusCode)。" }

  $headers = @{ "X-Modal-3D-Session" = $token }
  $health = Invoke-RestMethod "$base/health" -Headers $headers
  if (-not $health.ok) { throw "本地代理健康检查失败。" }

  $bad = Invoke-HttpAllowError "$base/modal/connect" "Post" $headers '{"token_id":"bad","token_secret":"bad"}'
  if ($bad.StatusCode -ne 401) { throw "无效 Modal 凭据应返回 401，实际为 $($bad.StatusCode)。" }

  $models = Invoke-HttpAllowError "$base/v1/models" "Get" $headers
  if ($models.StatusCode -ne 503) { throw "Fresh CI 环境未连接 Modal 且无 capability cache 时 /v1/models 应返回 503，实际为 $($models.StatusCode)。" }

  $capabilities = Invoke-RestMethod "$base/v1/capabilities" -Headers $headers
  if ($capabilities.preprocessing.kind -ne "rembg") { throw "本地预处理引擎应为 rembg。" }
  if ($capabilities.preprocessing.engine -ne "birefnet-general") { throw "默认 rembg 引擎应为 birefnet-general。" }
  if ($capabilities.preprocessing.provider_preference -ne "cpu") { throw "默认 rembg provider 偏好应为 cpu。" }
  if ($capabilities.preprocessing.provider -ne "cpu") { throw "Fresh CI 环境默认应实际使用 cpu。" }
  if ($capabilities.preprocessing.available_providers -notcontains "cpu") { throw "CPU provider 必须可用。" }
  if ($capabilities.preprocessing.canonical_size -ne 1024) { throw "Canonical 尺寸契约应为 1024。" }
  if (-not $capabilities.preprocessing.local_only) { throw "2D 预处理必须标记为 local_only。" }

  $preprocess = Invoke-RestMethod "$base/v1/preprocess/status" -Headers $headers
  if ($preprocess.engine -ne "birefnet-general") { throw "预处理状态引擎不匹配。" }
  $providerBody = @{ provider = "cpu" } | ConvertTo-Json
  $provider = Invoke-RestMethod "$base/v1/preprocess/provider" -Headers $headers -Method Post -ContentType "application/json" -Body $providerBody
  if ($provider.provider_preference -ne "cpu") { throw "CPU provider 设置未持久化。" }
  if ($preprocess.model_downloaded) { throw "Fresh CI 环境不应预装 birefnet-general 模型。" }

  $image = Join-Path $dataDir "smoke.png"
  # 1x1 真彩 PNG（合法图片，供 image_input 严格解析通过）。
  $pngBase64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
  [IO.File]::WriteAllBytes($image, [Convert]::FromBase64String($pngBase64))
  $project = Invoke-RestMethod "$base/v1/projects" -Method Post -Headers $headers -Form @{ file = Get-Item $image }
  if ($project.status -ne "draft") { throw "新 Project 状态应为 draft，实际为 $($project.status)。" }

  $projects = Invoke-RestMethod "$base/v1/projects" -Headers $headers
  if ($projects.Count -ne 1 -or $projects[0].id -ne $project.id) { throw "Project 列表未返回刚创建的项目。" }

  $deleted = Invoke-RestMethod "$base/v1/projects/$($project.id)" -Method Delete -Headers $headers
  if ($deleted.deleted -ne $project.id) { throw "Project 删除返回的 ID 不匹配。" }
  $missing = Invoke-HttpAllowError "$base/v1/projects/$($project.id)" "Get" $headers
  if ($missing.StatusCode -ne 404) { throw "已删除 Project 应返回 404，实际为 $($missing.StatusCode)。" }

  Write-Host "本地代理冒烟测试通过，随机端口：$port"
}
finally {
  if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
  Remove-Item $handshake -ErrorAction SilentlyContinue
  Remove-Item Env:MODAL_3D_AGENT_TOKEN -ErrorAction SilentlyContinue
  Remove-Item Env:MODAL_3D_AGENT_HANDSHAKE -ErrorAction SilentlyContinue
  Remove-Item Env:MODAL_3D_AGENT_DATA_DIR -ErrorAction SilentlyContinue
  Remove-Item $dataDir -Recurse -Force -ErrorAction SilentlyContinue
}
