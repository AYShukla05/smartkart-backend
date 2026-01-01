from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404

from .models import Product
from .serializers import (
    ProductCreateUpdateSerializer,
    ProductListSerializer,
    ProductDetailSerializer,
)
from users.permissions import IsSeller
from rest_framework.permissions import AllowAny


class SellerProductListCreateView(APIView):
    permission_classes = [IsSeller]

    def get(self, request):
        products = Product.objects.filter(seller=request.user).order_by("-created_at")
        serializer = ProductListSerializer(products, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = ProductCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(seller=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SellerProductDetailView(APIView):
    permission_classes = [IsSeller]

    def get_object(self, request, pk):
        return get_object_or_404(Product, pk=pk, seller=request.user)

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
        products = Product.objects.filter(is_active=True).order_by("-created_at")
        serializer = ProductListSerializer(products, many=True)
        return Response(serializer.data)


class PublicProductDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk):
        product = get_object_or_404(Product, pk=pk, is_active=True)
        serializer = ProductDetailSerializer(product)
        return Response(serializer.data)
