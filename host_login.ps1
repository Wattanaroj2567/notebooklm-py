# NotebookLM Host Login Helper
# Run this to refresh your authentication on the host machine.

$RepoRoot = Get-Location
$VenvPath = Join-Path $RepoRoot ".venv"
$PythonExe = Join-Path $VenvPath "Scripts\python.exe"

if (-Not (Test-Path $PythonExe)) {
    Write-Host "⚠️  Virtual environment not found at $VenvPath" -ForegroundColor Yellow
    Write-Host "Attempting to find notebooklm in PATH..."
    $Exe = Get-Command notebooklm -ErrorAction SilentlyContinue
    if ($Exe) {
        notebooklm login --browser-cookies chrome
    } else {
        Write-Host "❌ Could not find notebooklm. Please ensure the project is installed." -ForegroundColor Red
    }
} else {
    Write-Host "🚀 Launching NotebookLM login from local environment..." -ForegroundColor Cyan
    & $PythonExe -m notebooklm login --browser-cookies chrome
}

Write-Host "`n✅ After successful login, return to the AI chat and ask to 'sync_auth_from_host'." -ForegroundColor Green
