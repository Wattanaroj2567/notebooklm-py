param(
    [switch]$Recreate = $true
)

function Find-RepoRoot {
    # Prefer automatic script root, fall back to invocation definition, then current directory
    $scriptDir = $PSScriptRoot
    if (-not $scriptDir -or $scriptDir -eq '') {
        if ($MyInvocation.MyCommand.Definition) {
            $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
        } else {
            $scriptDir = (Get-Location).Path
        }
    }
    $current = $scriptDir
    while ($current -and $current -ne '' -and -not (Test-Path (Join-Path $current 'pyproject.toml'))) {
        $parent = Split-Path $current -Parent
        if ($parent -eq $current) { break }
        $current = $parent
    }
    if ($current -and $current -ne '' -and (Test-Path (Join-Path $current 'pyproject.toml'))) { return $current }
    return $scriptDir
}

$repoRoot = Find-RepoRoot
if (-not $repoRoot -or $repoRoot -eq '') {
    Write-Host "Could not determine repository root; using current directory"
    $repoRoot = (Get-Location).Path
}
Set-Location $repoRoot

Write-Host "Preparing .venv in $PWD"

$venvPath = Join-Path $repoRoot '.venv'
if (Test-Path $venvPath) {
    if ($Recreate) {
        Write-Host "Removing existing .venv..."
        Remove-Item -Recurse -Force $venvPath
    } else {
        Write-Host ".venv already exists. Use -Recreate to recreate. Exiting."
        exit 0
    }
}

Write-Host "Creating virtual environment..."
python -m venv $venvPath

$python = Join-Path $venvPath "Scripts\python.exe"
if (-Not (Test-Path $python)) {
    Write-Error "Python not available or venv creation failed. Ensure 'python' is on PATH and try again."
    exit 1
}

Write-Host "Upgrading pip / setuptools / wheel..."
& $python -m pip install --upgrade pip setuptools wheel

Write-Host "Installing package with extras: browser,dev,markdown (from project root)"
$projectPath = $repoRoot
try {
    & $python -m pip install -e "$projectPath[browser,dev,markdown]"
} catch {
    Write-Host "Editable install failed; trying non-editable install..."
    & $python -m pip install "$projectPath[browser,dev,markdown]"
}

# Attempt to run `uv sync` if available inside the venv
 # Attempt to run `uv sync` if available inside the venv
 $uvExe = Join-Path $venvPath "Scripts\uv.exe"
 if (Test-Path $uvExe) {
    Write-Host "Running 'uv sync --frozen --extra browser --extra dev --extra markdown' via uv.exe..."
    & $uvExe sync --frozen --extra browser --extra dev --extra markdown
 } else {
    Write-Host "'uv' executable not found in venv. Trying 'python -m uv'..."
    try {
        & $python -m uv sync --frozen --extra browser --extra dev --extra markdown
    } catch {
        Write-Host "Could not run 'uv sync' automatically. Activate the venv and run:"
        Write-Host "$venvPath\Scripts\Activate.ps1"
        Write-Host "uv sync --frozen --extra browser --extra dev --extra markdown"
    }
 }

Write-Host "Preinstall finished. .venv ready."
