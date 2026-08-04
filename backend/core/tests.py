from django.test import SimpleTestCase, override_settings


@override_settings(ALLOWED_HOSTS=["testserver"])
class HealthCheckTests(SimpleTestCase):
    def test_healthcheck_returns_ok(self) -> None:
        response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
