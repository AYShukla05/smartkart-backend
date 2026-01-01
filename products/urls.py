from django.urls import path
from .views import (
    SellerProductListCreateView,
    SellerProductDetailView,
    PublicProductListView,
)

urlpatterns = [
    # Public
    path("", PublicProductListView.as_view()),

    # Seller
    path("my/", SellerProductListCreateView.as_view()),
    path("my/<int:pk>/", SellerProductDetailView.as_view()),
]
