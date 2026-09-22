from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import SiteSettings


class WhatsAppIntegrationTests(TestCase):
    def setUp(self):
        self.settings = SiteSettings.get_settings()
        self.settings.company_phone = "+11 35179090"
        self.settings.whatsapp_phone = "5491135179090"
        self.settings.whatsapp_message = "¡Hola FLEXS! Consulta"
        self.settings.whatsapp_floating_enabled = True
        self.settings.save()

    def test_whatsapp_url_format(self):
        url = self.settings.whatsapp_url
        self.assertTrue(url.startswith("https://wa.me/5491135179090?text="))
        self.assertIn("FLEXS", url)

    def test_whatsapp_url_fallback_to_company_phone(self):
        self.settings.whatsapp_phone = ""
        self.settings.company_phone = "+11 35179090"
        self.settings.save()
        url = self.settings.whatsapp_url
        self.assertTrue(url.startswith("https://wa.me/5491135179090?text="))

    def test_whatsapp_button_rendered_in_home_page(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="whatsappFloatBtn"')
        self.assertContains(response, 'https://wa.me/5491135179090')

    def test_whatsapp_button_hidden_when_disabled(self):
        self.settings.whatsapp_floating_enabled = False
        self.settings.save()
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="whatsappFloatBtn"')

    def test_admin_settings_view_updates_whatsapp(self):
        from core.models import Company
        company = Company.objects.create(name='FLEXS Test', slug='flexs-test')
        admin_user = User.objects.create_superuser(
            username='josueflexs',
            email='admin@flexs.com',
            password='testpassword123'
        )
        self.client.force_login(admin_user)
        session = self.client.session
        session['active_company_id'] = company.id
        session.save()

        post_data = {
            'company_name': 'FLEXS',
            'company_email': 'ventas@flexs.com.ar',
            'company_phone': '+11 35179090',
            'company_phone_2': '',
            'company_address': 'San Martin',
            'public_prices_message': 'Consultar',
            'whatsapp_floating_enabled': 'on',
            'whatsapp_phone': '5491199998888',
            'whatsapp_message': 'Mensaje actualizado',
        }
        response = self.client.post(reverse('admin_settings'), data=post_data)
        self.assertEqual(response.status_code, 200)

        updated_settings = SiteSettings.get_settings()
        self.assertEqual(updated_settings.whatsapp_phone, '5491199998888')
        self.assertEqual(updated_settings.whatsapp_message, 'Mensaje actualizado')
        self.assertTrue(updated_settings.whatsapp_floating_enabled)
        self.assertIn('5491199998888', updated_settings.whatsapp_url)

    def test_home_google_maps_embed(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'google.com/maps/embed')
        self.assertNotContains(response, 'leaflet')
        # Ensure CSP allows Google Maps embedding
        csp = response.headers.get('Content-Security-Policy', '')
        self.assertIn("frame-src 'self' https://www.google.com https://maps.google.com;", csp)

