# Install summation-cli (sumcli) via uv tool install.
# Usage:
#   irm https://install.summation.com/sumcli.ps1 | iex
# Optional:
#   $env:SUMCLI_VERSION = "X.Y.Z"; irm https://install.summation.com/sumcli.ps1 | iex
#   $env:SUMCLI_PACKAGE = "C:\path\to\checkout"  # install from a local path (tests / dev)
$ErrorActionPreference = "Stop"

$Package = if ($env:SUMCLI_PACKAGE) { $env:SUMCLI_PACKAGE } else { "summation-cli" }
$BinName = "sumcli"

function Write-Say([string]$Message) { Write-Host $Message }
function Write-Err([string]$Message) {
  Write-Error "sumcli-install: $Message"
  exit 1
}

function Ensure-Uv {
  if (Get-Command uv -ErrorAction SilentlyContinue) {
    return
  }
  Write-Say "uv not found; installing via https://astral.sh/uv/install.ps1"
  irm https://astral.sh/uv/install.ps1 | iex
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $candidates = @(
      (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
      (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe")
    )
    foreach ($candidate in $candidates) {
      if (Test-Path $candidate) {
        $dir = Split-Path $candidate -Parent
        $env:Path = "$dir;$env:Path"
        break
      }
    }
  }
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Err "uv installed but not on PATH; open a new shell or add ~/.local/bin to PATH"
  }
}

function Install-Sumcli {
  # --force reinstalls so a corrupt or stale tool venv is repaired instead of
  # silently no-op'ing with "already installed".
  if ($env:SUMCLI_VERSION) {
    Write-Say "Installing ${Package}==$($env:SUMCLI_VERSION)"
    uv tool install --force "${Package}==$($env:SUMCLI_VERSION)"
  } else {
    Write-Say "Installing ${Package} (latest)"
    uv tool install --force $Package
  }
  if ($LASTEXITCODE -ne 0) {
    Write-Err "uv tool install failed (exit $LASTEXITCODE)"
  }
}

function Resolve-Real([string]$P) {
  $i = Get-Item -LiteralPath $P -ErrorAction SilentlyContinue
  if ($null -eq $i) { return $null }
  # ResolvedTarget follows the full reparse/symlink chain when available.
  if ($i.LinkType -and $i.ResolvedTarget) {
    $resolved = Get-Item -LiteralPath $i.ResolvedTarget -ErrorAction SilentlyContinue
    if ($null -ne $resolved) { return $resolved.FullName }
  }
  return $i.FullName
}

function Test-SameBin([string]$A, [string]$B) {
  if ($A -eq $B) { return $true }
  $ra = Resolve-Real $A
  $rb = Resolve-Real $B
  return ($null -ne $ra -and $null -ne $rb -and $ra -eq $rb)
}

function Verify-Sumcli {
  $binDir = (uv tool dir --bin 2>$null)
  if (-not $binDir) {
    $binDir = Join-Path $env:USERPROFILE ".local\bin"
  }
  $installed = Join-Path $binDir $BinName
  $installedExe = "$installed.exe"
  if (Test-Path -LiteralPath $installedExe) {
    $installed = $installedExe
  }

  if (-not (Test-Path -LiteralPath $installed)) {
    Write-Err "install finished but $installed is missing"
  }

  # Verify the binary we just installed, not whatever PATH resolves to.
  & $installed --version
  if ($LASTEXITCODE -ne 0) {
    Write-Err "$installed is installed but fails to run"
  }

  Write-Say "Installed. Try: $BinName --version"

  $resolvedCmd = Get-Command $BinName -ErrorAction SilentlyContinue
  if (-not $resolvedCmd) {
    Write-Err "'$BinName' is not on your PATH. Add $binDir to PATH, then open a new shell."
  }
  $resolved = $resolvedCmd.Source
  if (-not (Test-SameBin $resolved $installed)) {
    Write-Err @"
another '$BinName' comes first on your PATH:
  $resolved   <- your shell uses this one
  $installed   <- the one just installed
Remove the first program, or put $binDir earlier on PATH.
"@
  }
}

Ensure-Uv
Install-Sumcli
Verify-Sumcli
