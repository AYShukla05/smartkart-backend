from django.urls import path
from .views import AdminUserListView, CurrentUserView

urlpatterns = [
    path("me/", CurrentUserView.as_view()),
    path("admin/", AdminUserListView.as_view()),
]
