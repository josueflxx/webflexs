from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse


class LoginSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = 'secret123'
        self.user = User.objects.create_user(username='cliente_seguridad', password=self.password)

    @override_settings(
        LOGIN_MAX_FAILED_ATTEMPTS=3,
        LOGIN_LOCKOUT_SECONDS=120,
        LOGIN_ATTEMPT_WINDOW_SECONDS=300,
    )
    def test_login_lockout_after_repeated_failures(self):
        login_url = reverse('login')

        for _ in range(3):
            self.client.post(login_url, {'username': self.user.username, 'password': 'bad-pass'}, follow=True)

        response = self.client.post(
            login_url,
            {'username': self.user.username, 'password': self.password},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Demasiados intentos fallidos')
        self.assertNotIn('_auth_user_id', self.client.session)

    @override_settings(
        LOGIN_MAX_FAILED_ATTEMPTS=4,
        LOGIN_LOCKOUT_SECONDS=120,
        LOGIN_ATTEMPT_WINDOW_SECONDS=300,
    )
    def test_successful_login_still_works_before_limit(self):
        login_url = reverse('login')

        self.client.post(login_url, {'username': self.user.username, 'password': 'wrong-1'}, follow=True)
        self.client.post(login_url, {'username': self.user.username, 'password': 'wrong-2'}, follow=True)

        response = self.client.post(
            login_url,
            {'username': self.user.username, 'password': self.password},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('_auth_user_id', self.client.session)
