from django.urls import path
from .views import (
    CheckoutView,
    BuyerOrderListView,
    BuyerOrderDetailView,
    SellerOrderListView,
)

urlpatterns = [
    path("checkout/", CheckoutView.as_view()),
    path("", BuyerOrderListView.as_view()),
    path("<int:pk>/", BuyerOrderDetailView.as_view()),
    path("seller/", SellerOrderListView.as_view()),
]
