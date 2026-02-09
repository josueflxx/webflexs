# deploy_update.ps1
# Script para desplegar actualizaciones en el VPS de Hostinger
# Uso: powershell -ExecutionPolicy Bypass -File .\deploy_update.ps1

$VPS_IP = "72.61.218.244"
$USER = "root"

Write-Host "Iniciando despliegue en $VPS_IP..." -ForegroundColor Cyan

# Comandos a ejecutar en el servidor
$commands = @(
    "cd /var/www/webflexs",
    "git pull origin main",
    "source venv/bin/activate",
    "pip install -r requirements.txt",
    "python manage.py migrate",
    "python manage.py collectstatic --noinput",
    "systemctl restart gunicorn",
    "systemctl reload nginx",
    "echo '¡Despliegue completado exitosamente!'"
)

# Unir comandos con &&
$remote_command = $commands -join " && "

# Ejecutar vía SSH
ssh $USER@$VPS_IP $remote_command

if ($LASTEXITCODE -eq 0) {
    Write-Host "¡Éxito! El servidor se ha actualizado." -ForegroundColor Green
}
else {
    Write-Host "Error durante el despliegue. Revisa los logs de arriba." -ForegroundColor Red
}
