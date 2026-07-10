"""
Bulk buyer/seller generation, the known demo accounts, and the reset/cleanup
logic for seed-scoped users.
"""
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone

from cart.models import CartItem
from orders.models import Order, OrderItem
from seeding.constants import (
    DEMO_BUYER_EMAIL,
    DEMO_PASSWORD,
    DEMO_SELLER_EMAILS,
    SEED_EMAIL_DOMAIN,
    SEED_PASSWORD,
)
from users.models import User


def _seed_email(prefix, index):
    return f"{prefix}{index:05d}@{SEED_EMAIL_DOMAIN}"


def top_up_bulk_users(role, prefix, target_count, stdout=None):
    """
    Idempotent: only creates the delta needed to reach target_count for this
    role, continuing the deterministic email numbering from wherever it left
    off - a re-run never duplicates or renumbers existing seed users.
    """
    existing = User.objects.filter(
        email__endswith=f"@{SEED_EMAIL_DOMAIN}", role=role
    ).count()

    if existing >= target_count:
        if stdout:
            stdout.write(f"  {role.title()}s: already have {existing}, target is {target_count} - skipping.")
        return existing

    to_create = target_count - existing
    password_hash = make_password(SEED_PASSWORD)  # hashed once, reused - see SEEDING_PLAN.md
    now = timezone.now()

    batch = [
        User(
            email=_seed_email(prefix, i),
            role=role,
            password=password_hash,
            is_active=True,
            created_at=now,  # User.created_at is default=timezone.now (not auto_now_add) - safe to set directly
        )
        for i in range(existing + 1, existing + to_create + 1)
    ]
    User.objects.bulk_create(batch, batch_size=500)

    if stdout:
        stdout.write(f"  {role.title()}s: created {to_create} new ({existing} -> {existing + to_create}).")
    return existing + to_create


def create_demo_accounts(stdout=None):
    """
    Creates the known demo buyer + 2 demo sellers via the normal
    get_or_create/set_password path - only 3 rows, so the bulk-insert
    performance rationale doesn't apply, and this is simpler to get right.
    Idempotent: an existing demo account's password is left untouched on
    re-run (only set at creation time).
    """
    demo_buyer, created = User.objects.get_or_create(
        email=DEMO_BUYER_EMAIL, defaults={"role": User.BUYER}
    )
    if created:
        demo_buyer.set_password(DEMO_PASSWORD)
        demo_buyer.save(update_fields=["password"])
        if stdout:
            stdout.write(f"  Created demo buyer: {DEMO_BUYER_EMAIL}")
    elif stdout:
        stdout.write(f"  Demo buyer already exists: {DEMO_BUYER_EMAIL}")

    demo_sellers = []
    for email in DEMO_SELLER_EMAILS:
        seller, created = User.objects.get_or_create(
            email=email, defaults={"role": User.SELLER}
        )
        if created:
            seller.set_password(DEMO_PASSWORD)
            seller.save(update_fields=["password"])
            if stdout:
                stdout.write(f"  Created demo seller: {email}")
        elif stdout:
            stdout.write(f"  Demo seller already exists: {email}")
        demo_sellers.append(seller)

    return demo_buyer, demo_sellers


def delete_seed_data(stdout=None):
    """
    Deletes every @seed.smartkart.dev user and, via cascade, their carts and
    products. Never touches real users or the known demo accounts (different
    email domain entirely).

    The deletion order matters and isn't arbitrary: `Order.buyer`,
    `OrderItem.seller`, and `OrderItem.product` are all `on_delete=PROTECT`
    (by design - order history must survive even if the account behind it is
    later removed). Deleting seed Users directly, in the wrong order, raises
    ProtectedError. So: clear out any cart/order line items that reference a
    seed seller's products or a seed buyer's orders *first*, which releases
    those protections, then delete the users - at which point their Carts
    and Products cascade away cleanly.

    Known, accepted limitation: this doesn't attempt to untangle a real
    user's cart or order referencing a seed seller's product - an edge case
    that shouldn't occur in practice, since the seed email domain exists
    specifically to keep this data from ever organically interacting with
    real accounts.
    """
    with transaction.atomic():
        CartItem.objects.filter(
            product__seller__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
        ).delete()
        OrderItem.objects.filter(
            seller__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
        ).delete()
        orders_deleted, _ = Order.objects.filter(
            buyer__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
        ).delete()
        users_deleted, _ = User.objects.filter(
            email__endswith=f"@{SEED_EMAIL_DOMAIN}"
        ).delete()

    if stdout:
        stdout.write(
            f"  Deleted {orders_deleted} orders and {users_deleted} users "
            f"(products/cart items cascaded automatically)."
        )
    return users_deleted
