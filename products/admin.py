from django.contrib import admin
from .models import Product, ProductImage


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "seller",
        "category",
        "price",
        "stock",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "category")
    search_fields = ("name",)
    ordering = ("-created_at",)


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ("id", "product", "image_url", "created_at")
