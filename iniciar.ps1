# Abre Compra Óptima en tu PC.
#   Uso:  click derecho > "Ejecutar con PowerShell"   o   .\iniciar.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# 1) Entorno virtual (sólo la primera vez)
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host 'Creando entorno virtual e instalando dependencias (sólo la primera vez)...'
    python -m venv .venv
    & .venv\Scripts\python.exe -m pip install -q -r requirements.txt
}

# 2) Configuración: sin .env arranca en modo demo (sin IA)
if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Warning 'Creé .env desde .env.example. Poné tu GEMINI_API_KEY (gratis en https://aistudio.google.com/apikey) para usar la IA.'
}

# 3) Web
$port = 8010
Write-Host "Compra Óptima en http://127.0.0.1:$port  (Ctrl+C para cerrar)"
Start-Process "http://127.0.0.1:$port"
& .venv\Scripts\python.exe -m app.main
