#!/bin/bash

# FLEXS VPS Auto-Setup Script
# Target: Ubuntu 24.04
# IP: 72.61.218.244

set -e

PROJECT_NAME="webflexs"
REPO_URL="https://github.com/josueflxx/webflexs.git"
DB_NAME="flexs_db"
DB_USER="flexs_user"
DB_PASS=$(openssl rand -base64 12)
SECRET_KEY=$(openssl rand -base64 32)
SERVER_IP="72.61.218.244"

echo "=== 🚀 Iniciando Despliegue de FLEXS B2B en $SERVER_IP ==="

# 1. Update system
echo "--- Actualizando sistema ---"
apt update && apt upgrade -y

# 2. Install dependencies
echo "--- Instalando dependencias (Python, Nginx, Postgres) ---"
apt install -y python3-pip python3-venv nginx postgresql postgresql-contrib git libpq-dev curl

# 3. Setup PostgreSQL
echo "--- Configurando Base de Datos ---"
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME;"
sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"
sudo -u postgres psql -c "ALTER ROLE $DB_USER SET client_encoding TO 'utf8';"
sudo -u postgres psql -c "ALTER ROLE $DB_USER SET default_transaction_isolation TO 'read committed';"
sudo -u postgres psql -c "ALTER ROLE $DB_USER SET timezone TO 'UTC';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

# 4. Clone Repository
echo "--- Clonando Repositorio ---"
cd /var/www
if [ -d "$PROJECT_NAME" ]; then
    rm -rf "$PROJECT_NAME"
fi
git clone $REPO_URL
cd $PROJECT_NAME

# 5. Virtual Environment
echo "--- Configurando Entorno Virtual ---"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn psycopg2-binary

# 6. Create .env file
echo "--- Creando archivo de configuración (.env) ---"
cat > .env << EOF
DJANGO_SECRET_KEY=$SECRET_KEY
DJANGO_DEBUG=False
DJANGO_SETTINGS_MODULE=flexs_project.settings.production
DJANGO_ALLOWED_HOSTS=$SERVER_IP,localhost,127.0.0.1
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASS
DB_HOST=localhost
DB_PORT=5432
EOF

# 7. Django setup
echo "--- Ejecutando Migraciones y Static Files ---"
python manage.py migrate
python manage.py collectstatic --noinput

# 8. Gunicorn Setup (Systemd)
echo "--- Configurando Gunicorn (Systemd) ---"
cat > /etc/systemd/system/gunicorn.service << EOF
[Unit]
Description=gunicorn daemon
After=network.target

[Service]
User=root
Group=www-data
WorkingDirectory=/var/www/$PROJECT_NAME
ExecStart=/var/www/$PROJECT_NAME/venv/bin/gunicorn \\
          --access-logfile - \\
          --workers 3 \\
          --bind unix:/run/gunicorn.sock \\
          flexs_project.wsgi:application

[Install]
WantedBy=multi-user.target
EOF

systemctl start gunicorn
systemctl enable gunicorn

# 9. Nginx Setup
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
        proxy_pass http://unix:/run/gunicorn.sock;
    }
}
EOF

ln -sf /etc/nginx/sites-available/$PROJECT_NAME /etc/nginx/sites-enabled
nginx -t
systemctl restart nginx

# 10. Firewall
echo "--- Configurando Firewall ---"
ufw allow 'Nginx Full'
ufw allow 22/tcp
echo "y" | ufw enable

echo "=== ✅ DESPLIEGUE COMPLETADO ==="
echo "URL: http://$SERVER_IP"
echo "Credenciales DB guardadas en /var/www/$PROJECT_NAME/.env"
