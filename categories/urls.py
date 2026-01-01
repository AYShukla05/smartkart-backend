from django.urls import path
from .views import CategoryListCreateView, CategoryDetailView

urlpatterns = [
    path("", CategoryListCreateView.as_view()),       # GET, POST
    path("<int:pk>/", CategoryDetailView.as_view()),  # PUT, PATCH, DELETE
]
