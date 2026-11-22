"""The Cloudflare Worker in deploy/fallback tells Django responses apart from
Railway gateway errors by the X-Request-ID header. Keep that contract intact."""
from django.test import TestCase, override_settings


class EdgeFallbackContractTests(TestCase):
    def test_health_has_request_id(self):
        response = self.client.get('/health/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers.get('X-Request-ID'))

    def test_unknown_path_has_request_id(self):
        response = self.client.get('/this-page-does-not-exist-xyz/')
        self.assertTrue(response.headers.get('X-Request-ID'))

    @override_settings(MAINTENANCE_MODE=True)
    def test_maintenance_has_request_id(self):
        response = self.client.get('/some-page/')
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.headers.get('X-Request-ID'))

    def test_request_id_is_echoed(self):
        response = self.client.get('/health/', HTTP_X_REQUEST_ID='abc123')
        self.assertEqual(response.headers.get('X-Request-ID'), 'abc123')
