from django.db import models
from users.models import User
from categories.models import Category


class Product(models.Model):
    """
    Ownership rules:
    - A product belongs to exactly one seller (User with role=SELLER)
    - Only the owning seller can update/delete the product
    - Public users can only view is_active products
    """
    seller = models.ForeignKey(User, on_delete=models.CASCADE, related_name="products")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class ProductImage(models.Model):
    """
    Image rules:
    - Stores only image URL (uploaded to S3 by frontend)
    - Backend does not handle image files
    - Image belongs to exactly one product
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image_url = models.URLField()
    created_at = models.DateTimeField(auto_now_add=True)
