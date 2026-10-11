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

foreach ($CachePath in @($ToolRoot, $DownloadRoot, $PythonRoot, $PythonArchive)) {
    if ((Test-Path -LiteralPath $CachePath) -and
        ((Get-Item -LiteralPath $CachePath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Portable Python cache must not use links/junctions: $CachePath"
    }
}
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

# Verify the interpreter, DLLs, stdlib and exact isolated _pth BEFORE executing
# any project-local Python. An existing python.exe alone is not an attestation.
foreach ($BaselineDirectory in @($ToolRoot, $DownloadRoot, $PythonRoot)) {
    if ((Get-Item -LiteralPath $BaselineDirectory).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Portable Python cache must not use links/junctions: $BaselineDirectory"
    }
}
if ((Get-Item -LiteralPath $PythonArchive).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "Portable Python archive must not be a link/junction"
}
$PathConfigs = @(Get-ChildItem -LiteralPath $PythonRoot -Filter '*._pth' -Force)
if ($PathConfigs.Count -ne 1 -or $PathConfigs[0].Name -cne 'python312._pth') {
    throw "Unexpected portable Python path configuration"
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$BaselineZip = [IO.Compression.ZipFile]::OpenRead($PythonArchive)
try {
    $BaselineNames = @($BaselineZip.Entries | ForEach-Object { $_.Name })
    # Only reviewed package namespaces may coexist with the embedded baseline.
    # In particular an extra DLL must never reach the Windows loader first.
    $PackageFiles = @('typing_extensions.py', 'recursion94-python-packages.json')
    $PackageDirectories = @('__pycache__', 'attr', 'attrs', 'jsonschema',
        'jsonschema_specifications', 'referencing', 'rpds',
        'jsonschema-4.26.0.dist-info', 'attrs-26.1.0.dist-info',
        'jsonschema_specifications-2025.9.1.dist-info', 'referencing-0.37.0.dist-info',
        'rpds_py-0.30.0.dist-info', 'typing_extensions-4.15.0.dist-info')
    foreach ($PythonEntry in (Get-ChildItem -LiteralPath $PythonRoot -Force)) {
        if ($PythonEntry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Portable Python entry must not be a link/junction"
        }
        if ($BaselineNames -ccontains $PythonEntry.Name) { continue }
        if (($PackageFiles -ccontains $PythonEntry.Name) -and -not $PythonEntry.PSIsContainer) { continue }
        if (($PackageDirectories -ccontains $PythonEntry.Name) -and $PythonEntry.PSIsContainer) { continue }
        throw "Unexpected portable Python top-level entry: $($PythonEntry.Name)"
    }
    foreach ($BaselineEntry in $BaselineZip.Entries) {
        if ($BaselineEntry.Name -cne $BaselineEntry.FullName) {
            throw "Unexpected portable Python archive member"
        }
        $BaselinePath = Join-Path $PythonRoot $BaselineEntry.Name
        if (-not (Test-Path -LiteralPath $BaselinePath -PathType Leaf)) {
            throw "Missing portable Python baseline: $($BaselineEntry.Name)"
        }
        $BaselineFile = Get-Item -LiteralPath $BaselinePath -Force
        if ($BaselineFile.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Portable Python baseline must not be a link/junction"
        }
        $BaselineStream = $BaselineEntry.Open()
        $BaselineHasher = [Security.Cryptography.SHA256]::Create()
        try {
            $BaselineExpected = [BitConverter]::ToString($BaselineHasher.ComputeHash($BaselineStream)).Replace('-', '').ToLowerInvariant()
        } finally {
            $BaselineStream.Dispose()
            $BaselineHasher.Dispose()
        }
        if ($BaselineFile.Length -ne $BaselineEntry.Length -or
            -not (Test-FileHash -Path $BaselinePath -Expected $BaselineExpected)) {
            throw "Portable Python baseline mismatch: $($BaselineEntry.Name)"
        }
    }
} finally {
    $BaselineZip.Dispose()
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
