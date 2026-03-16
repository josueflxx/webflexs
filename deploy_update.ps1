# Safer deploy script for Hostinger VPS
# Usage: powershell -ExecutionPolicy Bypass -File .\deploy_update.ps1

$VPS_IP = "72.61.218.244"
$USER = if ($env:DEPLOY_USER) { $env:DEPLOY_USER } else { "flexsapp" }

Write-Host "Iniciando despliegue en $VPS_IP como $USER..." -ForegroundColor Cyan

$commands = @(
    "set -e",
    "cd /var/www/webflexs",
    "git pull origin main",
    "source venv/bin/activate",
    "export DJANGO_SETTINGS_MODULE=flexs_project.settings.production",
    "set -a",
    "source .env",
    "set +a",
    "pip install -r requirements.txt",
    "python manage.py migrate --settings=flexs_project.settings.production",
    "python manage.py check --settings=flexs_project.settings.production",
    "python manage.py collectstatic --noinput --settings=flexs_project.settings.production",
    "sudo systemctl restart gunicorn",
    "sudo systemctl reload nginx",
    "echo 'Despliegue completado correctamente.'"
)

$remoteCommand = $commands -join " && "
ssh "$USER@$VPS_IP" $remoteCommand

if ($LASTEXITCODE -eq 0) {
    Write-Host "Exito. El servidor se actualizo correctamente." -ForegroundColor Green
}
else {
    Write-Host "Error durante el despliegue. Revisa la salida anterior." -ForegroundColor Red
}
