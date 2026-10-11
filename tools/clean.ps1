[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$BuildRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "build"))
$ExpectedBuildRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "build"))

if (-not [string]::Equals(
    $BuildRoot,
    $ExpectedBuildRoot,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing to clean an unresolved target: $BuildRoot"
}

if (Test-Path -LiteralPath $BuildRoot) {
    $BuildItem = Get-Item -LiteralPath $BuildRoot -Force
    if (($BuildItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to recursively clean reparse point: $BuildRoot"
    }
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
    Write-Host "Removed generated build directory: $BuildRoot"
} else {
    Write-Host "Build directory is already clean: $BuildRoot"
}

$KnownGeneratedFiles = @(
    (Join-Path $ProjectRoot "assets\generated\z80_driver.bin")
)
foreach ($Path in $KnownGeneratedFiles) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        Remove-Item -LiteralPath $Path -Force
        Write-Host "Removed generated file: $Path"
    }
}

$KnownGeneratedDirectories = @(
    @{
        Path = (Join-Path $ProjectRoot "assets\generated\content")
        Expected = (Join-Path $ProjectRoot "assets\generated\content")
    },
    @{
        Path = (Join-Path $ProjectRoot "assets\generated\art")
        Expected = (Join-Path $ProjectRoot "assets\generated\art")
    },
    @{
        Path = (Join-Path $ProjectRoot "assets\generated\crc32")
        Expected = (Join-Path $ProjectRoot "assets\generated\crc32")
    }
)
foreach ($Target in $KnownGeneratedDirectories) {
    $ResolvedPath = [System.IO.Path]::GetFullPath($Target.Path)
    $ExpectedPath = [System.IO.Path]::GetFullPath(
        $Target.Expected
    )
    if (-not [string]::Equals(
        $ResolvedPath,
        $ExpectedPath,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to clean an unresolved generated target: $ResolvedPath"
    }
    if (Test-Path -LiteralPath $ResolvedPath) {
        $Item = Get-Item -LiteralPath $ResolvedPath -Force
        if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to recursively clean generated reparse point: $ResolvedPath"
        }
        Remove-Item -LiteralPath $ResolvedPath -Recurse -Force
        Write-Host "Removed generated directory: $ResolvedPath"
    }
}

Write-Host "RECURSION 94 clean passed. Tool cache, source, and evidence were preserved."
