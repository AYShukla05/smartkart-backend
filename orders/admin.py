from django.contrib import admin
from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = (
        "product",
        "seller",
        "quantity",
        "price_at_purchase",
    )


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "buyer",
        "status",
        "total_amount",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("buyer__email",)
    readonly_fields = (
        "buyer",
        "total_amount",
        "created_at",
    )
    inlines = [OrderItemInline]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = (
        "order",
        "product",
        "seller",
        "quantity",
        "price_at_purchase",
    )
    list_filter = ("seller",)
    search_fields = ("order__id", "product__name", "seller__email")
    readonly_fields = (
        "order",
        "product",
        "seller",
        "quantity",
        "price_at_purchase",
    )
