from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from cart.models import Cart, CartItem
from categories.models import Category
from orders.models import Order, OrderItem
from products.models import Product
from users.models import User


class CheckoutTestCase(TestCase):
    """Tests for the checkout flow - the most critical path in the application."""

    def setUp(self):
        self.client = APIClient()

        self.buyer = User.objects.create_user(
            email="buyer@test.com", password="testpass123", role="BUYER"
        )
        self.seller = User.objects.create_user(
            email="seller@test.com", password="testpass123", role="SELLER"
        )

        self.category = Category.objects.create(name="Electronics", slug="electronics")

        self.product = Product.objects.create(
            seller=self.seller,
            category=self.category,
            name="Test Phone",
            price=Decimal("15000.00"),
            stock=5,
        )

        self.cart = Cart.objects.create(buyer=self.buyer)
        self.cart_item = CartItem.objects.create(
            cart=self.cart, product=self.product, quantity=2
        )

        token = RefreshToken.for_user(self.buyer)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token.access_token}"
        )

    def test_successful_checkout(self):
        response = self.client.post("/api/orders/checkout/")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            Decimal(str(response.data["total_amount"])), Decimal("30000.00")
        )

        # Order and items created
        order = Order.objects.get(id=response.data["order_id"])
        self.assertEqual(order.buyer, self.buyer)
        self.assertEqual(order.items.count(), 1)

        # Price captured at purchase time
        item = order.items.first()
        self.assertEqual(item.price_at_purchase, Decimal("15000.00"))
        self.assertEqual(item.seller, self.seller)

        # Stock deducted
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)

        # Cart cleared
        self.assertEqual(CartItem.objects.filter(cart=self.cart).count(), 0)

    def test_checkout_empty_cart(self):
        self.cart_item.delete()
        response = self.client.post("/api/orders/checkout/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.data["detail"].lower())

    def test_checkout_insufficient_stock(self):
        self.cart_item.quantity = 10
        self.cart_item.save()

        response = self.client.post("/api/orders/checkout/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("insufficient", response.data["detail"].lower())

        # Stock unchanged
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_checkout_inactive_product(self):
        self.product.is_active = False
        self.product.save()

        response = self.client.post("/api/orders/checkout/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("unavailable", response.data["detail"].lower())

    def test_checkout_captures_price_at_purchase(self):
        """Price changes after checkout should not affect the order."""
        self.client.post("/api/orders/checkout/")

        self.product.price = Decimal("20000.00")
        self.product.save()

        order_item = OrderItem.objects.first()
        self.assertEqual(order_item.price_at_purchase, Decimal("15000.00"))

    def test_checkout_multi_seller_order(self):
        seller2 = User.objects.create_user(
            email="seller2@test.com", password="testpass123", role="SELLER"
        )
        product2 = Product.objects.create(
            seller=seller2,
            category=self.category,
            name="Test Laptop",
            price=Decimal("50000.00"),
            stock=10,
        )
        CartItem.objects.create(cart=self.cart, product=product2, quantity=1)

        response = self.client.post("/api/orders/checkout/")
        self.assertEqual(response.status_code, 201)

        order = Order.objects.get(id=response.data["order_id"])
        self.assertEqual(order.items.count(), 2)

        sellers = set(order.items.values_list("seller_id", flat=True))
        self.assertEqual(sellers, {self.seller.id, seller2.id})

    def test_seller_cannot_checkout(self):
        token = RefreshToken.for_user(self.seller)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token.access_token}"
        )
        response = self.client.post("/api/orders/checkout/")
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_cannot_checkout(self):
        self.client.credentials()
        response = self.client.post("/api/orders/checkout/")
        self.assertEqual(response.status_code, 401)


class SellerOrderIsolationTestCase(TestCase):
    """Tests that sellers can only see orders containing their own products."""

    def setUp(self):
        self.client = APIClient()

        self.buyer = User.objects.create_user(
            email="buyer@test.com", password="testpass123", role="BUYER"
        )
        self.seller1 = User.objects.create_user(
            email="seller1@test.com", password="testpass123", role="SELLER"
        )
        self.seller2 = User.objects.create_user(
            email="seller2@test.com", password="testpass123", role="SELLER"
        )

        category = Category.objects.create(name="Electronics", slug="electronics")

        product1 = Product.objects.create(
            seller=self.seller1,
            category=category,
            name="Phone",
            price=Decimal("15000.00"),
            stock=10,
        )
        product2 = Product.objects.create(
            seller=self.seller2,
            category=category,
            name="Laptop",
            price=Decimal("50000.00"),
            stock=10,
        )

        order = Order.objects.create(
            buyer=self.buyer, total_amount=Decimal("65000.00")
        )
        OrderItem.objects.create(
            order=order,
            product=product1,
            seller=self.seller1,
            quantity=1,
            price_at_purchase=Decimal("15000.00"),
        )
        OrderItem.objects.create(
            order=order,
            product=product2,
            seller=self.seller2,
            quantity=1,
            price_at_purchase=Decimal("50000.00"),
        )

    def test_seller_sees_only_own_items(self):
        token = RefreshToken.for_user(self.seller1)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token.access_token}"
        )
        response = self.client.get("/api/orders/seller/")
        self.assertEqual(response.status_code, 200)

        orders = response.data["results"]
        self.assertEqual(len(orders), 1)

        items = orders[0]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["product_name"], "Phone")

    def test_buyer_cannot_access_seller_orders(self):
        token = RefreshToken.for_user(self.buyer)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token.access_token}"
        )
        response = self.client.get("/api/orders/seller/")
        self.assertEqual(response.status_code, 403)
