"""Presentation changes must preserve prices, visibility and return context."""
import json
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import urlencode

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from catalog.models import ClampSpecs
from catalog.services.presentation import diameter_label, safe_catalog_return_url
from catalog import tests_pricing


class PublicPresentationHelpersTests(SimpleTestCase):
    def test_units_are_explicit_without_changing_values(self):
        for value, label in [('3/4', '3/4″ (pulgadas)'), ('1', '1″ (pulgadas)'),
                             ('18', '18 mm'), ('20', '20 mm'), ('1.25', '1.25 (unidad sin confirmar)')]:
            self.assertEqual(diameter_label(value), label)

    def test_return_links_reject_external_or_malformed_urls(self):
        for value in ['https://example.org/', '//example.org/', '//[', '/admin-panel/',
                      '/catalogo/\n', '/catalogo/\\example.org', 'javascript:alert(1)', None]:
            self.assertEqual(safe_catalog_return_url(value), '/catalogo/')
        for value in ['/catalogo/?category=abrazaderas&diameter=3%2F4&page=2',
                      '/catalogo/marcas/agrale/?rubro=bujes&q=armado&view=list']:
            self.assertEqual(safe_catalog_return_url(value), value)


class PublicClarityTests(TestCase):
    def setUp(self):
        tests_pricing.CurrentCatalogPricingTests.setUp(self)

    def test_zero_stock_is_orderable_in_both_catalog_layouts(self):
        self.assertEqual(self.product.stock, 0)
        class Buttons(HTMLParser):
            def __init__(self):
                super().__init__()
                self.cart = []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'button' and 'addToCart' in attrs.get('onclick', ''):
                    self.cart.append(attrs)
        for view in ['list', 'grid']:
            response = self.client.get(reverse('catalog'), {'view': view})
            self.assertContains(response, 'Por encargo')
            self.assertContains(response, 'Precio final · IVA incluido')
            parser = Buttons()
            parser.feed(response.content.decode())
            self.assertTrue(parser.cart)
            self.assertTrue(all('disabled' not in attrs for attrs in parser.cart))
        response = self.client.post(reverse('add_to_cart'), json.dumps({
            'product_id': self.product.pk, 'quantity': 2,
        }), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])

    def test_detail_shows_specs_and_preserves_filters_and_price(self):
        ClampSpecs.objects.create(product=self.product, fabrication='TREFILADA', diameter='3/4',
                                  width=75, length=120, shape='CURVA', manual_override=True)
        origin = '/catalogo/?category=abrazaderas&diameter=3%2F4&page=2&view=list'
        response = self.client.get(reverse('product_detail', args=[self.product.sku]), {'next': origin})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['catalog_return_url'], origin)
        self.assertEqual(response.context['final_price'], Decimal('8773.30'))
        for text in ['3/4″ (pulgadas)', '75 mm', '120 mm', 'Por encargo', 'Volver a los resultados']:
            self.assertContains(response, text)
        self.assertNotContains(response, 'Stock:')

    def test_listing_link_keeps_search_context(self):
        params = {'view': 'list', 'q': self.product.sku}
        response = self.client.get(reverse('catalog'), params)
        expected = reverse('product_detail', args=[self.product.sku]) + '?' + urlencode({
            'next': reverse('catalog') + '?' + urlencode(params),
        })
        self.assertContains(response, expected)

    def test_inactive_products_stay_hidden(self):
        self.product.is_active = False
        self.product.save(update_fields=['is_active'])
        response = self.client.get(reverse('catalog'))
        self.assertNotContains(response, self.product.sku)
        self.assertEqual(self.client.get(reverse('product_detail', args=[self.product.sku])).status_code, 404)
