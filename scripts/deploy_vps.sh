#!/bin/bash
# Script de despliegue consolidado para Django (B2B) y CatalogoPRO (C#)
set -e

echo "=== Iniciando actualización y despliegue en VPS ==="

# 1. Obtener los últimos cambios desde GitHub
echo "--- Descargando últimos cambios desde GitHub ---"
cd /var/www/webflexs
git pull origin main

# 2. Actualizar dependencias de Django y correr migraciones
echo "--- Ejecutando actualizaciones de Django B2B ---"
source venv/bin/activate
export DJANGO_SETTINGS_MODULE=flexs_project.settings.production

set -a
source .env
set +a

pip install -r requirements.txt
python manage.py migrate --settings=flexs_project.settings.production
python manage.py check --settings=flexs_project.settings.production
python manage.py collectstatic --noinput --settings=flexs_project.settings.production

# 3. Correr scripts de base de datos o marcas
if [ -f "scratch/populate_brands_internet.py" ]; then
    echo "--- Poblando marcas y datos ---"
    python scratch/populate_brands_internet.py || true
fi

# 4. Reiniciar servicios de Django
echo "--- Reiniciando Gunicorn (Django B2B) y Celery ---"
sudo systemctl restart gunicorn
sudo systemctl restart celery || true
sudo systemctl restart celery-beat || true

# 5. Ejecutar despliegue de CatalogoPRO
echo "--- Ejecutando despliegue de CatalogoPRO (Editor Masivo C#) ---"
if [ -f "scripts/deploy_catalogopro_vps.sh" ]; then
    sudo bash scripts/deploy_catalogopro_vps.sh
fi

# 6. Recargar Nginx
echo "--- Recargando Nginx ---"
sudo systemctl reload nginx

echo "=== ¡Despliegue y actualización completados con éxito! ==="
