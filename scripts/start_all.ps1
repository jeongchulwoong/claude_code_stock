# =============================================================================
# scripts/start_all.ps1 — Quant Desk 전체 스택 launcher (PowerShell 5.1+).
#
# Trader (main.py) + Dashboard (5000) + Realtime (5001) + Stock Watcher
# + Cloudflared tunnel (있을 때) 를 한 번에 띄우고, Ctrl+C 1회로 깨끗하게 정리.
#
# 호출 방식:
#   bash:        bash scripts/start_all.ps1   # 사용 X — 본 파일은 PowerShell 전용
#   PowerShell:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_all.ps1
#   from .bat:   start_all.bat 가 wrapper 로 호출
#
# 옵션:
#   -NoBrowser       : 브라우저 자동 오픈 안 함.
#   -SkipTrader      : main.py 시작 건너뜀 (UI 만 띄울 때).
#   -ShowWindows     : 백그라운드 프로세스 창 표시 (디버깅).
#
# 안전 정책:
#   - main.py 는 실주문 가능. PID lock (scripts/start_trader.py) 기반 single-instance.
#     이미 떠 있으면 skip — 절대 두 번 띄우지 않는다.
#   - Ctrl+C → Stop-ManagedProcesses (background 정리) → stop_trader.py (lock-aware 종료).
#   - frontend/dist/.vite/manifest.json 부재 시 React UI 자동 비활성 (Jinja fallback).
# =============================================================================

[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$SkipTrader,
    [switch]$ShowWindows,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# Codex/IDE 셸이 주입한 가짜 proxy 변수 정리 — broker/yfinance/cloudflared 직결 보장.
foreach ($k in @("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY",
                 "http_proxy","https_proxy","all_proxy","no_proxy")) {
    Remove-Item "Env:$k" -ErrorAction SilentlyContinue
}

# ──────────────────────────────────────────────────────────────────────
# Paths.
# ──────────────────────────────────────────────────────────────────────

$Root   = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "venv\Scripts\python.exe"
$LogDir = Join-Path $Root "logs"

$OpenBrowser = -not $NoBrowser
$ShowProcessWindows = [bool]$ShowWindows

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# venv python 우선, 없으면 PATH 의 python 사용 (개발 환경 대비).
if (-not (Test-Path -LiteralPath $Python)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Python = $cmd.Source }
}
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "[ERROR] Python not found."
    Write-Host "        Expected: $Root\venv\Scripts\python.exe (or python on PATH)"
    Write-Host "        Run: py -3.12 -m venv venv ; venv\Scripts\pip install -r requirements.txt"
    exit 1
}

# ──────────────────────────────────────────────────────────────────────
# Process lifecycle helpers.
# ──────────────────────────────────────────────────────────────────────

$script:ManagedProcesses = New-Object System.Collections.Generic.List[System.Diagnostics.Process]
$global:StartAllStopRequested = $false
$script:CleanupStarted = $false

function Write-Line($Message) { Write-Host $Message }

function Test-PortListening([int]$Port) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        return [bool]$conn
    } catch {
        $lines = netstat -ano | Select-String -Pattern ":$Port\s+.*LISTENING"
        return [bool]$lines
    }
}

function Stop-ListenersOnPort([int]$Port, [string]$Name) {
    try {
        $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        foreach ($conn in $listeners) {
            $ownerPid = [int]$conn.OwningProcess
            if ($ownerPid -le 0 -or $ownerPid -eq $PID) { continue }
            $proc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Line "[CLEAN] Stop existing $Name listener on port $Port pid=$ownerPid ($($proc.ProcessName))"
                Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        Write-Line "[WARN] Could not inspect port $Port listener: $($_.Exception.Message)"
    }
}

function Stop-ExistingDashboardProcesses {
    Stop-ListenersOnPort 5000 "dashboard"
    Stop-ListenersOnPort 5001 "realtime"
    Start-Sleep -Milliseconds 500
}

function Find-Cloudflared {
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\cloudflared.exe"),
        (Join-Path $env:ProgramFiles "cloudflared\cloudflared.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "cloudflared\cloudflared.exe")
    )
    foreach ($p in $candidates) {
        if ($p -and (Test-Path -LiteralPath $p)) { return $p }
    }
    $wingetRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path -LiteralPath $wingetRoot) {
        $hit = Get-ChildItem -Path $wingetRoot -Recurse -Filter "cloudflared.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Start-ManagedProcess {
    param(
        [Parameter(Mandatory=$true)][string]$Name,
        [Parameter(Mandatory=$true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory=$true)][string]$LogName
    )
    $stdout = Join-Path $LogDir "$LogName`_runtime.log"
    $stderr = Join-Path $LogDir "$LogName`_error.log"
    $windowStyle = if ($ShowProcessWindows) { "Normal" } else { "Hidden" }

    Write-Line "[BG] $Name -> logs\$LogName`_runtime.log"
    $p = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $Arguments `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle $windowStyle `
        -PassThru
    $script:ManagedProcesses.Add($p) | Out-Null
    return $p
}

function Stop-ManagedProcesses {
    if ($script:CleanupStarted) { return }
    $script:CleanupStarted = $true

    Write-Line ""
    Write-Line "[STOP] Stopping background processes..."

    # Trader 는 PID lock-aware 종료 — 다른 Python 프로세스를 잘못 죽이지 않게.
    if (-not $SkipTrader) {
        try {
            & $Python (Join-Path $Root "scripts\stop_trader.py") | ForEach-Object { Write-Line $_ }
        } catch {
            Write-Line "[WARN] stop_trader.py failed: $($_.Exception.Message)"
        }
    }

    for ($i = $script:ManagedProcesses.Count - 1; $i -ge 0; $i--) {
        $p = $script:ManagedProcesses[$i]
        try {
            $live = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
            if ($live) {
                Write-Line "[STOP] $($p.ProcessName) pid=$($p.Id)"
                Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            }
        } catch {
            Write-Line "[WARN] failed to stop pid=$($p.Id): $($_.Exception.Message)"
        }
    }

    Write-Line "[STOP] Cleanup complete."
}

# Ctrl+C → cleanup signal.
$cancelSubscription = Register-ObjectEvent -InputObject ([Console]) -EventName CancelKeyPress -Action {
    $EventArgs.Cancel = $true
    $global:StartAllStopRequested = $true
    Write-Host ""
    Write-Host "[CTRL+C] Stop requested. Cleaning up..."
}

# ──────────────────────────────────────────────────────────────────────
# Pre-flight.
# ──────────────────────────────────────────────────────────────────────

try {
    Write-Line "============================================================"
    Write-Line "  Quant Desk launcher"
    Write-Line "============================================================"
    Write-Line "[OK] Python : $Python"
    Write-Line "[OK] Logs   : $LogDir"

    # React UI 상태 — manifest 존재 + flag.
    $manifest = Join-Path $Root "frontend\dist\.vite\manifest.json"
    $reactBuilt = Test-Path -LiteralPath $manifest
    $reactFlag  = ($env:FRONTEND_REACT_ENABLED -in @("1","true","yes","on","TRUE","True"))
    $clientFlag = ($env:FRONTEND_CLIENT_REACT_ENABLED -in @("1","true","yes","on","TRUE","True"))

    if ($reactBuilt) {
        Write-Line "[OK] frontend dist : found ($manifest)"
    } else {
        if ($NoBuild) {
            Write-Line "[WARN] frontend dist : missing -- React UI inactive (Jinja fallback only)"
            Write-Line "         Run: cd frontend ; npm install ; npm run build"
        } else {
            Write-Line "[INFO] frontend dist : missing -- attempting one-time build (cd frontend ; npm run build)"
            $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
            if (-not $npmCmd) {
                Write-Line "[WARN] npm not on PATH -- skip auto-build. React UI inactive."
            } else {
                $frontendDir = Join-Path $Root "frontend"
                $nodeModules = Join-Path $frontendDir "node_modules"
                if (-not (Test-Path -LiteralPath $nodeModules)) {
                    Write-Line "[BUILD] npm install (one-time)"
                    & $npmCmd.Source --prefix $frontendDir install --silent 2>&1 |
                        Where-Object { $_ -match '\S' } |
                        Select-Object -First 5 |
                        ForEach-Object { Write-Line "        $_" }
                }
                Write-Line "[BUILD] npm run build (one-time)"
                & $npmCmd.Source --prefix $frontendDir run build 2>&1 |
                    Where-Object { $_ -match '\S' } |
                    Select-Object -Last 12 |
                    ForEach-Object { Write-Line "        $_" }
                $reactBuilt = Test-Path -LiteralPath $manifest
                if ($reactBuilt) {
                    Write-Line "[OK] frontend dist : built"
                } else {
                    Write-Line "[WARN] frontend build did not produce manifest -- React UI inactive."
                }
            }
        }
    }
    if ($reactFlag) {
        Write-Line "[OK] FRONTEND_REACT_ENABLED=1 -- admin React island will mount"
    } else {
        Write-Line "[INFO] FRONTEND_REACT_ENABLED off -- admin uses Jinja UI"
    }
    if ($clientFlag) {
        Write-Line "[OK] FRONTEND_CLIENT_REACT_ENABLED=1 -- /client React island will mount"
    } else {
        Write-Line "[INFO] FRONTEND_CLIENT_REACT_ENABLED off -- /client uses Jinja UI"
    }

    Stop-ExistingDashboardProcesses

    $cloudflared = Find-Cloudflared
    if ($cloudflared) {
        Write-Line "[OK] cloudflared : $cloudflared"
    } else {
        Write-Line "[INFO] cloudflared : not installed -- external sharing skipped"
        Write-Line "         Install: winget install --id Cloudflare.cloudflared"
    }

    # ----------------------------------------------------------------
    # 1) Trader (main.py) -- PID lock based.
    # ----------------------------------------------------------------

    if ($SkipTrader) {
        Write-Line ""
        Write-Line "[SKIP] Trader (-SkipTrader) -- UI / watcher / tunnels only"
    } else {
        Write-Line ""
        Write-Line "[START] Trader (main.py) -- logs\main_runtime.log"
        Write-Line "        ! WARNING: live order routing capable. Lock-aware via scripts\start_trader.py"
        & $Python (Join-Path $Root "scripts\start_trader.py") | ForEach-Object { Write-Line $_ }
        if ($LASTEXITCODE -ge 2) {
            Write-Line "[WARN] Trader start returned code $LASTEXITCODE. Check logs\main_runtime.log"
        }
    }

    # ──────────────────────────────────────────────────────────────────
    # 2) Dashboard 5000.
    # ──────────────────────────────────────────────────────────────────

    if (Test-PortListening 5000) {
        Write-Line "[SKIP] Dashboard 5000 already listening"
    } else {
        Start-ManagedProcess `
            -Name "Dashboard 5000" `
            -FilePath $Python `
            -Arguments @("dashboard\app.py") `
            -LogName "dashboard_5000" | Out-Null
    }

    # ──────────────────────────────────────────────────────────────────
    # 3) Realtime 5001 (Socket.IO).
    # ──────────────────────────────────────────────────────────────────

    if (Test-PortListening 5001) {
        Write-Line "[SKIP] Realtime 5001 already listening"
    } else {
        Start-ManagedProcess `
            -Name "Realtime 5001" `
            -FilePath $Python `
            -Arguments @("dashboard\realtime_app.py") `
            -LogName "realtime_5001" | Out-Null
    }

    # ──────────────────────────────────────────────────────────────────
    # 4) Stock watcher (백그라운드 메타 갱신).
    # ──────────────────────────────────────────────────────────────────

    Start-ManagedProcess `
        -Name "Stock Watcher" `
        -FilePath $Python `
        -Arguments @("scripts\fetch_real_stocks.py", "--watch", "--interval", "30") `
        -LogName "stock_watcher" | Out-Null

    # ──────────────────────────────────────────────────────────────────
    # 5) Cloudflared tunnels (선택).
    # ──────────────────────────────────────────────────────────────────

    if ($cloudflared) {
        Start-Sleep -Seconds 3
        Start-ManagedProcess `
            -Name "Cloudflare Tunnel 5000" `
            -FilePath $cloudflared `
            -Arguments @("tunnel", "--url", "http://localhost:5000", "--metrics", "127.0.0.1:20241") `
            -LogName "cloudflared_5000" | Out-Null
        Start-Sleep -Seconds 2
        Start-ManagedProcess `
            -Name "Cloudflare Tunnel 5001" `
            -FilePath $cloudflared `
            -Arguments @("tunnel", "--url", "http://localhost:5001", "--metrics", "127.0.0.1:20242") `
            -LogName "cloudflared_5001" | Out-Null
    }

    # ──────────────────────────────────────────────────────────────────
    # 6) Browser auto-open + status banner.
    # ──────────────────────────────────────────────────────────────────

    Start-Sleep -Seconds 4

    if ($OpenBrowser) {
        $loginUrl = if ($reactFlag -and $reactBuilt) {
            "http://127.0.0.1:5000/login"
        } else {
            "http://127.0.0.1:5000/advanced"
        }
        try { Start-Process $loginUrl | Out-Null } catch { }
    }

    Write-Line ""
    Write-Line "============================================================"
    Write-Line "  Stack ready"
    Write-Line "============================================================"
    Write-Line "  Endpoints:"
    Write-Line "    - Login        : http://127.0.0.1:5000/login"
    Write-Line "    - Admin (auth) : http://127.0.0.1:5000/advanced"
    Write-Line "    - Public       : http://127.0.0.1:5000/client"
    Write-Line "    - Realtime WS  : http://127.0.0.1:5001"
    Write-Line ""
    Write-Line "  Auth: JWT (admin 8h / client 24h) + Flask session (병행)"
    Write-Line ""
    Write-Line "  Logs:"
    if (-not $SkipTrader) {
        Write-Line "    - Trader        : logs\main_runtime.log"
    }
    Write-Line "    - Dashboard     : logs\dashboard_5000_runtime.log"
    Write-Line "    - Realtime      : logs\realtime_5001_runtime.log"
    Write-Line "    - Stock watcher : logs\stock_watcher_runtime.log"
    if ($cloudflared) {
        Write-Line "    - Tunnel 5000   : logs\cloudflared_5000_runtime.log"
        Write-Line "    - Tunnel 5001   : logs\cloudflared_5001_runtime.log"
    }
    Write-Line ""
    if (-not $SkipTrader) {
        Write-Line "  Trader status (read-only):"
        Write-Line "    venv\Scripts\python.exe scripts\trader_status.py"
        Write-Line ""
    }
    Write-Line "  Press Ctrl+C in this console to stop everything."
    Write-Line "============================================================"

    while (-not $global:StartAllStopRequested) {
        Start-Sleep -Seconds 1
    }
} finally {
    Stop-ManagedProcesses
    if ($cancelSubscription) {
        Unregister-Event -SourceIdentifier $cancelSubscription.Name -ErrorAction SilentlyContinue
        Remove-Job $cancelSubscription -Force -ErrorAction SilentlyContinue
    }
    Remove-Variable -Name StartAllStopRequested -Scope Global -ErrorAction SilentlyContinue
}
