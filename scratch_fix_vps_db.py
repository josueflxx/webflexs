from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("PRAGMA table_info('core_sitesettings')")
    cols = [row[1] for row in cursor.fetchall()]
    print("Current core_sitesettings columns:", cols)
    if 'warehouse_stock_enabled' not in cols:
        print("Adding warehouse_stock_enabled to core_sitesettings...")
        cursor.execute("ALTER TABLE core_sitesettings ADD COLUMN warehouse_stock_enabled bool DEFAULT 0 NOT NULL;")
        print("Added warehouse_stock_enabled!")
    
    cursor.execute("PRAGMA table_info('core_warehouse')")
    wh_cols = [row[1] for row in cursor.fetchall()]
    print("Current core_warehouse columns:", wh_cols)
    if 'stock_balance_enabled' not in wh_cols:
        print("Adding stock_balance_enabled to core_warehouse...")
        cursor.execute("ALTER TABLE core_warehouse ADD COLUMN stock_balance_enabled bool DEFAULT 0 NOT NULL;")
        print("Added stock_balance_enabled!")
