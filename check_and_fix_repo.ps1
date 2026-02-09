# check_and_fix_repo.ps1
# Script para forzar la sincronización del repositorio en el VPS
# Versión corregida: Elimina caracteres de retorno de carro (\r) de Windows

$VPS_IP = "72.61.218.244"
$USER = "root"

# Contenido del script bash (usamos comillas simples para los comandos internos)
$bash_script = @"
cd /var/www/webflexs
echo "1. Estado actual del repo:"
git rev-parse --short HEAD
git status -s

echo "2. Forzando sincronizacion con origin/main..."
git fetch --all
git reset --hard origin/main

echo "3. Estado post-fix:"
git rev-parse --short HEAD

echo "4. Reiniciando Gunicorn..."
systemctl restart gunicorn
"@

# Guardamos el script bash localmente
$bash_script | Out-File -Encoding ASCII "repo_sync.sh"

Write-Host "1. Subiendo script de sincronización..." -ForegroundColor Cyan
$DEST = "${USER}@${VPS_IP}:/tmp/repo_sync.sh"
scp .\repo_sync.sh "$DEST"

Write-Host "2. Corrigiendo formato y Ejecutando..." -ForegroundColor Cyan
# IMPORTANTE: Usamos sed para eliminar los \r de Windows antes de ejecutar
ssh $USER@$VPS_IP "sed -i 's/\r$//' /tmp/repo_sync.sh && bash /tmp/repo_sync.sh"

Write-Host "3. Limpiando..." -ForegroundColor Cyan
ssh $USER@$VPS_IP "rm /tmp/repo_sync.sh"
Remove-Item "repo_sync.sh"

Write-Host "¡Proceso terminado!" -ForegroundColor Green
