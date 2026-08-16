from django.db import models
from currency.services import CURRENCY_CHOICES
from users.models import User
from products.models import Product


class Order(models.Model):
    class Status(models.TextChoices):
        PLACED = "PLACED", "Placed"
        CANCELLED = "CANCELLED", "Cancelled"

    buyer = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="orders",
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLACED
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Order #{self.id} - {self.buyer.email}"


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT
    )
    seller = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="sold_items"
    )
    quantity = models.PositiveIntegerField()
    price_at_purchase = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )
    # Seller's currency at time of purchase, frozen alongside price_at_purchase
    # so order history stays accurate even if the seller's currency changes later.
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="INR")

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"
