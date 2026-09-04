# Student AI Assistant をコマンド一つで起動する（Windows）。
#
#   .\run.ps1
#
# 仮想環境の作成、依存関係のインストール、.env の用意、サーバー起動までを行う。

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Info  { param($m) Write-Host "> $m" -ForegroundColor Cyan }
function Write-Warn2 { param($m) Write-Host "! $m" -ForegroundColor Yellow }
function Fail        { param($m) Write-Host "x $m" -ForegroundColor Red; exit 1 }

# --- Python の確認 ---
$python = $null
foreach ($candidate in @("python", "py", "python3")) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) { $python = $found.Source; break }
}
if (-not $python) { Fail "Python 3.10 以上が見つかりません。https://www.python.org/downloads/ から導入してください。" }

$versionOk = & $python -c "import sys; print(1 if sys.version_info >= (3,10) else 0)"
if ($versionOk.Trim() -ne "1") { Fail "Python 3.10 以上が必要です。" }

# --- 仮想環境 ---
$venv = ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Info "仮想環境を作成しています..."
    & $python -m venv $venv
    if (-not (Test-Path $venvPython)) { Fail "venv の作成に失敗しました。" }
}

# --- 依存関係（requirements.txt が変わったときだけ入れ直す）---
$stamp = Join-Path $venv ".requirements.sha"
$currentHash = (Get-FileHash -Path "requirements.txt" -Algorithm SHA256).Hash
$storedHash = ""
if (Test-Path $stamp) { $storedHash = (Get-Content $stamp -Raw).Trim() }

if ($storedHash -ne $currentHash) {
    Write-Info "依存関係をインストールしています...(初回は数分かかります)"
    & $venvPython -m pip install --quiet --upgrade pip
    & $venvPython -m pip install --quiet -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Fail "依存関係のインストールに失敗しました。" }
    Set-Content -Path $stamp -Value $currentHash -Encoding utf8
}

# --- 設定ファイル ---
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Warn2 ".env を作成しました。GEMINI_API_KEY を設定すると会話機能が使えます。"
    Write-Warn2 "  https://aistudio.google.com/apikey でキーを取得し、.env に貼り付けてください。"
}

$envText = Get-Content ".env" -Raw
if ($envText -notmatch "(?m)^GEMINI_API_KEY=.+") {
    Write-Warn2 "GEMINI_API_KEY が未設定です。画面は開きますが、AIとの会話はできません。"
}

# --- 起動 ---
$port = 8000
if ($envText -match "(?m)^PORT=(\d+)") { $port = [int]$Matches[1] }

Write-Info "起動します:  http://localhost:$port"
$addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
             Where-Object { $_.IPAddress -ne "127.0.0.1" -and $_.PrefixOrigin -ne "WellKnown" }
foreach ($address in $addresses) {
    Write-Info "スマートフォンから:  http://$($address.IPAddress):$port"
}

& $venvPython -m backend.main
