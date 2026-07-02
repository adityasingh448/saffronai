$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = "C:\Users\Aditya\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (!(Test-Path $Python)) {
  $Python = "python"
}

Set-Location $Root

if (!(Test-Path ".venv")) {
  & $Python -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
