from django.urls import path
from .views import (
    SellerProductListCreateView,
    SellerProductDetailView,
    PublicProductListView,
    PublicProductDetailView,
)

urlpatterns = [
    # Public
    path("", PublicProductListView.as_view()),
    path("<int:pk>/", PublicProductDetailView.as_view()),

    # Seller
    path("my/", SellerProductListCreateView.as_view()),
    path("my/<int:pk>/", SellerProductDetailView.as_view()),
]
