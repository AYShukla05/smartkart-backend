from django.urls import path
from .views import (
    BuyerOrderAssistantView,
    ConfirmSellerActionView,
    GenerateDescriptionView,
    SellerAssistantView,
)

urlpatterns = [
    path("generate-description/", GenerateDescriptionView.as_view()),
    path("seller-assistant/", SellerAssistantView.as_view()),
    path("seller-assistant/confirm-action/", ConfirmSellerActionView.as_view()),
    path("order-assistant/", BuyerOrderAssistantView.as_view()),
]
