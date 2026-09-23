"""Admin presentation regression coverage; no accounting rule changes."""
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from admin_panel import tests as fixtures
from orders.models import Order, OrderItem
from catalog.models import Product
from django.template.loader import render_to_string
from django.test import SimpleTestCase, RequestFactory
from types import SimpleNamespace


class AdminNavigationTests(SimpleTestCase):
    def render_navigation(self, *, superuser=False, capabilities=()):
        return render_to_string('admin_panel/_module_navigation.html', {
            'user': SimpleNamespace(is_superuser=superuser),
            'admin_capabilities': capabilities,
            'request': RequestFactory().get('/admin-panel/categorias/?q=bujes'),
        })

    def test_navigation_keeps_sections_icons_and_descriptions(self):
        html = self.render_navigation()
        self.assertEqual(html.count('data-nav-section='), 4)
        for section in ('catalog', 'sales', 'fiscal', 'settings'):
            self.assertIn(f'aria-controls="admin-nav-{section}"', html)
            self.assertIn(f'id="admin-nav-{section}"', html)
        self.assertIn('Organizá tu catálogo', html)
        self.assertIn('Ordená familias y subcategorías', html)
        self.assertIn('href="#ui-icon-folder"', html)
        for route in ('admin_dashboard', 'admin_product_list', 'admin_client_dashboard',
                      'admin_order_list', 'admin_category_list', 'admin_brand_list'):
            self.assertIn(f'href="{reverse(route)}"', html)

    def test_restricted_navigation_does_not_expose_privileged_links(self):
        html = self.render_navigation(capabilities=('run_imports',))
        for route in ('admin_fiscal_config', 'admin_settings', 'admin_backup_center',
                      'admin_webhook_center', 'admin_supplier_price_list_batches'):
            self.assertNotIn(f'href="{reverse(route)}"', html)

    def test_authorized_navigation_keeps_all_management_links(self):
        html = self.render_navigation(superuser=True, capabilities=(
            'run_imports', 'manage_products', 'manage_backups', 'manage_integrations'))
        for route in ('admin_fiscal_config', 'admin_settings', 'admin_backup_center',
                      'admin_webhook_center', 'admin_supplier_price_list_batches'):
            self.assertIn(f'href="{reverse(route)}"', html)
        self.assertIn('next=/admin-panel/categorias/%3Fq%3Dbujes', html)


class AdminClarityTests(TestCase):
    client_class = fixtures.AuthorizedAdminTestClient

    def setUp(self):
        fixtures.ClientOrderHistoryViewTests.setUp(self)
        self.client.force_login(self.staff)

    def test_client_profile_prioritizes_movements_and_keeps_details(self):
        response = self.client.get(reverse('admin_client_order_history', args=[self.client_profile.pk]))
        self.assertContains(response, '<details class="client-profile-disclosure">')
        self.assertContains(response, 'Ver datos de contacto, fiscales y resumen completo')
        self.assertContains(response, 'client-compact-overview')
        self.assertContains(response, 'Nuevo movimiento')
        self.assertContains(response, 'Datos de contacto')
        self.assertNotContains(response, 'client-module-panel')
        self.assertContains(response, 'admin-module-nav')
        self.assertContains(response, 'data-nav-section="Catálogo"')

    def test_grid_groups_actions_and_starts_without_selection(self):
        self.staff.is_superuser = True
        self.staff.save(update_fields=['is_superuser'])
        response = self.client.get(reverse('admin_product_grid_editor'))
        self.assertContains(response, 'data-bulk-panel hidden', count=3)
        for label in ['Precios', 'Clasificación', 'Estado', 'bulkEmptyHint', 'selectedCount']:
            self.assertContains(response, label)

    def test_order_net_price_and_legacy_discount_are_consistent(self):
        order = Order.objects.get(user=self.client_user, status=Order.STATUS_DRAFT)
        product = Product.objects.create(sku='UI-PRICE-01', name='Producto de prueba', price=100)
        OrderItem.objects.create(order=order, product=product, product_sku=product.sku,
            product_name=product.name, quantity=2, unit_price_base=100,
            price_at_purchase=95, subtotal=190, discount_percentage_used=0)
        response = self.client.get(reverse('admin_order_detail', args=[order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Precio neto unit.')
        self.assertContains(response, 'Bonif. aplicada')
        from admin_panel.views.orders import _build_order_detail_items
        items = _build_order_detail_items(order)
        self.assertEqual(items[0].display_discount_percentage, Decimal('5.00'))
        self.assertEqual(items[0].price_at_purchase, Decimal('95.00'))

    def test_closed_movement_does_not_prompt_to_finish_or_assign_seller(self):
        from accounts.models import ClientTransaction
        order = Order.objects.get(user=self.client_user, status=Order.STATUS_CONFIRMED)
        ClientTransaction.objects.update_or_create(source_key=f'order:{order.pk}:charge', defaults={
            'client_profile': self.client_profile, 'company': self.company,
            'order': order, 'transaction_type': ClientTransaction.TYPE_ORDER_CHARGE,
            'amount': 100, 'movement_state': ClientTransaction.STATE_CLOSED,
        })
        response = self.client.get(reverse('admin_order_detail', args=[order.pk]))
        self.assertEqual(response.context['order_movement_state'], 'closed')
        self.assertContains(response, 'Movimiento cerrado')
        self.assertContains(response, 'Vendedor asignado y reglas del documento registrado.')
        self.assertNotContains(response, 'Asigná el vendedor y revisá las reglas antes de cerrar.')
