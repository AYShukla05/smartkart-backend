"""Bulk product generation (pareto-skewed across sellers) and the demo
sellers' curated, fully-described catalog."""
import json
import random
from decimal import Decimal
from pathlib import Path

from products.models import Product, ProductImage
from seeding.constants import (
    ADJECTIVES,
    BRANDS,
    CATEGORIES,
    IMAGES_PER_PRODUCT_MAX,
    IMAGES_PER_PRODUCT_MIN,
)
from seeding.generators.distributions import pareto_weighted_split
from seeding.generators.images import get_image_pool_by_category
from seeding.generators.utils import allow_manual_created_at, random_historical_datetime

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "real_catalog.json"

DEMO_DESCRIPTION_TEMPLATES = [
    "The {name} is designed for everyday reliability, built with quality "
    "materials that hold up to daily use - a favorite among {category} shoppers "
    "looking for something that just works.",
    "Meet the {name} - thoughtfully designed, well-built, and ready for daily "
    "use. It's become one of our most requested {category} picks.",
    "Looking for a dependable {category} pick? The {name} delivers solid "
    "performance without unnecessary extras, at a price that makes sense.",
    "The {name} blends practical design with real durability - a genuine "
    "standout in our {category} lineup, and one of our most repeat-purchased items.",
    "Built to last and easy to use, the {name} is exactly what you want from a "
    "{category} purchase: no surprises, just quality.",
]


def _random_product_name(category_slug, rng):
    nouns = CATEGORIES[category_slug]["nouns"]
    noun = rng.choice(nouns)
    name = f"{rng.choice(ADJECTIVES)} {noun}" if rng.random() < 0.5 else noun
    if rng.random() < 0.4:
        name = f"{name} - {rng.choice(BRANDS)}"
    return name


def _random_price(category_slug, rng):
    low, high, mode = CATEGORIES[category_slug]["price"]
    return Decimal(str(round(rng.triangular(low, high, mode), 2)))


def _load_catalog():
    with open(CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _next_catalog_entry(catalog, category_slug, pools, rng):
    """
    Returns the next real {name, description, price?} entry for this
    category from real_catalog.json, or None if the catalog has nothing
    for it. Each category's entries are shuffled once and then cycled
    through in order (wrapping via modulo) rather than re-randomized per
    call, so a single run doesn't repeat an entry until it's exhausted
    the whole pool for that category - same approach as
    enrich_product_catalog's per-category cycling.
    """
    entries = catalog.get(category_slug)
    if not entries:
        return None
    if category_slug not in pools:
        shuffled = list(entries)
        rng.shuffle(shuffled)
        pools[category_slug] = {"entries": shuffled, "index": 0}
    pool = pools[category_slug]
    entry = pool["entries"][pool["index"] % len(pool["entries"])]
    pool["index"] += 1
    return entry


def _initial_stock(rng):
    """
    Generous starting stock. This is deliberately NOT where realistic
    low/zero-stock products come from - the order generator decrements
    stock based on simulated historical orders (mirroring real checkout
    semantics, since there's no restock-on-cancel logic in the app), so
    popular products naturally end up lower on stock than unpopular ones.
    Modeling it any other way would double up two inconsistent stories
    about the same number.
    """
    return rng.randint(50, 500)


def _assign_images(products, image_pool, rng, batch_size=500):
    image_batch = []
    for product in products:
        pool = image_pool.get(product.category.slug, [])
        if not pool:
            continue
        n_images = rng.randint(IMAGES_PER_PRODUCT_MIN, min(IMAGES_PER_PRODUCT_MAX, len(pool)))
        for i, url in enumerate(rng.sample(pool, n_images)):
            image_batch.append(ProductImage(product=product, image_url=url, is_thumbnail=(i == 0)))
    ProductImage.objects.bulk_create(image_batch, batch_size=batch_size)
    return len(image_batch)


def top_up_products(sellers, categories_by_slug, target_count, stdout=None):
    """
    Tops up bulk seed products to target_count, pareto-distributed across
    `sellers` (a few power sellers, many small ones). Name/description come
    from real_catalog.json where the category has entries (same source
    enrich_product_catalog uses), so a fresh seed run doesn't regress to
    mismatched synthetic names paired with blank descriptions; price is
    taken from the catalog too where the source dataset has it, otherwise
    stays synthetic (triangular estimate).
    """
    rng = random.Random()
    existing = Product.objects.filter(seller__in=sellers).count()
    if existing >= target_count:
        if stdout:
            stdout.write(f"  Products: already have {existing}, target is {target_count} - skipping.")
        return existing

    to_create = target_count - existing
    image_pool = get_image_pool_by_category()
    category_slugs = list(categories_by_slug.keys())
    counts_per_seller = pareto_weighted_split(to_create, len(sellers), alpha=1.5, rng=rng)

    catalog = _load_catalog()
    catalog_pools = {}
    from_catalog = 0

    batch = []
    for seller, count in zip(sellers, counts_per_seller):
        for _ in range(count):
            slug = rng.choice(category_slugs)
            entry = _next_catalog_entry(catalog, slug, catalog_pools, rng)
            if entry:
                name = entry["name"]
                description = entry["description"]
                price = Decimal(str(entry["price"])) if "price" in entry else _random_price(slug, rng)
                from_catalog += 1
            else:
                name = _random_product_name(slug, rng)
                description = ""
                price = _random_price(slug, rng)
            batch.append(Product(
                seller=seller,
                category=categories_by_slug[slug],
                name=name,
                description=description,
                price=price,
                stock=_initial_stock(rng),
                is_active=rng.random() < 0.93,
                created_at=random_historical_datetime(rng, months_back=12),
            ))

    with allow_manual_created_at(Product):
        created = Product.objects.bulk_create(batch, batch_size=500)

    images_added = _assign_images(created, image_pool, rng)

    if stdout:
        stdout.write(
            f"  Products: created {to_create} new ({existing} -> {existing + to_create}), "
            f"{from_catalog} from real catalog data, {images_added} images assigned."
        )
    return existing + to_create


def create_demo_seller_products(demo_sellers, categories_by_slug, stdout=None):
    """
    ~20-30 curated products per demo seller, spread across a few categories,
    with real written descriptions (not blank) - meant to look finished and
    screenshot-ready immediately, not wait on Phase 1's generator.
    """
    rng = random.Random()
    image_pool = get_image_pool_by_category()
    demo_category_slugs = ["electronics", "home-kitchen", "sports-outdoors"]
    total_created = 0

    for seller in demo_sellers:
        existing = Product.objects.filter(seller=seller).count()
        target = rng.randint(20, 30)
        if existing >= target:
            if stdout:
                stdout.write(f"  Demo seller {seller.email}: already has {existing} products - skipping.")
            continue

        batch = []
        for _ in range(target - existing):
            slug = rng.choice(demo_category_slugs)
            category = categories_by_slug[slug]
            name = _random_product_name(slug, rng)
            description = rng.choice(DEMO_DESCRIPTION_TEMPLATES).format(
                name=name, category=category.name.lower()
            )
            batch.append(Product(
                seller=seller,
                category=category,
                name=name,
                description=description,
                price=_random_price(slug, rng),
                stock=_initial_stock(rng),
                is_active=True,
                created_at=random_historical_datetime(rng, months_back=8),
            ))

        with allow_manual_created_at(Product):
            created = Product.objects.bulk_create(batch, batch_size=100)

        images_added = _assign_images(created, image_pool, rng, batch_size=100)
        total_created += len(created)

        if stdout:
            stdout.write(
                f"  Demo seller {seller.email}: created {len(created)} products, "
                f"{images_added} images assigned."
            )

    return total_created
