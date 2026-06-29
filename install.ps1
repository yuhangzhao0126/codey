# codey installer (Windows) — install or update to the latest main.
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/yuhangzhao0126/codey/main/install.ps1 | iex"
# Re-run to self-update. Never overwrites your config.
$ErrorActionPreference = "Stop"

$repo       = "git+https://github.com/yuhangzhao0126/codey@main"
$configDir  = Join-Path $HOME ".config\codey"
$configFile = Join-Path $configDir "config.toml"
$localBin   = Join-Path $HOME ".local\bin"

Write-Host "codey installer"

# 1. ensure uv (bootstraps its own Python 3.11+)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "-> installing uv..."
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$localBin;$env:Path"
}

# 2. git is required (uv clones the repo over git+https)
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Host "git is required to install codey but was not found."
  Write-Host "  Install: winget install Git.Git   (then reopen your terminal)"
  exit 1
}

# 3. install or update codey
Write-Host "-> installing codey from main..."
uv tool install --force $repo

# 3. seed a placeholder config only if none exists
if (-not (Test-Path $configFile)) {
  Write-Host "-> writing default config to $configFile"
  New-Item -ItemType Directory -Force -Path $configDir | Out-Null
  @'
default_profile = "deepseek"

[profiles.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key  = "sk-..."
model    = "deepseek-chat"
'@ | Set-Content -Path $configFile -Encoding utf8
}

# 4. PATH hint
if (-not (Get-Command codey -ErrorAction SilentlyContinue)) {
  Write-Host "! $localBin is not on your PATH. Add it, then reopen your terminal:"
  Write-Host "    setx PATH `"$localBin;%PATH%`""
}

Write-Host "done. Edit $configFile to add your API key, then run: codey"
