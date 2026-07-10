from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from smartkart.pagination import UserPagination
from .models import User
from .permissions import IsAdmin
from .serializers import AdminUserListSerializer, CurrentUserSerializer


class CurrentUserView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = CurrentUserSerializer(request.user)
        return Response(serializer.data)


class AdminUserListView(APIView):
    """Read-only, platform-wide user listing for admins - the in-app
    equivalent of what Django admin already shows, so admins never have to
    leave the app's own UI to see who's on the platform."""

    permission_classes = [IsAdmin]

    def get(self, request):
        queryset = User.objects.all().order_by("-created_at")

        search = request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(email__icontains=search)

        role = request.query_params.get("role")
        if role in (User.BUYER, User.SELLER):
            queryset = queryset.filter(role=role)

        is_active = request.query_params.get("is_active")
        if is_active in ("true", "false"):
            queryset = queryset.filter(is_active=(is_active == "true"))

        paginator = UserPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = AdminUserListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
