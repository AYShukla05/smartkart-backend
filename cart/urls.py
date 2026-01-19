from django.urls import path
from .views import (
    CartDetailView,
    CartItemAddView,
    CartItemDetailView,
)

urlpatterns = [
    path("", CartDetailView.as_view()),
    path("items/", CartItemAddView.as_view()),
    path("items/<int:item_id>/", CartItemDetailView.as_view()),
]
