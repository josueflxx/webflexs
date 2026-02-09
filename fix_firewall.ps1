# fix_firewall.ps1
# Script para arreglar el firewall en Hostinger

$VPS_IP = "72.61.218.244"
$USER = "root"

Write-Host "Conectando al VPS para arreglar firewall..." -ForegroundColor Cyan

# Comandos para resetear y configurar UFW
$commands = @(
    "echo 'Configurando firewall...'",
    "ufw --force reset",
    "ufw default deny incoming",
    "ufw default allow outgoing",
    "ufw allow 22/tcp",
    "ufw allow 80/tcp",
    "ufw allow 443/tcp",
    "ufw allow 'Nginx Full'",
    "ufw --force enable",
    "systemctl restart nginx",
    "ufw status verbose"
)

$remote_command = $commands -join " && "

ssh $USER@$VPS_IP $remote_command
