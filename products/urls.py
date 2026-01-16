from django.urls import path
from .views import (
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
