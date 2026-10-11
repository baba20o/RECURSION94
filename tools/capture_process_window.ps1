param(
    [Parameter(Mandatory = $true)]
    [int]$ProcessId,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class R94ProcessWindowCapture
{
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@

$Process = Get-Process -Id $ProcessId
$Process.Refresh()
if ($Process.MainWindowHandle -eq [IntPtr]::Zero) {
    throw "Process $ProcessId has no main window."
}
$Bounds = New-Object R94ProcessWindowCapture+RECT
if (-not [R94ProcessWindowCapture]::GetWindowRect(
    $Process.MainWindowHandle,
    [ref]$Bounds
)) {
    throw "GetWindowRect failed."
}
$Width = $Bounds.Right - $Bounds.Left
$Height = $Bounds.Bottom - $Bounds.Top
if ($Width -lt 1 -or $Height -lt 1) {
    throw "Window dimensions are invalid: ${Width}x${Height}."
}
$OutputDirectory = Split-Path -Parent $OutputPath
if ($OutputDirectory) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}
$Bitmap = New-Object System.Drawing.Bitmap($Width, $Height)
$Graphics = [System.Drawing.Graphics]::FromImage($Bitmap)
try {
    [void][R94ProcessWindowCapture]::SetForegroundWindow(
        $Process.MainWindowHandle
    )
    Start-Sleep -Milliseconds 250
    $Graphics.CopyFromScreen(
        $Bounds.Left,
        $Bounds.Top,
        0,
        0,
        $Bitmap.Size,
        [System.Drawing.CopyPixelOperation]::SourceCopy
    )
}
finally {
    $Graphics.Dispose()
}
try {
    $Bitmap.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
}
finally {
    $Bitmap.Dispose()
}
Write-Output "Captured ${Width}x${Height} foreground screen pixels."
