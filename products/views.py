import logging
import uuid

from botocore.exceptions import ClientError
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404

from django.db.models import Q
from .models import Product, ProductImage
from .s3_utils import (
    generate_presigned_put_url,
    get_public_url,
    delete_s3_object,
    get_s3_key_from_url,
    S3UploadError,
)
from smartkart.pagination import ProductPagination
from .serializers import (
    ProductCreateUpdateSerializer,
    ProductListSerializer,
    ProductDetailSerializer,
    ProductImageCreateSerializer
)
from users.permissions import IsSeller
from rest_framework.permissions import AllowAny

logger = logging.getLogger(__name__)


class SellerProductListCreateView(APIView):
    permission_classes = [IsSeller]

    def get(self, request):
        queryset = Product.objects.filter(
            seller=request.user
        ).select_related("category").prefetch_related("images")

        search = request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )

        allowed_orderings = {
            "price", "-price", "name", "-name", "stock", "-stock", "created_at", "-created_at"
        }
        ordering = request.query_params.get("ordering", "-created_at")
        if ordering not in allowed_orderings:
            ordering = "-created_at"
        queryset = queryset.order_by(ordering)

        paginator = ProductPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = ProductListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = ProductCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(seller=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SellerProductDetailView(APIView):
    permission_classes = [IsSeller]

    def get_object(self, request, pk):
        return get_object_or_404(
            Product.objects.select_related("category").prefetch_related("images"),
            pk=pk,
            seller=request.user,
        )

    def get(self, request, pk):
        product = self.get_object(request, pk)
        serializer = ProductDetailSerializer(product)
        return Response(serializer.data)

    def patch(self, request, pk):
        product = self.get_object(request, pk)
        serializer = ProductCreateUpdateSerializer(
            product, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        product = self.get_object(request, pk)
        product.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PublicProductListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        queryset = Product.objects.filter(
            is_active=True
        ).select_related("category").prefetch_related("images")

        search = request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )

        category = request.query_params.get("category")
        if category:
            queryset = queryset.filter(category_id=category)

        allowed_orderings = {
            "price", "-price", "name", "-name", "created_at", "-created_at"
        }
        ordering = request.query_params.get("ordering", "-created_at")
        if ordering not in allowed_orderings:
            ordering = "-created_at"
        queryset = queryset.order_by(ordering)

        paginator = ProductPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = ProductListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class PublicProductDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk):
        product = get_object_or_404(
            Product.objects.select_related("category").prefetch_related("images"),
            pk=pk,
            is_active=True,
        )
        serializer = ProductDetailSerializer(product)
        return Response(serializer.data)


class SellerProductImageCreateView(APIView):
    permission_classes = [IsSeller]

    def post(self, request, product_id):
        product = get_object_or_404(
            Product,
            id=product_id,
            seller=request.user
        )

        serializer = ProductImageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        image_url = serializer.validated_data["image_url"]
        key = get_s3_key_from_url(image_url)
        if not key or not key.startswith(f"products/{product.id}/"):
            return Response(
                {"image_url": ["This image was not uploaded for this product."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_first = not product.images.exists()
        product_image = ProductImage.objects.create(
            product=product,
            image_url=image_url,
            is_thumbnail=is_first,
        )

        response_serializer = ProductImageCreateSerializer(product_image)
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED
        )


class SellerProductImagePresignView(APIView):
    permission_classes = [IsSeller]

    def post(self, request, product_id):
        get_object_or_404(Product, id=product_id, seller=request.user)

        file_name = request.data.get("file_name")
        if not file_name:
            return Response(
                {"file_name": ["This field is required."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        allowed_extensions = (".jpg", ".jpeg", ".png", ".webp")
        if not file_name.lower().endswith(allowed_extensions):
            return Response(
                {"file_name": ["Unsupported file type. Allowed types: jpg, jpeg, png, webp."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        key = f"products/{product_id}/{uuid.uuid4().hex}.webp"
        try:
            upload_url = generate_presigned_put_url(key)
        except S3UploadError:
            return Response(
                {"detail": "Image upload service unavailable."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        file_url = get_public_url(key)

        return Response({"upload_url": upload_url, "file_url": file_url})


class SellerProductImageThumbnailView(APIView):
    permission_classes = [IsSeller]

    def patch(self, request, product_id, image_id):
        image = get_object_or_404(
            ProductImage,
            id=image_id,
            product_id=product_id,
            product__seller=request.user,
        )

        with transaction.atomic():
            ProductImage.objects.filter(product_id=product_id).update(is_thumbnail=False)
            image.is_thumbnail = True
            image.save(update_fields=["is_thumbnail"])

        return Response({"status": "ok"})


class SellerProductImageDeleteView(APIView):
    permission_classes = [IsSeller]

    def delete(self, request, product_id, image_id):
        image = get_object_or_404(
            ProductImage,
            id=image_id,
            product_id=product_id,
            product__seller=request.user,
        )

        was_thumbnail = image.is_thumbnail
        product_id_ref = image.product_id

        try:
            delete_s3_object(image.image_url)
        except ClientError:
            logger.warning("Failed to delete S3 object: %s", image.image_url, exc_info=True)

        image.delete()

        if was_thumbnail:
            next_image = ProductImage.objects.filter(product_id=product_id_ref).first()
            if next_image:
                next_image.is_thumbnail = True
                next_image.save(update_fields=["is_thumbnail"])

        return Response(status=status.HTTP_204_NO_CONTENT)
