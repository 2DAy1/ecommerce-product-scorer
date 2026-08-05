import json

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings


@override_settings(ALLOWED_HOSTS=["testserver"])
class HealthCheckTests(SimpleTestCase):
    def test_healthcheck_returns_ok(self) -> None:
        response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


@override_settings(ALLOWED_HOSTS=["testserver"])
class SessionAuthenticationTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(
            username="dashboard-user",
            password="test-password",
        )
        self.client = Client(enforce_csrf_checks=True)

    def csrf_token(self) -> str:
        response = self.client.get("/api/auth/session/")
        return response.cookies["csrftoken"].value

    def test_session_endpoint_sets_csrf_cookie_for_anonymous_user(self) -> None:
        response = self.client.get("/api/auth/session/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"authenticated": False, "username": ""},
        )
        self.assertIn("csrftoken", response.cookies)

    def test_login_requires_csrf_and_establishes_session(self) -> None:
        payload = json.dumps(
            {"username": "dashboard-user", "password": "test-password"}
        )

        rejected = self.client.post(
            "/api/auth/login/",
            payload,
            content_type="application/json",
        )
        token = self.csrf_token()
        response = self.client.post(
            "/api/auth/login/",
            payload,
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"authenticated": True, "username": "dashboard-user"},
        )
        self.assertIn("sessionid", self.client.cookies)

    def test_invalid_credentials_return_useful_error(self) -> None:
        token = self.csrf_token()

        response = self.client.post(
            "/api/auth/login/",
            json.dumps({"username": "dashboard-user", "password": "wrong"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid username or password.")

    def test_logout_ends_authenticated_session(self) -> None:
        self.client.force_login(self.user)
        token = self.csrf_token()

        response = self.client.post(
            "/api/auth/logout/",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        session = self.client.get("/api/auth/session/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(session.json()["authenticated"])
