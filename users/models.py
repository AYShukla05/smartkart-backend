import re

from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.utils import timezone

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    BUYER = 'BUYER'
    SELLER = 'SELLER'

    ROLE_CHOICES = (
        (BUYER, 'Buyer'),
        (SELLER, 'Seller'),
    )

    email = models.EmailField(unique=True)
    # A friendly, public-safe identifier - unlike email, safe to show buyers
    # a seller's identity. Nullable because nothing prompts for it yet (no
    # registration field, no profile editor), so it's auto-derived from the
    # email on save() rather than required at creation time.
    username = models.CharField(max_length=150, unique=True, null=True, blank=True)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def save(self, *args, **kwargs):
        if not self.username:
            self.username = self._generate_unique_username()
        super().save(*args, **kwargs)

    def _generate_unique_username(self):
        base = re.sub(r'[^a-zA-Z0-9]', '', self.email.split('@')[0]).lower() or 'user'
        candidate = base
        suffix = 1
        while User.objects.filter(username=candidate).exclude(pk=self.pk).exists():
            suffix += 1
            candidate = f'{base}{suffix}'
        return candidate

    def __str__(self):
        return self.email
