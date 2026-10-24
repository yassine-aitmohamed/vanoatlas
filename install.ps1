$ErrorActionPreference = "Stop"
$Repo = "https://github.com/yassine-aitmohamed/vanoatlas"
$Dir = if ($env:VANOATLAS_DIR) { $env:VANOATLAS_DIR } else { Join-Path $HOME "VanoAtlas" }

function Find-Python {
  foreach ($c in @("py", "python", "python3")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) {
      $a = if ($c -eq "py") { @("-3", "--version") } else { @("--version") }
      try { & $c @a *> $null; if ($LASTEXITCODE -eq 0) { return $c } } catch {}
    }
  }
  return $null
}

$Py = Find-Python
if (-not $Py) {
  Write-Host "Installing Python 3 with winget ..."
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
  $Py = Find-Python
  if (-not $Py) { Write-Host "Please install Python 3 from https://www.python.org/downloads/ and run this command again."; exit 1 }
}
$PyArgs = if ($Py -eq "py") { @("-3") } else { @() }

$Local = $PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "VanoAtlas\vanoatlas.py"))
if (-not $Local) {
  Write-Host "Downloading VanoAtlas to $Dir ..."
  $Tmp = Join-Path $env:TEMP ("vanoatlas_" + [guid]::NewGuid())
  New-Item -ItemType Directory -Path $Tmp | Out-Null
  Invoke-WebRequest "$Repo/archive/refs/heads/main.zip" -OutFile "$Tmp\vanoatlas.zip"
  Expand-Archive "$Tmp\vanoatlas.zip" -DestinationPath $Tmp
  New-Item -ItemType Directory -Force -Path $Dir | Out-Null
  Copy-Item -Recurse -Force "$Tmp\vanoatlas-main\*" $Dir
  Remove-Item -Recurse -Force $Tmp
} else {
  $Dir = $PSScriptRoot
}

Write-Host "Setting up VanoLeaf ..."
& $Py @PyArgs -m venv "$Dir\VanoLeaf\.venv"
& "$Dir\VanoLeaf\.venv\Scripts\python.exe" -m pip install -q --upgrade pip
& "$Dir\VanoLeaf\.venv\Scripts\python.exe" -m pip install -q -r "$Dir\VanoLeaf\requirements.txt"
Write-Host "Setting up VanoNote ..."
& $Py @PyArgs -m venv "$Dir\VanoNote\.venv"
& "$Dir\VanoNote\.venv\Scripts\python.exe" -m pip install -q --upgrade pip
try { & "$Dir\VanoNote\.venv\Scripts\python.exe" -m pip install -q PyMuPDF } catch { Write-Host "PyMuPDF was not installed: PDF import in VanoNote is limited." }

$Desktop = [Environment]::GetFolderPath("Desktop")
$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut((Join-Path $Desktop "VanoAtlas.lnk"))
$Link.TargetPath = Join-Path $Dir "start.bat"
$Link.WorkingDirectory = $Dir
$Link.Save()
Write-Host "Shortcut created on the Desktop: VanoAtlas"
if (-not (Get-Command pdflatex -ErrorAction SilentlyContinue)) { Write-Host "Tip: install MiKTeX (https://miktex.org) to compile LaTeX in VanoLeaf." }
Write-Host "VanoAtlas is installed in $Dir. Starting ..."
Start-Process -FilePath (Join-Path $Dir "start.bat") -WorkingDirectory $Dir
