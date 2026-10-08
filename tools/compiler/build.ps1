# Builds qbsp.exe and vis.exe (ericw-tools 2.0, git main) with the Deep6 clip
# hulls into tools\compiler\bin, on Windows. Needs:
#   Git, CMake, Python 3, Visual Studio 2022 (or its Build Tools, "Desktop
#   development with C++"), and vcpkg (https://vcpkg.io) with VCPKG_ROOT set.
# Run from a "Developer PowerShell for VS 2022":
#   powershell -ExecutionPolicy Bypass -File tools\compiler\build.ps1
# Not tested on Windows by the SDK authors yet; tools\compiler\build.sh is the
# tested Linux build.
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Src = Join-Path $Here 'ericw-tools'
$Rev = if ($env:ERICW_REV) { $env:ERICW_REV } else { '36eec1da2a194467e6baac2f444c2dd04b57d266' }
if (-not $env:VCPKG_ROOT) { throw 'Set VCPKG_ROOT to your vcpkg folder (https://vcpkg.io).' }
if (-not (Test-Path $Src)) {
    git clone --recurse-submodules https://github.com/ericwa/ericw-tools.git $Src
    Push-Location $Src; git checkout -q $Rev; git submodule update --init --recursive; Pop-Location
}
$bf = Join-Path $Src 'common\bspfile.cc'
if (-not (Select-String -Path $bf -Pattern 'Deep6' -Quiet)) {
    # Deep6 clip hulls (map space, z up), measured from the retail BSPs
    python - $bf @'
import sys
p = sys.argv[1]
s = open(p).read()
old = '{{0, 0, 0}, {0, 0, 0}}, {{-16, -16, -32}, {16, 16, 24}}, {{-32, -32, -64}, {32, 32, 24}}};'
new = '{{0, 0, 0}, {0, 0, 0}}, {{-32, -32, -2}, {32, 32, 4}}, {{-64, -64, -2}, {64, 64, 4}}}; /* Deep6 */'
assert s.count(old) >= 1, 'Quake hull table not found'
open(p, 'w').write(s.replace(old, new, 1))
'@
}
& "$env:VCPKG_ROOT\vcpkg.exe" install tbb:x64-windows embree3:x64-windows
$Build = Join-Path $Src 'build'
cmake -S $Src -B $Build -A x64 "-DCMAKE_TOOLCHAIN_FILE=$env:VCPKG_ROOT\scripts\buildsystems\vcpkg.cmake" `
      -DSKIP_TBB_INSTALL=ON -DSKIP_EMBREE_INSTALL=ON
cmake --build $Build --config Release --target qbsp vis
$Bin = Join-Path $Here 'bin'
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Get-ChildItem -Path $Build -Recurse -Include qbsp.exe, vis.exe, *.dll |
    Where-Object { $_.FullName -match '\\Release\\' } | Copy-Item -Destination $Bin -Force
Write-Host "built: $Bin\qbsp.exe $Bin\vis.exe"
