from django.urls import path
from .views import CurrentUserView

urlpatterns = [
    path("me/", CurrentUserView.as_view()),
]
