from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from categories.models import Category
from products.models import Product
from users.models import User


class ProductOwnershipTestCase(TestCase):
    """Tests that sellers can only access their own products."""

    def setUp(self):
        self.client = APIClient()

        self.seller1 = User.objects.create_user(
            email="seller1@test.com", password="testpass123", role="SELLER"
        )
        self.seller2 = User.objects.create_user(
            email="seller2@test.com", password="testpass123", role="SELLER"
        )

        category = Category.objects.create(name="Electronics", slug="electronics")

        self.product1 = Product.objects.create(
            seller=self.seller1,
            category=category,
            name="Seller1 Phone",
            price=Decimal("15000.00"),
            stock=10,
        )
        self.product2 = Product.objects.create(
            seller=self.seller2,
            category=category,
            name="Seller2 Laptop",
            price=Decimal("50000.00"),
            stock=10,
        )

    def _auth_as(self, user):
        token = RefreshToken.for_user(user)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token.access_token}"
        )

    def test_seller_sees_only_own_products(self):
        self._auth_as(self.seller1)
        response = self.client.get("/api/products/my/")
        self.assertEqual(response.status_code, 200)
        names = [p["name"] for p in response.data["results"]]
        self.assertIn("Seller1 Phone", names)
        self.assertNotIn("Seller2 Laptop", names)

    def test_seller_cannot_access_other_sellers_product(self):
        self._auth_as(self.seller1)
        response = self.client.get(f"/api/products/my/{self.product2.id}/")
        self.assertEqual(response.status_code, 404)

    def test_seller_cannot_update_other_sellers_product(self):
        self._auth_as(self.seller1)
        response = self.client.patch(
            f"/api/products/my/{self.product2.id}/",
            {"price": "1.00"},
        )
        self.assertEqual(response.status_code, 404)

    def test_seller_cannot_delete_other_sellers_product(self):
        self._auth_as(self.seller1)
        response = self.client.delete(f"/api/products/my/{self.product2.id}/")
        self.assertEqual(response.status_code, 404)

    def test_buyer_cannot_create_product(self):
        buyer = User.objects.create_user(
            email="buyer@test.com", password="testpass123", role="BUYER"
        )
        self._auth_as(buyer)
        response = self.client.post(
            "/api/products/my/",
            {
                "name": "Hack Product",
                "price": "100.00",
                "stock": 1,
                "category": 1,
            },
        )
        self.assertEqual(response.status_code, 403)


class PublicProductListTestCase(TestCase):
    """Tests for the public product browsing API."""

    def setUp(self):
        self.client = APIClient()

        seller = User.objects.create_user(
            email="seller@test.com", password="testpass123", role="SELLER"
        )
        category = Category.objects.create(name="Electronics", slug="electronics")

        Product.objects.create(
            seller=seller,
            category=category,
            name="Active Phone",
            price=Decimal("15000.00"),
            stock=10,
            is_active=True,
        )
        Product.objects.create(
            seller=seller,
            category=category,
            name="Hidden Phone",
            price=Decimal("10000.00"),
            stock=5,
            is_active=False,
        )

    def test_public_list_excludes_inactive(self):
        response = self.client.get("/api/products/")
        self.assertEqual(response.status_code, 200)
        names = [p["name"] for p in response.data["results"]]
        self.assertIn("Active Phone", names)
        self.assertNotIn("Hidden Phone", names)

    def test_search_by_name(self):
        response = self.client.get("/api/products/?search=Active")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["name"], "Active Phone")


class SemanticSearchViewTestCase(TestCase):
    """PublicProductListView's own orchestration around semantic_search() -
    mocked here since the real ranking needs Postgres, this only tests the
    view's decisions given controlled return values."""

    def setUp(self):
        self.client = APIClient()

        seller = User.objects.create_user(
            email="seller@test.com", password="testpass123", role="SELLER"
        )
        self.electronics = Category.objects.create(name="Electronics", slug="electronics")
        self.grocery = Category.objects.create(name="Grocery", slug="grocery")

        self.laptop = Product.objects.create(
            seller=seller, category=self.electronics, name="Laptop",
            price=Decimal("50000.00"), stock=5, is_active=True,
        )
        self.coffee = Product.objects.create(
            seller=seller, category=self.grocery, name="Coffee",
            price=Decimal("300.00"), stock=5, is_active=True,
        )

    @patch("products.views.semantic_search")
    def test_semantic_results_are_used_and_flags_propagate(self, mock_search):
        mock_search.return_value = (Product.objects.filter(id=self.laptop.id).order_by("id"), False, 1)

        response = self.client.get("/api/products/?search=laptop")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_semantic"])
        self.assertFalse(response.data["is_fallback"])
        self.assertFalse(response.data["category_relaxed"])
        self.assertEqual(response.data["confident_count"], 1)
        names = [p["name"] for p in response.data["results"]]
        self.assertEqual(names, ["Laptop"])

    @patch("products.views.semantic_search")
    def test_empty_semantic_result_falls_back_to_icontains(self, mock_search):
        mock_search.return_value = (Product.objects.none(), True, 0)

        response = self.client.get("/api/products/?search=Coffee")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_semantic"])
        self.assertFalse(response.data["is_fallback"])
        names = [p["name"] for p in response.data["results"]]
        self.assertEqual(names, ["Coffee"])

    @patch("products.views.semantic_search")
    def test_category_scoped_empty_result_retries_catalog_wide(self, mock_search):
        # First call (scoped to Grocery) finds nothing, second call (no
        # category) finds the laptop instead.
        mock_search.side_effect = [
            (Product.objects.none(), True, 0),
            (Product.objects.filter(id=self.laptop.id).order_by("id"), False, 1),
        ]

        response = self.client.get(f"/api/products/?search=laptop&category={self.grocery.id}")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_semantic"])
        self.assertTrue(response.data["category_relaxed"])
        names = [p["name"] for p in response.data["results"]]
        self.assertEqual(names, ["Laptop"])

        self.assertEqual(mock_search.call_args_list[0].kwargs["category_id"], str(self.grocery.id))
        self.assertIsNone(mock_search.call_args_list[1].kwargs["category_id"])

    @patch("products.views.semantic_search")
    def test_explicit_ordering_overrides_relevance(self, mock_search):
        mock_search.return_value = (
            Product.objects.filter(id__in=[self.laptop.id, self.coffee.id]), False, 2
        )

        response = self.client.get("/api/products/?search=x&ordering=price")

        prices = [float(p["price"]) for p in response.data["results"]]
        self.assertEqual(prices, sorted(prices))

    @patch("products.views.semantic_search")
    def test_no_explicit_ordering_keeps_relevance_order(self, mock_search):
        # Ordered by -price, distinguishable from the default -created_at
        # (which would put Coffee, created second, ahead of Laptop).
        queryset = Product.objects.filter(id__in=[self.laptop.id, self.coffee.id]).order_by("-price")
        mock_search.return_value = (queryset, False, 2)

        response = self.client.get("/api/products/?search=x")

        names = [p["name"] for p in response.data["results"]]
        self.assertEqual(names, list(queryset.values_list("name", flat=True)))
