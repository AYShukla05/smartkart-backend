from django.db import models
from users.models import User
from products.models import Product


class Cart(models.Model):
    """
    One active cart per buyer.
    Created lazily when buyer adds first item.
    """
    buyer = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="cart"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Cart({self.buyer.email})"


class CartItem(models.Model):
    """
    Represents buyer intent.
    Deleted after order placement.
    """
    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name="items"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT
    )
    quantity = models.PositiveIntegerField()

    class Meta:
        unique_together = ("cart", "product")

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"
