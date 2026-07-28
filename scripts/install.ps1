# Install summation-cli (sumcli) via uv tool install.
# Usage:
#   irm https://install.summation.com/sumcli.ps1 | iex
# Optional:
#   $env:SUMCLI_VERSION = "X.Y.Z"; irm https://install.summation.com/sumcli.ps1 | iex
$ErrorActionPreference = "Stop"

$Package = "summation-cli"
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
  if ($env:SUMCLI_VERSION) {
    Write-Say "Installing ${Package}==$($env:SUMCLI_VERSION)"
    uv tool install "${Package}==$($env:SUMCLI_VERSION)"
  } else {
    Write-Say "Installing ${Package} (latest)"
    uv tool install $Package
  }
}

Ensure-Uv
Install-Sumcli

if (Get-Command $BinName -ErrorAction SilentlyContinue) {
  Write-Say "Installed. Try: $BinName --version"
  & $BinName --version
} else {
  Write-Say "Installed $Package."
  Write-Say "If '$BinName' is not found, ensure uv's tool bin dir is on PATH."
  Write-Say "Upgrade later with: uv tool upgrade $Package"
}
