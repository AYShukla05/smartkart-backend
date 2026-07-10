"""Creates the hand-curated category taxonomy. Trivial and cheap - get_or_create
per category is naturally idempotent, no bulk-insert needed at ~20 rows."""
from categories.models import Category
from seeding.constants import CATEGORIES


def create_categories(stdout=None):
    created_count = 0
    for slug, meta in CATEGORIES.items():
        _, created = Category.objects.get_or_create(
            slug=slug, defaults={"name": meta["name"], "is_active": True}
        )
        if created:
            created_count += 1

    if stdout:
        stdout.write(
            f"  Categories: {created_count} created, "
            f"{len(CATEGORIES) - created_count} already existed."
        )

    return {c.slug: c for c in Category.objects.filter(slug__in=CATEGORIES.keys())}
