"""Local-only catalog preview with disposable database, accounts and stock."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
os.environ["DJANGO_SETTINGS_MODULE"] = "flexs_project.settings.test"
from django.conf import settings


class PreviewIdentityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.contrib.auth import get_user_model
        if request.GET.get('demo_role') in ('admin', 'client'):
            request.session['demo_role'] = request.GET['demo_role']
        role = request.session.get('demo_role', 'admin')
        request.user = get_user_model().objects.get(pk=settings.PREVIEW_USER_IDS[role])
        request.session['active_company_id'] = settings.PREVIEW_COMPANY_ID
        response = self.get_response(request)
        if 'text/html' in response.get('Content-Type', '') and not response.streaming:
            notice = '<div style="background:#20252c;color:#cbd3de;font:11px/1.5 system-ui;padding:7px 20px;text-align:center;border-bottom:1px solid #353b45">VISTA PREVIA LOCAL · Productos, precios y stock de muestra · <a style="color:#ff956f" href="?view=list&demo_role=admin">Vista admin</a> / <a style="color:#ff956f" href="?view=list&demo_role=client">Vista cliente</a></div>'
            response.content = response.content.replace(b'<main class="main-content">', b'<main class="main-content">' + notice.encode())
            if notice.encode() not in response.content:
                response.content = response.content.replace(b'</header>', b'</header>' + notice.encode(), 1)
            if 'Content-Length' in response: response['Content-Length'] = str(len(response.content))
        return response


def main():
    with TemporaryDirectory(prefix='flexs-catalog-ui-preview-') as directory:
        settings.DATABASES['default']['NAME'] = str(Path(directory) / 'preview.sqlite3')
        settings.MEDIA_ROOT = Path(directory) / 'media'
        settings.STATIC_ROOT = Path(directory) / 'static'
        settings.STATIC_ROOT.mkdir()
        settings.CATALOG_EXCEL_CACHE_DIR = str(Path(directory) / 'exports')
        settings.DEBUG = True
        settings.SESSION_COOKIE_SECURE = False
        settings.CSRF_COOKIE_SECURE = False
        settings.TEMPLATES[0]['APP_DIRS'] = False
        settings.TEMPLATES[0]['OPTIONS']['loaders'] = ['django.template.loaders.filesystem.Loader', 'django.template.loaders.app_directories.Loader']
        index = settings.MIDDLEWARE.index('django.contrib.auth.middleware.AuthenticationMiddleware')
        settings.MIDDLEWARE.insert(index + 1, 'scripts.preview_catalog_frontend.PreviewIdentityMiddleware')
        import django
        django.setup()
        from django.core.management import call_command
        from django.contrib.auth import get_user_model
        from django.db import connections
        from accounts.models import ClientProfile, ClientCompany
        from catalog.models import Category, Product, PriceList
        from core.models import SiteSettings, Company, CatalogExcelTemplate, CatalogExcelTemplateSheet, CatalogExcelTemplateColumn
        from core.services import catalog_excel_jobs as jobs
        from concurrent.futures import ThreadPoolExecutor

        call_command('migrate', verbosity=0, interactive=False)
        company, _ = Company.objects.get_or_create(slug='flexs', defaults={'name': 'Flexs'})
        price_list, _ = PriceList.objects.get_or_create(company=company, slug='base', defaults={'name': 'Flexs Base'})
        company.default_price_list = price_list
        company.save(update_fields=['default_price_list'])
        admin = get_user_model().objects.create_superuser(username='preview-admin', password=None)
        client = get_user_model().objects.create_user(username='preview-client', password=None)
        profile = ClientProfile.objects.create(user=client, company_name='Cliente de muestra', is_approved=True)
        ClientCompany.objects.create(client_profile=profile, company=company)
        settings.PREVIEW_USER_IDS = {'admin': admin.pk, 'client': client.pk}
        settings.PREVIEW_COMPANY_ID = company.pk
        site = SiteSettings.get_settings()
        site.show_public_prices = True
        site.save()
        tree = [('Abrazaderas', ['Trefiladas', 'Laminadas']), ('Acero', ['Comunes', 'Especiales']), ('Acero NHK', []), ('Alemites', []), ('Arandelas', ['Planas', 'De presión']), ('Bujes', ['Armados', 'De goma', 'De bronce']), ('Bulones', []), ('Escuadras', []), ('Pernos', []), ('Pitones', []), ('Plaquetas', []), ('Soportes', []), ('Tensores', []), ('Tuercas', [])]
        roots = {}
        from django.utils.text import slugify
        for order, (name, children) in enumerate(tree):
            root = Category.objects.create(name=name.upper(), public_name=name, slug=slugify(name), order=order, public_order=order)
            roots[name] = root
            for child in children:
                Category.objects.create(name=child.upper(), public_name=child, slug=slugify(name + '-' + child), parent=root)
        samples = [
            ('BA05001', '1/2 B.ARM C/PESTAÑA IVECO DAILY CORTO 5/8X35-40X32', '9376.27', 0),
            ('BA05002', '1/2 B.ARM C/PESTAÑA IVECO DAILY LARGO 5/8X36 HOJA 70', '14072.30', 12),
            ('BA03040', '1/2 B.ARM FORD CARGO CABAÑA D/P/T MACIZO', '9589.64', 8),
            ('BA03041', '1/2 B.ARM FORD SAPO BISAGRA CAPOT F-14000', '9719.23', 0),
            ('BA04001', '1/2 BUJE ARMADO ELÁSTICO DELANTERO', '7250.66', 24),
            ('BA10001', '1/2 BUJE ARMADO SUSPENSIÓN TRASERA', '7250.66', 6),
            ('BA05003', '1/2 BUJE C/PESTAÑA IVECO DAILY REFORZADO', '16820.50', 0),
            ('BA04002', '1/2 BUJE FORD CARGO OJO DE ELÁSTICO', '10980.00', 18),
        ]
        bujes = Category.objects.get(slug='bujes-armados')
        for sku, name, price, stock in samples:
            product = Product.objects.create(sku=sku, name=name, price=price, stock=stock, category=bujes, supplier='Proveedor de muestra')
            product.categories.add(bujes)
        for root in roots.values():
            for category in list(root.children.all()) or [root]:
                for index in range(2):
                    product = Product.objects.create(sku=f'DEMO-{category.pk:02}-{index+1}', name=f'{category.display_name} {root.display_name} — medida {index+1}', price=5800 + 325 * category.pk + 500 * index, category=category, stock=0 if index else 15)
                    product.categories.add(category)
        template = CatalogExcelTemplate.objects.create(name='Vista previa local', is_active=True, is_client_download_enabled=True)
        sheet = CatalogExcelTemplateSheet.objects.create(template=template, name='Catálogo de muestra')
        sheet.categories.add(*roots.values())
        for order, key in enumerate(('sku', 'name', 'price'), 1):
            CatalogExcelTemplateColumn.objects.create(sheet=sheet, key=key, order=order)
        # This preview has its own queue and database, never the production broker.
        executor = ThreadPoolExecutor(max_workers=1)
        def publish_preview(spec, token):
            def generate():
                try: jobs.generate_export(spec, token)
                finally: connections.close_all()
            executor.submit(generate)
        jobs.publish_export = publish_preview
        print('LOCAL PREVIEW ONLY: http://127.0.0.1:8769/catalogo/?view=list', flush=True)
        try:
            call_command('runserver', '127.0.0.1:8769', use_reloader=False, use_threading=True, insecure=True)
        finally:
            executor.shutdown(wait=True)
            connections.close_all()


if __name__ == '__main__':
    main()
