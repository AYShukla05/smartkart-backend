"""
Bulk order generation. The key trick, called out in SEEDING_PLAN.md: products
for each order are picked from the *entire* active catalog, not scoped to one
seller - that alone produces realistic multi-seller orders with zero special
casing, exactly matching how real checkout already works (OrderItem.seller
is just `product.seller` per line item).
"""
import random
from decimal import Decimal

from orders.models import Order, OrderItem
from products.models import Product
from seeding.constants import (
    ORDER_ITEMS_MAX,
    ORDER_ITEMS_MIN,
    ORDER_MONTHS_BACK,
    ORDER_STATUS_WEIGHTS,
)
from seeding.generators.distributions import pareto_weighted_split
from seeding.generators.utils import allow_manual_created_at, weighted_recent_datetime


def _load_active_product_pool():
    """One query, loaded into memory - this avoids re-querying per order
    pick across what can be tens of thousands of item selections."""
    return list(Product.objects.filter(is_active=True).values("id", "seller_id", "price", "stock"))


def _random_status(rng):
    statuses = list(ORDER_STATUS_WEIGHTS.keys())
    weights = list(ORDER_STATUS_WEIGHTS.values())
    return rng.choices(statuses, weights=weights, k=1)[0]


def _decrement_stock(quantity_by_product_id, stdout=None):
    """
    Mirrors real CheckoutView semantics: stock is decremented for every
    generated order regardless of eventual status, since there's no
    restock-on-cancel logic anywhere in the app (confirmed) - seeded data
    should look like the real thing, not invent behavior the app doesn't have.
    Clamped at 0 rather than allowed to go negative.
    """
    if not quantity_by_product_id:
        return

    products = list(Product.objects.filter(id__in=quantity_by_product_id.keys()).only("id", "stock"))
    for product in products:
        product.stock = max(0, product.stock - quantity_by_product_id[product.id])
    Product.objects.bulk_update(products, ["stock"], batch_size=500)

    if stdout:
        stdout.write(f"  Stock decremented for {len(products)} products based on simulated orders.")


def _build_orders(buyers_with_counts, product_pool, rng, months_back):
    """
    Shared core: given a list of (buyer, order_count) pairs and a product
    pool to draw from, builds Order + OrderItem rows and a stock-decrement
    map. Returns (orders_created_count, quantity_by_product_id).
    """
    orders_batch = []
    order_line_plans = []  # parallel list: chosen items for each order, before we have real PKs

    for buyer, count in buyers_with_counts:
        for _ in range(count):
            n_items = rng.randint(ORDER_ITEMS_MIN, min(ORDER_ITEMS_MAX, len(product_pool)))
            # Over-sample then dedupe by product id: some callers (the demo
            # buyer's pool, biased toward demo-seller products) intentionally
            # repeat entries for weighting, and rng.sample() dedupes by list
            # position, not content - without this, the same product could
            # end up as two separate line items in one order, which real
            # checkout could never produce (CartItem is already unique per product).
            candidates = rng.sample(product_pool, min(n_items * 2, len(product_pool)))
            seen_ids = set()
            chosen = []
            for candidate in candidates:
                if candidate["id"] not in seen_ids:
                    seen_ids.add(candidate["id"])
                    chosen.append(candidate)
                if len(chosen) == n_items:
                    break
            lines = [(p, rng.randint(1, 3)) for p in chosen]
            total_amount = sum(Decimal(p["price"]) * qty for p, qty in lines)

            orders_batch.append(Order(
                buyer=buyer,
                status=_random_status(rng),
                total_amount=total_amount,
                created_at=weighted_recent_datetime(rng, months_back),
            ))
            order_line_plans.append(lines)

    if not orders_batch:
        return 0, {}

    with allow_manual_created_at(Order):
        created_orders = Order.objects.bulk_create(orders_batch, batch_size=500)

    items_batch = []
    quantity_by_product_id = {}
    for order, lines in zip(created_orders, order_line_plans):
        for product, qty in lines:
            items_batch.append(OrderItem(
                order=order,
                product_id=product["id"],
                seller_id=product["seller_id"],
                quantity=qty,
                price_at_purchase=product["price"],
            ))
            quantity_by_product_id[product["id"]] = quantity_by_product_id.get(product["id"], 0) + qty

    OrderItem.objects.bulk_create(items_batch, batch_size=500)
    return len(created_orders), quantity_by_product_id


def top_up_orders(buyers, target_count, stdout=None):
    """
    Tops up bulk seed orders to target_count. Only a subset of buyers
    (BUYER_ACTIVE_RATIO) get any orders at all, and among those, order
    counts are pareto-distributed - a small repeat-buyer cohort, most
    buyers with just one or two orders.
    """
    from seeding.constants import BUYER_ACTIVE_RATIO

    rng = random.Random()
    existing = Order.objects.filter(buyer__in=buyers).count()
    if existing >= target_count:
        if stdout:
            stdout.write(f"  Orders: already have {existing}, target is {target_count} - skipping.")
        return existing

    to_create = target_count - existing
    product_pool = _load_active_product_pool()
    if not product_pool:
        if stdout:
            stdout.write("  Orders: no active products exist yet - skipping (seed products first).")
        return existing

    active_buyer_count = max(1, int(len(buyers) * BUYER_ACTIVE_RATIO))
    active_buyers = rng.sample(buyers, min(active_buyer_count, len(buyers)))
    # alpha=1.3 was tried first and produced an extreme, unrealistic skew in
    # testing (one buyer holding >25% of all platform orders) - 1.8 is
    # closer to the ~50-70% "top 20% own X%" range the distribution report
    # targets. Re-check the report after any further tuning here.
    counts = pareto_weighted_split(to_create, len(active_buyers), alpha=1.8, rng=rng)
    buyers_with_counts = list(zip(active_buyers, counts))

    created_count, quantity_by_product_id = _build_orders(
        buyers_with_counts, product_pool, rng, months_back=ORDER_MONTHS_BACK
    )
    _decrement_stock(quantity_by_product_id, stdout=stdout)

    if stdout:
        stdout.write(f"  Orders: created {created_count} new ({existing} -> {existing + created_count}).")
    return existing + created_count


def create_demo_buyer_orders(demo_buyer, demo_sellers, stdout=None):
    """
    ~15-20 orders for the demo buyer, deliberately mixing demo-seller
    products with random bulk-seller products - real, memorable order
    history to demo Phase 4's conversational assistant against later,
    instead of anonymous random data.
    """
    rng = random.Random()
    existing = Order.objects.filter(buyer=demo_buyer).count()
    target = rng.randint(15, 20)
    if existing >= target:
        if stdout:
            stdout.write(f"  Demo buyer: already has {existing} orders - skipping.")
        return existing

    demo_products = list(
        Product.objects.filter(seller__in=demo_sellers, is_active=True)
        .values("id", "seller_id", "price", "stock")
    )
    general_pool = _load_active_product_pool()
    # Bias toward demo-seller products (~60%) while still mixing in the general
    # catalog, so the demo buyer's history is memorable but not exclusively demo data.
    mixed_pool = demo_products * 3 + general_pool
    if not mixed_pool:
        if stdout:
            stdout.write("  Demo buyer: no active products exist yet - skipping.")
        return existing

    to_create = target - existing
    created_count, quantity_by_product_id = _build_orders(
        [(demo_buyer, to_create)], mixed_pool, rng, months_back=ORDER_MONTHS_BACK
    )
    _decrement_stock(quantity_by_product_id, stdout=stdout)

    if stdout:
        stdout.write(f"  Demo buyer: created {created_count} new orders ({existing} -> {existing + created_count}).")
    return existing + created_count
