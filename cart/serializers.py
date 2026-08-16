from rest_framework import serializers
from .models import CartItem
from products.models import Product


class CartItemCreateUpdateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)

    def validate_product_id(self, value):
        if not Product.objects.filter(id=value, is_active=True).exists():
            raise serializers.ValidationError("Invalid or inactive product.")
        return value

class CartItemReadSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    price = serializers.DecimalField(
        source="product.price",
        max_digits=10,
        decimal_places=2,
        read_only=True
    )
    currency = serializers.CharField(source="product.seller.currency", read_only=True)
    thumbnail = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            "id",
            "product_id",
            "product_name",
            "price",
            "currency",
            "quantity",
            "thumbnail",
        ]

    def get_thumbnail(self, obj):
        images = obj.product.images.all()
        thumb = next((i for i in images if i.is_thumbnail), None)
        if thumb is None and images:
            thumb = images[0]
        return thumb.image_url if thumb else None

class CartSerializer(serializers.Serializer):
    items = serializers.SerializerMethodField()

    def get_items(self, obj):
        queryset = obj.items.select_related(
            "product", "product__seller"
        ).prefetch_related("product__images")
        return CartItemReadSerializer(queryset, many=True).data
