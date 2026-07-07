# Despliegue de CatalogoPRO (C# / React) en Hostinger VPS
# Uso: powershell -ExecutionPolicy Bypass -File .\deploy_catalogopro.ps1

$VPS_IP = "72.61.218.244"
$USER = if ($env:DEPLOY_USER) { $env:DEPLOY_USER } else { "flexsapp" }
$BASE_DIR = "C:\Users\Brian\Desktop\SISTEMA BASE"
$STAGE_DIR = "$BASE_DIR\publish_staging"

Clear-Host
Write-Host "=========================================================" -ForegroundColor Blue
Write-Host "       INICIANDO DESPLIEGUE DE CATALOGOPRO EN VPS        " -ForegroundColor Blue
Write-Host "=========================================================" -ForegroundColor Blue
Write-Host "VPS IP: $VPS_IP" -ForegroundColor Yellow
Write-Host "Usuario: $USER" -ForegroundColor Yellow
Write-Host ""

# 1. Obtener credenciales de base de datos desde el VPS
Write-Host "[1/7] Conectando al VPS para leer la configuración de base de datos..." -ForegroundColor Cyan
$remoteEnv = ssh "$USER@$VPS_IP" "cat /var/www/webflexs/.env"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Error al conectar al VPS mediante SSH. Asegúrate de tener las llaves configuradas." -ForegroundColor Red
    exit 1
}

# Valores por defecto
$dbName = "flexs_db"
$dbUser = "flexs_user"
$dbPass = ""
$dbHost = "localhost"
$dbPort = "5432"

foreach ($line in ($remoteEnv -split "`n")) {
    $line = $line.Trim()
    if ($line -match "^DB_NAME=(.+)$") { $dbName = $Matches[1].Trim() }
    if ($line -match "^DB_USER=(.+)$") { $dbUser = $Matches[1].Trim() }
    if ($line -match "^DB_PASSWORD=(.+)$") { $dbPass = $Matches[1].Trim() }
    if ($line -match "^DB_HOST=(.+)$") { $dbHost = $Matches[1].Trim() }
    if ($line -match "^DB_PORT=(.+)$") { $dbPort = $Matches[1].Trim() }
}

Write-Host "Configuración encontrada: BD=$dbName, Usuario=$dbUser, Host=$dbHost" -ForegroundColor Green

# 2. Compilar C# Backend para Linux
Write-Host "`n[2/7] Compilando backend C# para Linux-x64 (autocontenido)..." -ForegroundColor Cyan
if (Test-Path "$BASE_DIR\publish_backend") { Remove-Item -Recurse -Force "$BASE_DIR\publish_backend" }

dotnet publish "$BASE_DIR\CatalogoPro.sln" -c Release -r linux-x64 --self-contained true -p:PublishSingleFile=true -p:PublishTrimmed=false -o "$BASE_DIR\publish_backend"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Error al compilar el backend en C#." -ForegroundColor Red
    exit 1
}

# 3. Generar appsettings.Production.json con las credenciales del VPS
Write-Host "`n[3/7] Generando appsettings de producción con credenciales PostgreSQL..." -ForegroundColor Cyan
$connectionString = "Host=$dbHost;Port=$dbPort;Database=$dbName;Username=$dbUser;Password=$dbPass;Include Error Detail=true"

$appSettings = @{
    Logging = @{
        LogLevel = @{
            Default = "Information"
            "Microsoft.AspNetCore" = "Warning"
        }
    }
    AllowedHosts = "*"
    ConnectionStrings = @{
        DefaultConnection = $connectionString
    }
    JwtSettings = @{
        Secret = "SuperSecretSecureKeyForCatalogoProSystem2026!_MustBeAtLeast32CharsLong"
        Issuer = "CatalogoProAPI"
        Audience = "CatalogoProClient"
        ExpiryInMinutes = 1440
    }
}

$appSettings | ConvertTo-Json -Depth 5 | Out-File -FilePath "$BASE_DIR\publish_backend\appsettings.Production.json" -Encoding utf8

# 4. Compilar React Frontend
Write-Host "`n[4/7] Compilando frontend React..." -ForegroundColor Cyan
Push-Location "$BASE_DIR\frontend"
npm run build
Pop-Location

if ($LASTEXITCODE -ne 0) {
    Write-Host "Error al compilar el frontend React." -ForegroundColor Red
    exit 1
}

# Comprimir frontend para subida veloz
Write-Host "Empaquetando frontend estático..." -ForegroundColor Yellow
if (Test-Path "$BASE_DIR\frontend.tar.gz") { Remove-Item "$BASE_DIR\frontend.tar.gz" }
tar -czf "$BASE_DIR\frontend.tar.gz" -C "$BASE_DIR\frontend\dist" .

# 5. Transferir archivos al VPS
Write-Host "`n[5/7] Subiendo archivos al VPS..." -ForegroundColor Cyan
# Asegurar directorios remotos
ssh "${USER}@${VPS_IP}" "sudo mkdir -p /var/www/catalogopro/api /var/www/catalogopro/frontend && sudo chown -R ${USER}:${USER} /var/www/catalogopro"

# Subir Backend
scp "$BASE_DIR\publish_backend\CatalogoPro.WebAPI" "${USER}@${VPS_IP}:/var/www/catalogopro/api/"
scp "$BASE_DIR\publish_backend\appsettings.json" "${USER}@${VPS_IP}:/var/www/catalogopro/api/"
scp "$BASE_DIR\publish_backend\appsettings.Production.json" "${USER}@${VPS_IP}:/var/www/catalogopro/api/"

# Subir Frontend
scp "$BASE_DIR\frontend.tar.gz" "${USER}@${VPS_IP}:/var/www/catalogopro/"

# Descomprimir frontend en el VPS
ssh "${USER}@${VPS_IP}" "tar -xzf /var/www/catalogopro/frontend.tar.gz -C /var/www/catalogopro/frontend/ && rm /var/www/catalogopro/frontend.tar.gz"

# Dar permisos de ejecución al ejecutable de C#
ssh "${USER}@${VPS_IP}" "chmod +x /var/www/catalogopro/api/CatalogoPro.WebAPI"

# 6. Configurar servicio Systemd en el VPS
Write-Host "`n[6/7] Configurando servicio Systemd en el VPS..." -ForegroundColor Cyan

$serviceContent = @"
[Unit]
Description=CatalogoPro C# WebAPI Service
After=network.target

[Service]
WorkingDirectory=/var/www/catalogopro/api
ExecStart=/var/www/catalogopro/api/CatalogoPro.WebAPI --urls "http://localhost:5050"
Restart=always
RestartSec=10
KillSignal=SIGINT
SyslogIdentifier=catalogopro-api
Environment=ASPNETCORE_ENVIRONMENT=Production

[Install]
WantedBy=multi-user.target
"@

$serviceContent | Out-File -FilePath "$BASE_DIR\catalogopro.service" -Encoding utf8
scp "$BASE_DIR\catalogopro.service" "${USER}@${VPS_IP}:/tmp/"
Remove-Item "$BASE_DIR\catalogopro.service"

$systemdCommands = @(
    "sudo mv /tmp/catalogopro.service /etc/systemd/system/",
    "sudo systemctl daemon-reload",
    "sudo systemctl enable catalogopro",
    "sudo systemctl restart catalogopro"
)
ssh "${USER}@${VPS_IP}" ($systemdCommands -join " && ")

# 7. Configurar Nginx en el VPS
Write-Host "`n[7/7] Configurando Nginx en el VPS..." -ForegroundColor Cyan

$pythonNginxScript = @'
import os, sys
files = os.listdir("/etc/nginx/sites-enabled")
if not files:
    print("No active Nginx configuration found.")
    sys.exit(1)
path = os.path.join("/etc/nginx/sites-available", files[0])
with open(path, "r") as f:
    content = f.read()

if "/editor-masivo" in content:
    print("Nginx already configured for /editor-masivo.")
    sys.exit(0)

last_brace = content.rfind("}")
if last_brace == -1:
    print("Invalid Nginx configuration structure.")
    sys.exit(1)

blocks = """
    # Frontend de CatalogoPRO
    location /editor-masivo {
        alias /var/www/catalogopro/frontend;
        try_files $uri $uri/ /editor-masivo/index.html;
    }

    # Backend Proxy de CatalogoPRO (C# API)
    location /api/catalogopro/ {
        proxy_pass http://localhost:5050/api/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection keep-alive;
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
"""

new_content = content[:last_brace] + blocks + content[last_brace:]
with open(path, "w") as f:
    f.write(new_content)
print("Updated Nginx configuration.")
'@

# Guardar script temporal de python en el VPS y ejecutarlo
ssh "${USER}@${VPS_IP}" "echo '$pythonNginxScript' > /tmp/update_nginx.py"
ssh "${USER}@${VPS_IP}" "sudo python3 /tmp/update_nginx.py && rm /tmp/update_nginx.py"
ssh "${USER}@${VPS_IP}" "sudo systemctl reload nginx"

Write-Host ""
Write-Host "=========================================================" -ForegroundColor Green
Write-Host "        DESPLIEGUE FINALIZADO EXITOSAMENTE               " -ForegroundColor Green
Write-Host "  Accede al editor en: https://${VPS_IP}/editor-masivo   " -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Green
