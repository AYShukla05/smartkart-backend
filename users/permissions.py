from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        print("User:", request.user)
        print("Is Authenticated:", request.user.is_authenticated)
        print("Is Staff:", request.user.is_staff)              
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_staff
        )


class IsSeller(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == "SELLER"
        )


class IsBuyer(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == "BUYER"
        )
