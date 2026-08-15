from django.test import TestCase
from rest_framework.test import APIClient

from users.models import User


class RegistrationTestCase(TestCase):
    """Tests for user registration."""

    def setUp(self):
        self.client = APIClient()

    def test_register_buyer(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "buyer@test.com",
                "password": "securepass123",
                "role": "BUYER",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["user"]["role"], "BUYER")
        self.assertIn("access", response.data["tokens"])
        self.assertIn("refresh", response.data["tokens"])

        user = User.objects.get(email="buyer@test.com")
        self.assertEqual(user.role, "BUYER")
        self.assertFalse(user.is_staff)

    def test_register_seller(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "seller@test.com",
                "password": "securepass123",
                "role": "SELLER",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["user"]["role"], "SELLER")

    def test_register_seller_with_currency(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "usdseller@test.com",
                "password": "securepass123",
                "role": "SELLER",
                "currency": "USD",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["user"]["currency"], "USD")

        user = User.objects.get(email="usdseller@test.com")
        self.assertEqual(user.currency, "USD")

    def test_register_without_currency_defaults_to_inr(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "defaultseller@test.com",
                "password": "securepass123",
                "role": "SELLER",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["user"]["currency"], "INR")

        user = User.objects.get(email="defaultseller@test.com")
        self.assertEqual(user.currency, "INR")

    def test_register_duplicate_email(self):
        User.objects.create_user(
            email="existing@test.com", password="testpass123", role="BUYER"
        )
        response = self.client.post(
            "/api/auth/register/",
            {
                "email": "existing@test.com",
                "password": "securepass123",
                "role": "BUYER",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_register_missing_fields(self):
        response = self.client.post("/api/auth/register/", {"email": "a@b.com"})
        self.assertEqual(response.status_code, 400)


class LoginTestCase(TestCase):
    """Tests for JWT login."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="user@test.com", password="testpass123", role="BUYER"
        )

    def test_login_success(self):
        response = self.client.post(
            "/api/auth/login/",
            {"email": "user@test.com", "password": "testpass123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    def test_login_wrong_password(self):
        response = self.client.post(
            "/api/auth/login/",
            {"email": "user@test.com", "password": "wrongpass"},
        )
        self.assertEqual(response.status_code, 401)

    def test_login_nonexistent_user(self):
        response = self.client.post(
            "/api/auth/login/",
            {"email": "nobody@test.com", "password": "testpass123"},
        )
        self.assertEqual(response.status_code, 401)
