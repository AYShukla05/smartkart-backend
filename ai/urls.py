from django.urls import path
from .views import GenerateDescriptionView

urlpatterns = [
    path("generate-description/", GenerateDescriptionView.as_view()),
]
