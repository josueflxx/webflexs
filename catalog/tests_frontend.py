"""Presentation regressions: compact layout without changing catalog pricing."""
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from catalog import tests_pricing


class CatalogWorkspaceTests(TestCase):
    def setUp(self):
        tests_pricing.CurrentCatalogPricingTests.setUp(self)

    def test_download_is_in_header_not_in_category_sidebar(self):
        response = self.client.get(reverse('catalog'), {'view': 'list'})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        sidebar = html.split('<aside class="catalog-sidebar"')[1].split('</aside>')[0]
        self.assertNotIn('excel-download-btn', sidebar)
        self.assertContains(response, 'catalog-header-actions')
        self.assertContains(response, 'Descargar Excel')
        self.assertContains(response, 'id="catalogCategorySearch"')
        self.assertContains(response, 'aria-label="Ordenar productos"')

    def test_both_presentations_keep_the_current_price_and_actions(self):
        for view in ('list', 'grid'):
            with self.subTest(view=view):
                response = self.client.get(reverse('catalog'), {'view': view, 'q': self.product.sku})
                self.assertEqual(response.status_code, 200)
                item = next(iter(response.context['page_obj']))
                self.assertEqual(item.final_price, Decimal('7250.66'))
                self.assertContains(response, 'catalog-favorite-btn')
                self.assertContains(response, 'aria-pressed="false"')
                self.assertContains(response, 'catalog_workspace.js')

    def test_anonymous_catalog_does_not_offer_private_excel(self):
        self.client.logout()
        response = self.client.get(reverse('catalog'), {'view': 'list'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="btn btn-outline excel-download-btn"')
        self.assertContains(response, 'catalogCategorySearch')
