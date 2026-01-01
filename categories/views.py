from rest_framework import generics
from rest_framework.permissions import AllowAny

from users.permissions import IsAdmin
from .models import Category
from .serializers import CategorySerializer


class CategoryListCreateView(generics.ListCreateAPIView):
    """
    GET  -> Public category listing (no authentication)
    POST -> Admin-only category creation (authentication required)
    """
    serializer_class = CategorySerializer
    queryset = Category.objects.all()

    def get_authenticators(self):
        # Disable authentication ONLY for public GET
        if self.request.method == "GET":
            return []
        return super().get_authenticators()

    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAdmin()]

    def get_queryset(self):
        # Public users only see active categories
        if self.request.method == "GET":
            return Category.objects.filter(is_active=True)
        return Category.objects.all()


class CategoryDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    PUT / PATCH / DELETE -> Admin-only
    """
    serializer_class = CategorySerializer
    queryset = Category.objects.all()
    permission_classes = [IsAdmin]
