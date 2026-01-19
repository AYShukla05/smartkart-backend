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
        seller = self.context.get("seller")
        items = order.items.filter(seller=seller) if seller else order.items.all()
        return OrderItemReadSerializer(items, many=True).data
