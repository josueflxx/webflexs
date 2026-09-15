"""Read-only dashboard preview with example data; never accesses a real database.

Run python scripts/preview_dashboard.py and open http://127.0.0.1:8768/admin-panel/.
Use ?empty=1 for the empty state and ?large=1 for long amounts.
Switch between light and dark using the theme button in the page header.
"""
import os
import sys
from datetime import timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE'] = 'flexs_project.settings.test'

import django
django.setup()
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone


def example_context(query):
    now = timezone.localtime()
    rows = [
        ('requests_inbox', 'Solicitudes por revisar', 0, 'Pedidos del portal y propuestas que siguen en gestión comercial.', 'admin_order_request_list'),
        ('orders_to_confirm', 'Pedidos por confirmar', 4, 'Borradores y pedidos que todavía necesitan cierre comercial.', 'admin_order_list'),
        ('orders_in_preparation', 'Pedidos en curso', 5, 'Pedidos aprobados que siguen en preparación.', 'admin_order_list'),
        ('orders_to_invoice', 'Pedidos listos para facturar', 4, 'Ventas sin factura ni comprobante vinculado.', 'admin_order_list'),
        ('fiscal_pending', 'Comprobantes a resolver', 1, 'Listos, en envío o con reintento pendiente.', 'admin_fiscal_document_list'),
        ('open_movements', 'Movimientos abiertos', 0, 'Movimientos todavía no cerrados o por revisar.', 'admin_payment_list'),
        ('clients_with_debt', 'Clientes con deuda', 0, 'Clientes con saldo positivo en cuenta corriente.', 'admin_client_report_debtors'),
        ('critical_stock', 'Stock crítico', 11578, 'Productos activos con stock igual o menor a 5 unidades.', 'admin_product_list'),
        ('recent_payments', 'Cobros registrados', 0, 'Cobros activos listos para seguimiento.', 'admin_payment_list'),
    ]
    empty = 'empty' in query
    amount = 1234567890.12 if 'large' in query else 0
    user = SimpleNamespace(username='admin_demo', first_name='Admin', is_superuser=True, is_authenticated=True)
    return {
        'user': user,
        'request': SimpleNamespace(user=user, resolver_match=SimpleNamespace(url_name='admin_dashboard'), get_full_path=lambda: '/admin-panel/'),
        'active_company': SimpleNamespace(name='Flexs'),
        'admin_capabilities': ['run_imports', 'manage_products', 'manage_backups', 'manage_integrations'],
        'dashboard_generated_at': now,
        'today_sales': {'sales_total': amount, 'documents_count': 0},
        'month_sales': {'sales_total': amount, 'documents_count': 0, 'average_ticket': amount},
        'estimated_margin': amount, 'pending_orders_count': 0 if empty else 9,
        'my_client_tasks_count': 0, 'my_client_tasks_overdue': 0, 'new_clients_month': 0,
        'operational_snapshot_cards': [dict(key=key, label=label, count=0 if empty else count, help_text=help_text, url=reverse(url)) for key, label, count, help_text, url in rows],
        'recent_activity': [] if empty else [dict(badge='Estado' if n % 2 == 0 else 'Pedido', title=f'Pedido #{13 - n // 2} → Confirmado', summary='Cliente de ejemplo | Confirmado al cerrar el movimiento en cuenta corriente.', occurred_at=now - timedelta(minutes=5+n*13), actor_label='admin_demo', scope_label='Flexs', amount=None, detail_url=reverse('admin_order_list'), detail_label='Ver pedido') for n in range(10)],
        'top_clients_rank': [], 'top_products_rank': [], 'top_debtors_rank': [],
    }


class PreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'core' / 'static'), **kwargs)

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path.startswith('/static/'):
            self.path = parsed.path.removeprefix('/static')
            return super().do_GET()
        if parsed.path not in ('/', '/admin-panel/'):
            self.send_error(404, 'Preview: navigation is disabled')
            return
        query = parse_qs(parsed.query)
        html = render_to_string('admin_panel/dashboard.html', example_context(query))
        html = html.replace('</title>', ' · Vista previa local</title>', 1)
        payload = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == '__main__':
    print('Vista previa local con datos de ejemplo: http://127.0.0.1:8768/admin-panel/', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8768), PreviewHandler).serve_forever()
