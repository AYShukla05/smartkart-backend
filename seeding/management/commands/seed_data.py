"""
Populates the database with realistic fake data (users, products, orders)
and a reusable S3-backed stock-photo pool, so upcoming AI features have
something meaningful to operate over. Full reasoning in SEEDING_PLAN.md.

    python manage.py seed_data --dry-run              # always safe, writes nothing
    python manage.py seed_data --confirm
    python manage.py seed_data --confirm --reset
    python manage.py seed_data --rebuild-image-pool
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count

from orders.models import Order
from products.models import Product
from seeding.constants import (
    DEFAULT_BUYERS,
    DEFAULT_ORDERS,
    DEFAULT_PRODUCTS,
    DEFAULT_SELLERS,
    SEED_EMAIL_DOMAIN,
)
from seeding.generators.categories import create_categories
from seeding.generators.images import build_image_pool, rebuild_image_pool
from seeding.generators.orders import create_demo_buyer_orders, top_up_orders
from seeding.generators.products import create_demo_seller_products, top_up_products
from seeding.generators.users import create_demo_accounts, delete_seed_data, top_up_bulk_users
from seeding.models import SeedImagePoolItem

User = get_user_model()


def _fmt_delta(existing, target):
    if existing >= target:
        return f"{existing} existing, target {target} - no change needed"
    return f"{existing} existing -> target {target} (+{target - existing})"


class Command(BaseCommand):
    help = "Seeds realistic fake data at scale. Always dry-run unless --confirm is passed. See SEEDING_PLAN.md."

    def add_arguments(self, parser):
        parser.add_argument("--confirm", action="store_true", help="Actually write data.")
        parser.add_argument("--dry-run", action="store_true", help="No-op flag - dry-run is already the default without --confirm.")
        parser.add_argument("--reset", action="store_true", help="Delete existing seed data first. Never touches the image pool or demo accounts.")
        parser.add_argument("--rebuild-image-pool", action="store_true", help="Delete and re-download the entire stock-photo pool, then exit.")
        parser.add_argument("--buyers", type=int, default=DEFAULT_BUYERS)
        parser.add_argument("--sellers", type=int, default=DEFAULT_SELLERS)
        parser.add_argument("--products", type=int, default=DEFAULT_PRODUCTS)
        parser.add_argument("--orders", type=int, default=DEFAULT_ORDERS)

    def handle(self, *args, **options):
        self._print_target_database()

        if options["rebuild_image_pool"]:
            self.stdout.write(self.style.WARNING("Rebuilding image pool (deleting existing pool first)..."))
            rebuild_image_pool(stdout=self.stdout)
            return

        if not options["confirm"]:
            self._print_dry_run_plan(options)
            self.stdout.write(self.style.WARNING(
                "\nDry run only - nothing was written. Pass --confirm to actually seed data."
            ))
            return

        if options["reset"]:
            self.stdout.write(self.style.WARNING("Deleting existing seed data (users/products/orders only, not the image pool)..."))
            delete_seed_data(stdout=self.stdout)

        self.stdout.write("\nStep 0: Image pool")
        build_image_pool(stdout=self.stdout)

        self.stdout.write("\nStep 1: Users")
        top_up_bulk_users(User.BUYER, "buyer", options["buyers"], stdout=self.stdout)
        top_up_bulk_users(User.SELLER, "seller", options["sellers"], stdout=self.stdout)
        demo_buyer, demo_sellers = create_demo_accounts(stdout=self.stdout)

        self.stdout.write("\nStep 2: Categories")
        categories_by_slug = create_categories(stdout=self.stdout)

        self.stdout.write("\nStep 3: Products")
        sellers = list(User.objects.filter(role=User.SELLER, email__endswith=f"@{SEED_EMAIL_DOMAIN}"))
        top_up_products(sellers, categories_by_slug, options["products"], stdout=self.stdout)
        create_demo_seller_products(demo_sellers, categories_by_slug, stdout=self.stdout)

        self.stdout.write("\nStep 4: Orders")
        buyers = list(User.objects.filter(role=User.BUYER, email__endswith=f"@{SEED_EMAIL_DOMAIN}"))
        top_up_orders(buyers, options["orders"], stdout=self.stdout)
        create_demo_buyer_orders(demo_buyer, demo_sellers, stdout=self.stdout)

        self._print_distribution_report()
        self.stdout.write(self.style.SUCCESS("\nDone."))

    def _print_target_database(self):
        db = connection.settings_dict
        self.stdout.write(self.style.WARNING(
            f"Target database: host={db.get('HOST') or '(local file)'}  name={db.get('NAME')}"
        ))
        self.stdout.write("")  # never prints db.get('PASSWORD') or db.get('USER') - host/name only

    def _print_dry_run_plan(self, options):
        existing_buyers = User.objects.filter(role=User.BUYER, email__endswith=f"@{SEED_EMAIL_DOMAIN}").count()
        existing_sellers = User.objects.filter(role=User.SELLER, email__endswith=f"@{SEED_EMAIL_DOMAIN}").count()
        existing_products = Product.objects.filter(seller__email__endswith=f"@{SEED_EMAIL_DOMAIN}").count()
        existing_orders = Order.objects.filter(buyer__email__endswith=f"@{SEED_EMAIL_DOMAIN}").count()
        existing_pool_images = SeedImagePoolItem.objects.count()

        self.stdout.write("Planned changes:")
        self.stdout.write(f"  Buyers:       {_fmt_delta(existing_buyers, options['buyers'])}")
        self.stdout.write(f"  Sellers:      {_fmt_delta(existing_sellers, options['sellers'])}")
        self.stdout.write(f"  Products:     {_fmt_delta(existing_products, options['products'])}")
        self.stdout.write(f"  Orders:       {_fmt_delta(existing_orders, options['orders'])}")
        self.stdout.write(f"  Pool images:  {existing_pool_images} existing (built once, reused - not part of --confirm scale)")
        if options["reset"]:
            self.stdout.write(self.style.WARNING(
                "  --reset was also passed: existing seed users/products/orders would be deleted "
                "before reseeding (the image pool would NOT be touched)."
            ))

    def _print_distribution_report(self):
        self.stdout.write("\nDistribution check (verifying the skew is real, not flat - see SEEDING_PLAN.md):")

        seller_counts = list(
            Product.objects.filter(seller__email__endswith=f"@{SEED_EMAIL_DOMAIN}")
            .values("seller_id").annotate(n=Count("id")).values_list("n", flat=True)
        )
        self._report_skew("Products per seller", seller_counts, "products")

        total_buyers = User.objects.filter(role=User.BUYER, email__endswith=f"@{SEED_EMAIL_DOMAIN}").count()
        buyer_counts = list(
            Order.objects.filter(buyer__email__endswith=f"@{SEED_EMAIL_DOMAIN}")
            .values("buyer_id").annotate(n=Count("id")).values_list("n", flat=True)
        )
        # Buyers with zero orders never appear in the grouped query above - pad
        # them back in so min/median reflect the true full buyer population,
        # not just the subset that ordered at least once.
        buyer_counts += [0] * max(0, total_buyers - len(buyer_counts))
        self._report_skew("Orders per buyer", buyer_counts, "orders")

    def _report_skew(self, label, counts, unit):
        if not counts:
            self.stdout.write(f"  {label}: no data yet.")
            return
        counts_sorted = sorted(counts, reverse=True)
        n = len(counts_sorted)
        total = sum(counts_sorted)
        top_20_n = max(1, int(n * 0.2))
        top_20_share = (sum(counts_sorted[:top_20_n]) / total * 100) if total else 0
        median = counts_sorted[n // 2]
        self.stdout.write(f"  {label} - min: {min(counts_sorted)}, median: {median}, max: {counts_sorted[0]}")
        self.stdout.write(f"  Top 20% own {top_20_share:.0f}% of all {unit}")
