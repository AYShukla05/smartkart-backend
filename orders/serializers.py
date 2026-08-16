from rest_framework import serializers
from .models import Order, OrderItem

class OrderItemReadSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = OrderItem
        fields = (
            "id",
            "product_name",
            "quantity",
            "price_at_purchase",
            "currency",
        )


class OrderReadSerializer(serializers.ModelSerializer):
    items = OrderItemReadSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = (
            "id",
            "status",
            "total_amount",
            "created_at",
            "items",
        )
        
class SellerOrderReadSerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            "id",
            "status",
            "created_at",
            "items",
        )

    def get_items(self, order):
        # When a seller is in context, the view already scopes the "items"
        # Prefetch queryset to that seller - call .all() (not .filter()) so
        # this hits the prefetch cache instead of issuing a fresh query.
        items = order.items.all()
        return OrderItemReadSerializer(items, many=True).data


class AdminOrderItemReadSerializer(OrderItemReadSerializer):
    seller_email = serializers.CharField(source="seller.email", read_only=True)

    class Meta(OrderItemReadSerializer.Meta):
        fields = OrderItemReadSerializer.Meta.fields + ("seller_email",)


class AdminOrderReadSerializer(serializers.ModelSerializer):
    """Platform-wide order view for admins: buyer identity plus every line
    item regardless of seller (OrderReadSerializer is buyer-scoped to "my
    orders", SellerOrderReadSerializer is seller-scoped to "my items" -
    this is neither, it's the full picture, gated by IsAdmin instead)."""

    buyer_email = serializers.CharField(source="buyer.email", read_only=True)
    items = AdminOrderItemReadSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = (
            "id",
            "buyer_email",
            "status",
            "total_amount",
            "created_at",
            "items",
        )
