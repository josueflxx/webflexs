param(
    [string]$VpsIp = "72.61.218.244",
    [string]$DeployUser = $(if ($env:DEPLOY_USER) { $env:DEPLOY_USER } else { "flexsapp" }),
    [string]$EditorSource = "C:\Users\Brian\Desktop\SISTEMA BASE"
)

$ErrorActionPreference = "Stop"
$frontendDir = Join-Path $EditorSource "frontend"
$distDir = Join-Path $frontendDir "dist"
$archive = Join-Path $EditorSource "frontend-webflexs.tar.gz"

if (-not (Test-Path (Join-Path $frontendDir "package.json"))) {
    throw "No se encontro el frontend de CatalogoPRO en $frontendDir"
}

Write-Host "[1/4] Compilando el editor en modo WEBFLEXS..." -ForegroundColor Cyan
$previousMode = $env:VITE_WEBFLEXS_MODE
try {
    $env:VITE_WEBFLEXS_MODE = "true"
    Push-Location $frontendDir
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "Fallo la compilacion del frontend." }
}
finally {
    Pop-Location
    $env:VITE_WEBFLEXS_MODE = $previousMode
}

Write-Host "[2/4] Empaquetando archivos estaticos..." -ForegroundColor Cyan
if (Test-Path $archive) { Remove-Item -LiteralPath $archive -Force }
tar -czf $archive -C $distDir .
if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el paquete del editor." }

Write-Host "[3/4] Subiendo el paquete al VPS..." -ForegroundColor Cyan
scp $archive "${DeployUser}@${VpsIp}:/tmp/catalogopro-editor.tar.gz"
if ($LASTEXITCODE -ne 0) { throw "No se pudo transferir el editor al VPS." }

$remoteStage = @'
set -euo pipefail
target=/var/www/webflexs/catalogopro_build/frontend
if [ "$target" != "/var/www/webflexs/catalogopro_build/frontend" ]; then exit 1; fi
mkdir -p "$target"
find "$target" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
tar -xzf /tmp/catalogopro-editor.tar.gz -C "$target"
rm -f /tmp/catalogopro-editor.tar.gz
sudo bash /var/www/webflexs/scripts/deploy_catalogopro_vps.sh
'@

Write-Host "[4/4] Activando el frontend oficial sin conexion .NET a PostgreSQL..." -ForegroundColor Cyan
$remoteStage | ssh "${DeployUser}@${VpsIp}" "bash -s"
if ($LASTEXITCODE -ne 0) { throw "Fallo el despliegue remoto del editor." }

Write-Host "Editor desplegado en https://flexsrepuestos.shop/editor-masivo/" -ForegroundColor Green
Write-Host "La escritura depende de FEATURE_EXTERNAL_EDITOR_WRITES en el entorno de WEBFLEXS." -ForegroundColor Yellow
