# Builds AsusServiceOLED.exe (onefile, no console window) from overlay.py.
# Run this from the speaker-notes-prompter folder:
#   powershell -ExecutionPolicy Bypass -File build_exe.ps1

Set-Location -Path $PSScriptRoot

Write-Host "== Installing build dependencies (requests, websockets, pyinstaller) ==" -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install "requests>=2.28,<3" "websockets>=11,<17" "pillow>=10,<12" pyinstaller
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install failed - fix the error above and re-run." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "== Building SpeakerNotes.exe (onefile, windowed) ==" -ForegroundColor Cyan
Stop-Process -Name SpeakerNotes -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500
# vosk / sounddevice (voice-sync) and the copilot deps are deliberately left
# out of the build (they are optional, and the copilot module is not even
# present in this checkout) - this keeps the exe to the working feature set:
# slide notes, Firebase sync, local WebSocket sync, the cloaked overlay.
python -m PyInstaller --onefile --windowed --name SpeakerNotes `
    --icon "app.ico" `
    --version-file "version.txt" `
    --add-data "app.ico;." `
    --exclude-module vosk `
    --exclude-module sounddevice `
    --exclude-module copilot `
    --exclude-module RealtimeSTT `
    --exclude-module faster_whisper `
    --exclude-module keyboard `
    --exclude-module pyaudiowpatch `
    overlay.py

if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller build failed - see the error above." -ForegroundColor Red
    exit 1
}

if (Test-Path ".env") {
    Copy-Item ".env" "dist\.env" -Force
    Write-Host "Copied .env next to the exe (dist\.env) so Firebase auto-connect still works."
}
if (Test-Path "sample_notes.txt") {
    Copy-Item "sample_notes.txt" "dist\sample_notes.txt" -Force
}
if (Test-Path "app.ico") {
    Copy-Item "app.ico" "dist\app.ico" -Force
}

Write-Host ""
Write-Host "Done. Your exe is at: dist\SpeakerNotes.exe" -ForegroundColor Green
Write-Host "Double-click it, or run: dist\SpeakerNotes.exe sample_notes.txt"
