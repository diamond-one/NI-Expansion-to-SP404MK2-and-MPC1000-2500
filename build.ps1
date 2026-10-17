$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    python -m pip install -r requirements.txt 'pyinstaller==6.22.2'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    python -m PyInstaller --noconfirm NI-to-SP.spec
    if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
} finally {
    Pop-Location
}
