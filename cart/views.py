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

        existing_item = CartItem.objects.filter(cart=cart, product=product).first()
        existing_qty = existing_item.quantity if existing_item else 0

        if existing_qty + quantity > product.stock:
            available = product.stock - existing_qty
            detail = f"Only {product.stock} units available."
            if existing_qty > 0:
                detail = f"Only {available} more can be added. You already have {existing_qty} in cart."
            return Response(
                {"detail": detail},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if existing_item:
            existing_item.quantity = F("quantity") + quantity
            existing_item.save(update_fields=["quantity"])
            existing_item.refresh_from_db()
            cart_item = existing_item
        else:
            cart_item = CartItem.objects.create(
                cart=cart,
                product=product,
                quantity=quantity,
            )

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

        new_quantity = serializer.validated_data["quantity"]
        if new_quantity > cart_item.product.stock:
            return Response(
                {"detail": f"Only {cart_item.product.stock} units available."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cart_item.quantity = new_quantity
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
