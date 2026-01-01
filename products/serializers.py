from rest_framework import serializers
from .models import Product


class ProductCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "description",
            "price",
            "stock",
            "category",
            "is_active",
        ]

    def validate_price(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Price must be greater than zero."
            )
        return value
    
    def validate_stock(self, value):
        if value < 0:
            raise serializers.ValidationError(
                "Stock cannot be negative."
            )
        return value



class ProductListSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    seller_id = serializers.IntegerField(source="seller.id", read_only=True)
    seller_username = serializers.CharField(source="seller.username", read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "price",
            "stock",
            "is_active",
            "category",
            "category_name",
            "seller_id",
            "seller_username",
        ]


class ProductDetailSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    seller_id = serializers.IntegerField(source="seller.id", read_only=True)
    seller_username = serializers.CharField(source="seller.username", read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "description",
            "price",
            "stock",
            "category",
            "category_name",
            "is_active",
            "seller_id",
            "seller_username",
            "created_at",
            "updated_at",
        ]
