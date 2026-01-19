from django.db import transaction
from django.db.models import Prefetch

from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from cart.models import CartItem
from products.models import Product
from users.permissions import IsBuyer, IsSeller

from .models import Order, OrderItem
from .serializers import OrderReadSerializer, SellerOrderReadSerializer


class CheckoutView(APIView):
    permission_classes = [IsBuyer]

    def post(self, request):
        cart_items = CartItem.objects.filter(cart__buyer=request.user)

        if not cart_items.exists():
            return Response(
                {"detail": "Cart is empty."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Lock products to prevent race conditions
            product_ids = cart_items.values_list("product_id", flat=True)
            products = (
                Product.objects
                .select_for_update()
                .filter(id__in=product_ids, is_active=True)
            )
            product_map = {product.id: product for product in products}

            total_amount = 0
            order_items_data = []

            for item in cart_items:
                product = product_map.get(item.product_id)

                if not product:
                    return Response(
                        {"detail": f"Product {item.product_id} is unavailable."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                if product.stock < item.quantity:
                    return Response(
                        {
                            "detail": f"Insufficient stock for {product.name}."
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                total_amount += product.price * item.quantity

                order_items_data.append({
                    "product": product,
                    "seller": product.seller,
                    "quantity": item.quantity,
                    "price_at_purchase": product.price,
                })

            # Create Order
            order = Order.objects.create(
                buyer=request.user,
                total_amount=total_amount,
            )

            # Create OrderItems + deduct stock
            OrderItem.objects.bulk_create([
                OrderItem(
                    order=order,
                    product=data["product"],
                    seller=data["seller"],
                    quantity=data["quantity"],
                    price_at_purchase=data["price_at_purchase"],
                )
                for data in order_items_data
            ])
            for data in order_items_data:
                data["product"].stock -= data["quantity"]

            Product.objects.bulk_update(
                [data["product"] for data in order_items_data], ["stock"]
            )

            # Clear cart
            cart_items.delete()

        return Response(
            {"order_id": order.id, "total_amount": total_amount},
            status=status.HTTP_201_CREATED,
        )

class BuyerOrderListView(APIView):
    permission_classes = [IsBuyer]

    def get(self, request):
        orders = (
            Order.objects
            .filter(buyer=request.user)
            .prefetch_related("items", "items__product")
            .order_by("-created_at")
        )

        serializer = OrderReadSerializer(orders, many=True)
        return Response(serializer.data)
    
class BuyerOrderDetailView(APIView):
    permission_classes = [IsBuyer]

    def get(self, request, pk):
        order = get_object_or_404(
            Order.objects.filter(buyer=request.user)
            .prefetch_related("items", "items__product"),
            pk=pk,
        )
        return Response(OrderReadSerializer(order).data)


class SellerOrderListView(APIView):
    permission_classes = [IsSeller]

    def get(self, request):
        orders = (
            Order.objects
            .filter(items__seller=request.user)
            .distinct()
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=OrderItem.objects.filter(
                        seller=request.user
                    ).select_related("product"),
                )
            )
            .order_by("-created_at")
        )

        serializer = SellerOrderReadSerializer(
            orders,
            many=True,
            context={"seller": request.user}
        )
        return Response(serializer.data)
