from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404

from .models import Product
from .serializers import (
    ProductCreateUpdateSerializer,
    ProductListSerializer,
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
        if serializer.is_valid():
            serializer.save(seller=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SellerProductDetailView(APIView):
    permission_classes = [IsSeller]

    def get_object(self, request, pk):
        return get_object_or_404(Product, pk=pk, seller=request.user)

    def patch(self, request, pk):
        product = self.get_object(request, pk)
        serializer = ProductCreateUpdateSerializer(
            product, data=request.data, partial=True
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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
