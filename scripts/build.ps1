param([string]$ImageSource)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
if (-not $ImageSource) { $ImageSource = Join-Path $workspace 'smart-car/project/code/image.c' }
$ImageSource = (Resolve-Path -LiteralPath $ImageSource).Path
# MinGW make cannot stat UTF-8 paths on this installation. A junction gives
# CMake an ASCII path while continuing to compile the very same source files.
$hasher = [Security.Cryptography.SHA256]::Create()
$hash = [BitConverter]::ToString($hasher.ComputeHash(
    [Text.Encoding]::UTF8.GetBytes($workspace))).Replace('-', '').Substring(0, 12)
$hasher.Dispose()
$aliasPath = Join-Path ([IO.Path]::GetTempPath()) "VisionLab-$hash"
if (-not (Test-Path -LiteralPath $aliasPath)) {
    New-Item -ItemType Junction -Path $aliasPath -Target $workspace | Out-Null
}
$aliasItem = Get-Item -LiteralPath $aliasPath
if ($aliasItem.LinkType -ne 'Junction' -or $aliasItem.Target -ne $workspace) {
    throw "Build alias is occupied by another directory: $aliasPath"
}
$buildDirectory = Join-Path $workspace 'build'
New-Item -ItemType Directory -Path $buildDirectory -Force | Out-Null
$sourceRecord = Join-Path $buildDirectory ("sources-" + [Guid]::NewGuid().ToString('N') + '.json')
try {
python "$workspace/scripts/stamp_build.py" --capture $sourceRecord --image-source $ImageSource
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$algorithmDirectory = Split-Path -Parent $ImageSource
$algorithmHash = [Security.Cryptography.SHA256]::Create()
$algorithmId = [BitConverter]::ToString($algorithmHash.ComputeHash([Text.Encoding]::UTF8.GetBytes($algorithmDirectory))).Replace('-', '').Substring(0, 12)
$algorithmHash.Dispose()
$algorithmAlias = Join-Path ([IO.Path]::GetTempPath()) "VisionLab-source-$algorithmId"
if (-not (Test-Path -LiteralPath $algorithmAlias)) {
    New-Item -ItemType Junction -Path $algorithmAlias -Target $algorithmDirectory | Out-Null
}
$algorithmItem = Get-Item -LiteralPath $algorithmAlias
if ($algorithmItem.LinkType -ne 'Junction' -or $algorithmItem.Target -ne $algorithmDirectory) {
    throw "Algorithm alias is occupied: $algorithmAlias"
}
$entryAlias = Join-Path $algorithmAlias (Split-Path -Leaf $ImageSource)
cmake -S $aliasPath -B "$aliasPath/build/pc" -G 'MinGW Makefiles' `
    -DCMAKE_BUILD_TYPE=Debug -DCMAKE_C_COMPILER=gcc -DCMAKE_DEPENDS_USE_COMPILER=FALSE `
    "-DVISIONLAB_SOURCE_ROOT=$($workspace.Replace('\', '/'))" "-DVISIONLAB_IMAGE_SOURCE=$($entryAlias.Replace('\', '/'))"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
cmake --build "$aliasPath/build/pc"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$debugAlias = $aliasPath.Replace('\', '/')
"set substitute-path $debugAlias ." | Set-Content -Encoding ASCII -LiteralPath "$workspace/build/gdb-paths.gdb"
python "$workspace/scripts/stamp_build.py" "$workspace/build/pc/visionlab_replay.exe" $sourceRecord --image-source $ImageSource
exit $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $sourceRecord) { Remove-Item -LiteralPath $sourceRecord }
}
