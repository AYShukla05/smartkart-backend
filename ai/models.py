from django.db import models
from pgvector.django import VectorField

from products.models import Product


class ProductEmbedding(models.Model):
    """
    Design rules:
    - Separate model, not a field on Product, so re-embedding never touches
      Product's migration history
    - 512 dimensions instead of Voyage's default, this catalog doesn't need
      the extra precision, keeps storage and query cost down
    - model_id recorded on every row, so switching embedding models later is
      detectable instead of silently mixing incompatible vectors
    """
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name="embedding",
    )
    embedding = VectorField(dimensions=512)
    model_id = models.CharField(max_length=100, default="voyage-4-lite-512")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
