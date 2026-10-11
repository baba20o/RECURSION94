[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ToolRoot = Join-Path $ProjectRoot ".toolcache"
$DownloadRoot = Join-Path $ToolRoot "downloads"
$PythonRoot = Join-Path $ToolRoot "python"
$PythonExe = Join-Path $PythonRoot "python.exe"
$PythonArchive = Join-Path $DownloadRoot "python-3.12.10-embed-amd64.zip"
$PythonUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
$PythonSha256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"

New-Item -ItemType Directory -Force -Path $DownloadRoot | Out-Null

function Test-FileHash {
    param([string]$Path, [string]$Expected)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    return $Actual -eq $Expected.ToLowerInvariant()
}

if ($Force -or -not (Test-FileHash -Path $PythonArchive -Expected $PythonSha256)) {
    Write-Host "Downloading pinned portable Python 3.12.10..."
    Invoke-WebRequest -UseBasicParsing -Uri $PythonUrl -OutFile $PythonArchive
}

if (-not (Test-FileHash -Path $PythonArchive -Expected $PythonSha256)) {
    throw "Portable Python archive checksum mismatch: $PythonArchive"
}

if ($Force -or -not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    if (Test-Path -LiteralPath $PythonRoot) {
        Remove-Item -LiteralPath $PythonRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $PythonRoot | Out-Null
    Expand-Archive -LiteralPath $PythonArchive -DestinationPath $PythonRoot -Force
}

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Portable Python extraction did not create $PythonExe"
}

$BootstrapScript = Join-Path $PSScriptRoot "bootstrap_toolchain.py"
$Arguments = @($BootstrapScript, "--root", $ProjectRoot)
if ($Force) {
    $Arguments += "--force"
}

$Process = Start-Process -FilePath $PythonExe -ArgumentList $Arguments -NoNewWindow -Wait -PassThru
if ($Process.ExitCode -ne 0) {
    throw "Toolchain bootstrap failed with exit code $($Process.ExitCode)"
}

Write-Host "RECURSION 94 toolchain bootstrap passed."
