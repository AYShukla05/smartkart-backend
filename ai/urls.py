from django.urls import path
from .views import GenerateDescriptionView, SellerAssistantView

urlpatterns = [
    path("generate-description/", GenerateDescriptionView.as_view()),
    path("seller-assistant/", SellerAssistantView.as_view()),
]
