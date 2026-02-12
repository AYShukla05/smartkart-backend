from django.urls import path
from .views import (
    CheckoutView,
    BuyerOrderListView,
    BuyerOrderDetailView,
    SellerOrderListView,
    SellerStatsView,
)

urlpatterns = [
    path("checkout/", CheckoutView.as_view()),
    path("", BuyerOrderListView.as_view()),
    path("<int:pk>/", BuyerOrderDetailView.as_view()),
    path("seller/stats/", SellerStatsView.as_view()),
    path("seller/", SellerOrderListView.as_view()),
]
