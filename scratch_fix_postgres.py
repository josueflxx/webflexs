import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings')

from dotenv import load_dotenv
load_dotenv('/var/www/webflexs/.env')

django.setup()

from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='core_sitesettings';")
    cols = [row[0] for row in cursor.fetchall()]
    print("PostgreSQL core_sitesettings columns:", cols)
    if 'warehouse_stock_enabled' not in cols:
        print("Adding warehouse_stock_enabled to PostgreSQL core_sitesettings...")
        cursor.execute("ALTER TABLE core_sitesettings ADD COLUMN warehouse_stock_enabled boolean DEFAULT false NOT NULL;")
        print("Added warehouse_stock_enabled!")
        
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='core_warehouse';")
    wh_cols = [row[0] for row in cursor.fetchall()]
    print("PostgreSQL core_warehouse columns:", wh_cols)
    if 'stock_balance_enabled' not in wh_cols:
        print("Adding stock_balance_enabled to PostgreSQL core_warehouse...")
        cursor.execute("ALTER TABLE core_warehouse ADD COLUMN stock_balance_enabled boolean DEFAULT false NOT NULL;")
        print("Added stock_balance_enabled!")
