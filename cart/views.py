from django.db.models import F
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404

from .models import Cart, CartItem
from .serializers import (
    CartSerializer,
    CartItemCreateUpdateSerializer,
    CartItemReadSerializer,
)
from products.models import Product
from users.permissions import IsBuyer


def get_or_create_cart(user):
    cart, _ = Cart.objects.get_or_create(buyer=user)
    return cart


class CartDetailView(APIView):
    permission_classes = [IsBuyer]

    def get(self, request):
        cart = get_or_create_cart(request.user)
        serializer = CartSerializer(cart)
        return Response(serializer.data)

class CartItemAddView(APIView):
    permission_classes = [IsBuyer]

    def post(self, request):
        serializer = CartItemCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        cart = get_or_create_cart(request.user)
        product = get_object_or_404(Product, id=serializer.validated_data["product_id"])
        quantity = serializer.validated_data["quantity"]

        cart_item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={"quantity": quantity},
        )

        if not created:
            cart_item.quantity = F("quantity") + quantity
            cart_item.save(update_fields=["quantity"])
            cart_item.refresh_from_db()

        response_serializer = CartItemReadSerializer(cart_item)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

class CartItemDetailView(APIView):
    permission_classes = [IsBuyer]

    def patch(self, request, item_id):
        cart_item = get_object_or_404(
            CartItem,
            id=item_id,
            cart__buyer=request.user
        )

        serializer = CartItemCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        cart_item.quantity = serializer.validated_data["quantity"]
        cart_item.save(update_fields=["quantity"])

        response_serializer = CartItemReadSerializer(cart_item)
        return Response(response_serializer.data)

    def delete(self, request, item_id):
        cart_item = get_object_or_404(
            CartItem,
            id=item_id,
            cart__buyer=request.user
        )
        cart_item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
