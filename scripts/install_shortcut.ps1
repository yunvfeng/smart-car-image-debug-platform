param([string]$Pythonw)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
if (-not $Pythonw) {
    $pythonPath = (Get-Command python).Source
    $Pythonw = Join-Path (Split-Path $pythonPath) 'pythonw.exe'
}
if (-not (Test-Path -LiteralPath $Pythonw)) { throw "pythonw.exe not found: $Pythonw" }
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'VisionLab.lnk'
if (Test-Path -LiteralPath $shortcutPath) { throw "Shortcut already exists: $shortcutPath" }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $Pythonw
$shortcut.Arguments = '"' + (Join-Path $workspace 'scripts\launch_app.pyw') + '"'
$shortcut.WorkingDirectory = $workspace
$shortcut.Description = 'VisionLab - Smart Car Acquisition and C Vision Replay'
$shortcut.IconLocation = (Join-Path $workspace 'visionlab\assets\visionlab.ico') + ',0'
$shortcut.Save()
Write-Output $shortcutPath
