from rest_framework import serializers
from .models import Product, ProductImage


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
    thumbnail = serializers.SerializerMethodField()

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
            "thumbnail",
        ]

    def get_thumbnail(self, obj):
        images = obj.images.all()
        thumb = next((i for i in images if i.is_thumbnail), None)
        if thumb is None and images:
            thumb = images[0]
        return thumb.image_url if thumb else None


class AdminProductListSerializer(ProductListSerializer):
    """Same as ProductListSerializer, plus the seller's email - safe to
    expose here since this endpoint is IsAdmin-gated, unlike the public
    listing this class extends, which must never leak seller emails to
    anonymous visitors."""

    seller_email = serializers.CharField(source="seller.email", read_only=True)

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + ["seller_email"]
        # get_thumbnail is inherited from ProductListSerializer - no need to redefine it.


class ProductImageCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ["id", "image_url", "is_thumbnail"]
        read_only_fields = ["is_thumbnail"]


class ProductDetailSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    seller_id = serializers.IntegerField(source="seller.id", read_only=True)
    seller_username = serializers.CharField(source="seller.username", read_only=True)
    images = ProductImageCreateSerializer(many=True, read_only=True)

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
            "images",
            "created_at",
            "updated_at",
        ]
