#!/bin/bash
# Script de despliegue interno en VPS para CatalogoPRO
set -e

echo "=== Iniciando despliegue de CatalogoPRO en VPS ==="

# 1. Asegurar directorios de producción y detener el servicio para liberar el binario
systemctl stop catalogopro || true
mkdir -p /var/www/catalogopro/api /var/www/catalogopro/editor-masivo
rm -rf /var/www/catalogopro/editor-masivo/*

# 2. Copiar archivos precompilados a producción
cp -r /var/www/webflexs/catalogopro_build/api/* /var/www/catalogopro/api/
cp -r /var/www/webflexs/catalogopro_build/frontend/* /var/www/catalogopro/editor-masivo/

# 3. Dar permisos de ejecución al binario
chmod +x /var/www/catalogopro/api/CatalogoPro.WebAPI

# 4. Leer configuración de base de datos desde .env de Django
DB_NAME=$(grep '^DB_NAME=' /var/www/webflexs/.env | cut -d '=' -f2- | tr -d '\r')
DB_USER=$(grep '^DB_USER=' /var/www/webflexs/.env | cut -d '=' -f2- | tr -d '\r')
DB_PASSWORD=$(grep '^DB_PASSWORD=' /var/www/webflexs/.env | cut -d '=' -f2- | tr -d '\r')
DB_HOST=$(grep '^DB_HOST=' /var/www/webflexs/.env | cut -d '=' -f2- | tr -d '\r')
DB_PORT=$(grep '^DB_PORT=' /var/www/webflexs/.env | cut -d '=' -f2- | tr -d '\r')

# Valores por defecto en caso de no estar definidos
DB_NAME=${DB_NAME:-flexs_db}
DB_USER=${DB_USER:-flexs_user}
DB_PASSWORD=${DB_PASSWORD:-}
DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5432}

# 5. Generar appsettings.Production.json
CATALOGO_CONN="Host=${DB_HOST};Port=${DB_PORT};Database=${DB_NAME};Username=${DB_USER};Password=${DB_PASSWORD};Include Error Detail=true"

cat <<EOF > /var/www/catalogopro/api/appsettings.Production.json
{
  "Logging": {
    "LogLevel": {
      "Default": "Information",
      "Microsoft.AspNetCore": "Warning"
    }
  },
  "AllowedHosts": "*",
  "ConnectionStrings": {
    "DefaultConnection": "${CATALOGO_CONN}"
  },
  "JwtSettings": {
    "Secret": "SuperSecretSecureKeyForCatalogoProSystem2026!_MustBeAtLeast32CharsLong",
    "Issuer": "CatalogoProAPI",
    "Audience": "CatalogoProClient",
    "ExpiryInMinutes": 1440
  }
}
EOF

# 6. Crear o actualizar servicio Systemd
cat <<EOF > /etc/systemd/system/catalogopro.service
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
EOF

# 5.5 Asegurar que el runtime de ASP.NET Core 8 esté instalado en el VPS
if ! dpkg -s aspnetcore-runtime-8.0 &> /dev/null; then
    echo "Instalando ASP.NET Core 8 runtime en el VPS..."
    apt-get update
    apt-get install -y aspnetcore-runtime-8.0
fi

# Recargar y reiniciar el servicio
systemctl daemon-reload
systemctl enable catalogopro
systemctl restart catalogopro

# 7. Configurar Nginx si no está inyectado
NGINX_FILE=""
for f in /etc/nginx/sites-enabled/*; do
    if [ -f "$f" ]; then
        NGINX_FILE="$f"
        break
    fi
done

if [ -n "$NGINX_FILE" ]; then
    if ! grep -q "/editor-masivo" "$NGINX_FILE"; then
        echo "Inyectando reglas de Nginx en $NGINX_FILE..."
        python3 -c "
with open('$NGINX_FILE', 'r') as f:
    content = f.read()

last_brace = content.rfind('}')
if last_brace == -1:
    import sys
    sys.exit(1)

blocks = '''
    # Frontend de CatalogoPRO
    location /editor-masivo {
        root /var/www/catalogopro;
        try_files \$uri \$uri/ /editor-masivo/index.html;
    }

    # Backend Proxy de CatalogoPRO (C# API)
    location /api/catalogopro/ {
        proxy_pass http://localhost:5050/api/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection keep-alive;
        proxy_set_header Host \$host;
        proxy_cache_bypass \$http_upgrade;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
'''

new_content = content[:last_brace] + blocks + content[last_brace:]
with open('$NGINX_FILE', 'w') as f:
    f.write(new_content)
"
        systemctl reload nginx
        echo "Nginx configurado y recargado con éxito."
    else
        echo "Nginx ya tiene la ruta /editor-masivo configurada."
    fi
else
    echo "No se encontró el archivo de configuración activo de Nginx."
fi

echo "=== Despliegue de CatalogoPRO completado con éxito ==="
