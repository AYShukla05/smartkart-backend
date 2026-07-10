from django.urls import path
from .views import (
    AdminProductListView,
    SellerProductListCreateView,
    SellerProductDetailView,
    PublicProductListView,
    PublicProductDetailView,
    SellerProductImageCreateView,
    SellerProductImagePresignView,
    SellerProductImageThumbnailView,
    SellerProductImageDeleteView,
)

urlpatterns = [
    # Admin
    path("admin/", AdminProductListView.as_view()),

    # Public
    path("", PublicProductListView.as_view()),
    path("<int:pk>/", PublicProductDetailView.as_view()),

    # Seller
    path("my/", SellerProductListCreateView.as_view()),
    path("my/<int:pk>/", SellerProductDetailView.as_view()),
    path("my/<int:product_id>/images/", SellerProductImageCreateView.as_view()),
    path("my/<int:product_id>/images/presign/", SellerProductImagePresignView.as_view()),
    path("my/<int:product_id>/images/<int:image_id>/", SellerProductImageDeleteView.as_view()),
    path("my/<int:product_id>/images/<int:image_id>/thumbnail/", SellerProductImageThumbnailView.as_view()),
]
