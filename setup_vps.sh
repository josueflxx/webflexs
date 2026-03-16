#!/bin/bash

# FLEXS VPS setup script
# Target: Ubuntu 24.04

set -euo pipefail

PROJECT_NAME="webflexs"
REPO_URL="https://github.com/josueflxx/webflexs.git"
DB_NAME="flexs_db"
DB_USER="flexs_user"
DB_PASS="$(openssl rand -base64 24)"
SECRET_KEY="$(openssl rand -base64 64)"
SERVER_IP="72.61.218.244"
APP_USER="flexsapp"
REDIS_URL="redis://127.0.0.1:6379/1"

echo "=== Iniciando despliegue seguro de FLEXS en $SERVER_IP ==="

echo "--- Actualizando sistema ---"
apt update && apt upgrade -y

echo "--- Instalando dependencias base ---"
apt install -y python3-pip python3-venv nginx postgresql postgresql-contrib git libpq-dev curl redis-server

echo "--- Configurando PostgreSQL ---"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1 || sudo -u postgres psql -c "CREATE DATABASE $DB_NAME;"
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER'" | grep -q 1 || sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"
sudo -u postgres psql -c "ALTER ROLE $DB_USER SET client_encoding TO 'utf8';"
sudo -u postgres psql -c "ALTER ROLE $DB_USER SET default_transaction_isolation TO 'read committed';"
sudo -u postgres psql -c "ALTER ROLE $DB_USER SET timezone TO 'UTC';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

echo "--- Creando usuario de sistema para la aplicacion ---"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$APP_USER"

echo "--- Clonando repositorio ---"
mkdir -p /var/www
cd /var/www
if [ -d "$PROJECT_NAME" ]; then
    rm -rf "$PROJECT_NAME"
fi
git clone "$REPO_URL" "$PROJECT_NAME"
cd "$PROJECT_NAME"
chown -R "$APP_USER":www-data /var/www/$PROJECT_NAME

echo "--- Configurando entorno virtual ---"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn psycopg2-binary
chown -R "$APP_USER":www-data /var/www/$PROJECT_NAME

echo "--- Creando .env de produccion ---"
cat > .env << EOF
DJANGO_SECRET_KEY=$SECRET_KEY
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=$SERVER_IP,localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=http://$SERVER_IP
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASS
DB_HOST=localhost
DB_PORT=5432
REDIS_URL=$REDIS_URL
CELERY_BROKER_URL=$REDIS_URL
CELERY_RESULT_BACKEND=$REDIS_URL
EOF
chmod 640 .env
chown "$APP_USER":www-data .env

systemctl enable redis-server
systemctl restart redis-server

echo "--- Ejecutando migraciones y staticfiles con settings de produccion ---"
export DJANGO_SETTINGS_MODULE=flexs_project.settings.production
set -a
source .env
set +a
sudo -u "$APP_USER" /var/www/$PROJECT_NAME/venv/bin/python manage.py migrate --settings=flexs_project.settings.production
sudo -u "$APP_USER" /var/www/$PROJECT_NAME/venv/bin/python manage.py collectstatic --noinput --settings=flexs_project.settings.production

echo "--- Configurando Gunicorn ---"
cat > /etc/systemd/system/gunicorn.service << EOF
[Unit]
Description=gunicorn daemon for FLEXS
After=network.target redis-server.service
Requires=redis-server.service

[Service]
User=$APP_USER
Group=www-data
WorkingDirectory=/var/www/$PROJECT_NAME
Environment=DJANGO_SETTINGS_MODULE=flexs_project.settings.production
EnvironmentFile=/var/www/$PROJECT_NAME/.env
ExecStart=/var/www/$PROJECT_NAME/venv/bin/gunicorn \
          --access-logfile - \
          --workers 3 \
          --bind unix:/run/gunicorn.sock \
          flexs_project.wsgi:application
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable gunicorn
systemctl restart gunicorn

echo "--- Configurando Nginx ---"
cat > /etc/nginx/sites-available/$PROJECT_NAME << EOF
server {
    listen 80;
    server_name $SERVER_IP;

    location = /favicon.ico { access_log off; log_not_found off; }

    location /static/ {
        root /var/www/$PROJECT_NAME;
    }

    location /media/ {
        root /var/www/$PROJECT_NAME;
    }

    location / {
        include proxy_params;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_pass http://unix:/run/gunicorn.sock;
    }
}
EOF

ln -sf /etc/nginx/sites-available/$PROJECT_NAME /etc/nginx/sites-enabled/$PROJECT_NAME
nginx -t
systemctl restart nginx

echo "--- Configurando firewall ---"
ufw allow 'Nginx Full'
ufw allow 22/tcp
echo "y" | ufw enable

echo "=== Despliegue completado ==="
echo "URL: http://$SERVER_IP"
echo "Archivo .env: /var/www/$PROJECT_NAME/.env"
