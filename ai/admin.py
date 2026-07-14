from django.contrib import admin

from .models import ProductEmbedding


@admin.register(ProductEmbedding)
class ProductEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("id", "product", "model_id", "created_at", "updated_at")
    list_filter = ("model_id",)
    search_fields = ("product__name",)
