from django.apps import AppConfig


class SeedingConfig(AppConfig):
    """
    Operational tooling for populating the database with realistic fake
    data (users, products, orders) and a reusable stock-photo pool.

    Never imported by any request-serving code path - only invoked
    directly via `manage.py seed_data`.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "seeding"
