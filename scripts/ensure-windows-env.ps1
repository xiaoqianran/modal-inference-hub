if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "当前仅支持 Windows 环境。"
}

if (-not $env:SystemDrive) {
    if (-not $env:SystemRoot) { throw "无法确定 Windows 系统盘。" }
    $env:SystemDrive = [System.IO.Path]::GetPathRoot($env:SystemRoot).TrimEnd('\')
}
if (-not $env:USERPROFILE) {
    $env:USERPROFILE = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::UserProfile)
    if (-not $env:USERPROFILE) { throw "无法确定 Windows 用户目录。" }
}
if (-not $env:HOMEDRIVE) {
    $env:HOMEDRIVE = [System.IO.Path]::GetPathRoot($env:USERPROFILE).TrimEnd('\')
}
if (-not $env:HOMEPATH) {
    $env:HOMEPATH = $env:USERPROFILE.Substring($env:HOMEDRIVE.Length)
}
if (-not $env:ProgramData) {
    $env:ProgramData = Join-Path $env:SystemDrive "ProgramData"
}
if (-not $env:ALLUSERSPROFILE) {
    $env:ALLUSERSPROFILE = $env:ProgramData
}
