# fix_migration_db.ps1
# Script para corregir inconsistencia en la DB (Versión SCP - Corregida)

$VPS_IP = "72.61.218.244"
$USER = "root"

Write-Host "1. Subiendo archivo SQL..." -ForegroundColor Cyan

# Construimos el destino como string para que PowerShell no se confunda con los dos puntos
$DEST = "${USER}@${VPS_IP}:/tmp/fix.sql"

# Subimos el archivo local al servidor
scp .\fix.sql "$DEST"

Write-Host "2. Aplicando corrección..." -ForegroundColor Cyan
# Ejecutamos el archivo desde el servidor
ssh $USER@$VPS_IP "sudo -u postgres psql -d flexs_db -f /tmp/fix.sql"

Write-Host "3. Limpiando y Reiniciando..." -ForegroundColor Cyan
ssh $USER@$VPS_IP "rm /tmp/fix.sql && systemctl restart gunicorn"

Write-Host "¡Listo! Si viste DELETE 1 (o 0), funcionó." -ForegroundColor Green
