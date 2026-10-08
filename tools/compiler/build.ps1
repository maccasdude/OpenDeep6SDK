# Builds qbsp.exe and vis.exe (ericw-tools 2.0, git main) with the Deep6 clip
# hulls into tools\compiler\bin, on Windows. Needs:
#   Git, CMake, Python 3 and Visual Studio 2022 (or its Build Tools, "Desktop
#   development with C++").
# Run from a "Developer PowerShell for VS 2022":
#   powershell -ExecutionPolicy Bypass -File tools\compiler\build.ps1
# Embree 4 and oneTBB are downloaded as the official prebuilt packages, the
# same ones ericw-tools' own build-windows.ps1 uses (into tools\compiler\deps).
# tools\compiler\build.sh is the Linux build.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'          # Invoke-WebRequest is slow with the progress bar
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Src = Join-Path $Here 'ericw-tools'
$Deps = Join-Path $Here 'deps'
$Rev = if ($env:ERICW_REV) { $env:ERICW_REV } else { '36eec1da2a194467e6baac2f444c2dd04b57d266' }

if (-not (Test-Path $Src)) {
    git clone --recurse-submodules https://github.com/ericwa/ericw-tools.git $Src
    if ($LASTEXITCODE) { throw 'git clone of ericw-tools failed' }
    Push-Location $Src; git checkout -q $Rev; git submodule update --init --recursive; Pop-Location
    if ($LASTEXITCODE) { throw "git checkout of $Rev failed" }
}

# Embree 4.4.0 and oneTBB 2021.11.0 (prebuilt, x64)
New-Item -ItemType Directory -Force -Path $Deps | Out-Null
$Embree = Join-Path $Deps 'embree-4.4.0'
$Tbb = Join-Path $Deps 'oneapi-tbb-2021.11.0'
if (-not (Test-Path $Embree)) {
    $z = Join-Path $Deps 'embree.zip'
    Invoke-WebRequest 'https://github.com/RenderKit/embree/releases/download/v4.4.0/embree-4.4.0.x64.windows.zip' -OutFile $z
    Expand-Archive -Path $z -DestinationPath $Embree
    Remove-Item $z
}
if (-not (Test-Path $Tbb)) {
    $z = Join-Path $Deps 'tbb.zip'
    Invoke-WebRequest 'https://github.com/uxlfoundation/oneTBB/releases/download/v2021.11.0/oneapi-tbb-2021.11.0-win.zip' -OutFile $z
    Expand-Archive -Path $z -DestinationPath $Deps      # the zip holds oneapi-tbb-2021.11.0\
    Remove-Item $z
}

$bf = Join-Path $Src 'common\bspfile.cc'
if (-not (Select-String -Path $bf -Pattern 'Deep6' -Quiet)) {
    # Deep6 clip hulls (map space, z up), measured from the retail BSPs
    $patch = @'
import sys
p = sys.argv[1]
s = open(p).read()
old = '{{0, 0, 0}, {0, 0, 0}}, {{-16, -16, -32}, {16, 16, 24}}, {{-32, -32, -64}, {32, 32, 24}}};'
new = '{{0, 0, 0}, {0, 0, 0}}, {{-32, -32, -2}, {32, 32, 4}}, {{-64, -64, -2}, {64, 64, 4}}}; /* Deep6 */'
assert s.count(old) >= 1, 'Quake hull table not found'
open(p, 'w').write(s.replace(old, new, 1))
'@
    # the script goes to python on stdin (an argument would be ignored)
    $patch | python - $bf
    if ($LASTEXITCODE) { throw 'patching the Deep6 hulls into bspfile.cc failed' }
}

$Build = Join-Path $Src 'build'
cmake -S $Src -B $Build -A x64 `
      "-Dembree_DIR=$Embree\lib\cmake\embree-4.4.0" "-DTBB_DIR=$Tbb\lib\cmake\tbb" `
      -DSKIP_TBB_INSTALL=ON -DSKIP_EMBREE_INSTALL=ON `
      -DENABLE_LIGHTPREVIEW=OFF -DDISABLE_TESTS=ON -DDISABLE_DOCS=ON
if ($LASTEXITCODE) { throw 'cmake configure failed' }
cmake --build $Build --config Release --target qbsp vis
if ($LASTEXITCODE) { throw 'cmake build failed' }

$Bin = Join-Path $Here 'bin'
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Get-ChildItem -Path $Build -Recurse -Include qbsp.exe, vis.exe, *.dll |
    Where-Object { $_.FullName -match '\\Release\\' } | Copy-Item -Destination $Bin -Force
# run-time DLLs of embree and TBB, next to the exes
Copy-Item (Join-Path $Embree 'bin\*.dll') $Bin -Force
Get-ChildItem (Join-Path $Tbb 'redist\intel64\vc14') -Filter *.dll |
    Where-Object { $_.Name -notmatch '_debug' } | Copy-Item -Destination $Bin -Force
foreach ($exe in 'qbsp.exe', 'vis.exe') {
    if (-not (Test-Path (Join-Path $Bin $exe))) { throw "$exe was not built" }
}
Write-Host "built: $Bin\qbsp.exe $Bin\vis.exe"
