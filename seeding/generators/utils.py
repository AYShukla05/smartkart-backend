"""Small shared helpers used by more than one generator."""
import random
from contextlib import contextmanager

from django.utils import timezone


@contextmanager
def allow_manual_created_at(model):
    """
    Product.created_at and Order.created_at are `auto_now_add=True`, which
    force-overwrites to `timezone.now()` on every save - including inside
    bulk_create(). Without this, every seeded row would show the exact
    moment the script ran, which looks nothing like real historical data.
    Flips the field's `auto_now_add` off for the duration of the `with`
    block, then restores it - see SEEDING_PLAN.md for the full explanation.
    """
    field = model._meta.get_field("created_at")
    original = field.auto_now_add
    field.auto_now_add = False
    try:
        yield
    finally:
        field.auto_now_add = original


def random_historical_datetime(rng, months_back):
    """Uniform-random timestamp somewhere in the last `months_back` months."""
    now = timezone.now()
    days_back = rng.randint(0, months_back * 30)
    seconds_in_day = rng.randint(0, 86_399)
    return now - timezone.timedelta(days=days_back, seconds=seconds_in_day)


def weighted_recent_datetime(rng, months_back):
    """
    Like random_historical_datetime, but with mild recency weighting - more
    recent months are somewhat more likely than older ones, matching how
    order volume on a real, growing marketplace tends to look, rather than
    perfectly flat across the whole window.
    """
    now = timezone.now()
    # Linearly increasing weights: month 0 (this month) heaviest, oldest month lightest.
    weights = [months_back - m for m in range(months_back)]
    month_offset = rng.choices(range(months_back), weights=weights, k=1)[0]
    days_back = rng.randint(month_offset * 30, (month_offset + 1) * 30 - 1)
    seconds_in_day = rng.randint(0, 86_399)
    return now - timezone.timedelta(days=days_back, seconds=seconds_in_day)
