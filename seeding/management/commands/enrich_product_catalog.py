"""
Assigns real product names/descriptions (and prices, where the source
dataset has them) to existing blank-description products, sourced from
seeding/data/real_catalog.json - see that file's provenance notes.

Update-in-place only: never deletes or recreates products, since
CartItem/OrderItem both PROTECT their product FK, so the ~15,000 seeded
orders would block any delete-based approach anyway.

Holds out a small random sample with blank descriptions so they remain
real candidates for testing the "Generate with AI" feature interactively.

    python manage.py enrich_product_catalog --dry-run              # always safe, writes nothing
    python manage.py enrich_product_catalog --confirm
    python manage.py enrich_product_catalog --confirm --holdout=20
"""
import json
import random
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import connection

from products.models import Product

CATALOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "real_catalog.json"
DEFAULT_HOLDOUT = 50
BULK_UPDATE_BATCH_SIZE = 500


class Command(BaseCommand):
    help = "Enrich blank-description products with real name/description/price data. See module docstring."

    def add_arguments(self, parser):
        parser.add_argument("--confirm", action="store_true", help="Actually write the changes.")
        parser.add_argument("--dry-run", action="store_true", help="No-op flag - dry-run is already the default without --confirm.")
        parser.add_argument(
            "--holdout", type=int, default=DEFAULT_HOLDOUT,
            help=f"Products to leave with a blank description for AI-generation testing (default {DEFAULT_HOLDOUT}).",
        )

    def handle(self, *args, **options):
        self._print_target_database()

        with open(CATALOG_PATH, encoding="utf-8") as f:
            catalog = json.load(f)

        products = list(Product.objects.filter(description="").select_related("category"))
        if not products:
            self.stdout.write("No blank-description products found - nothing to do.")
            return

        rng = random.Random()
        rng.shuffle(products)

        holdout_count = min(options["holdout"], len(products))
        to_enrich = products[holdout_count:]

        by_category = {}
        for product in to_enrich:
            by_category.setdefault(product.category.slug, []).append(product)

        updated = []
        missing_categories = []
        price_from_real_data = 0
        for slug, category_products in by_category.items():
            entries = catalog.get(slug)
            if not entries:
                missing_categories.append(slug)
                continue
            shuffled_entries = list(entries)
            rng.shuffle(shuffled_entries)
            for i, product in enumerate(category_products):
                entry = shuffled_entries[i % len(shuffled_entries)]
                product.name = entry["name"]
                product.description = entry["description"]
                if "price" in entry:
                    product.price = entry["price"]
                    price_from_real_data += 1
                updated.append(product)

        self.stdout.write("Planned changes:")
        self.stdout.write(f"  Blank-description products found: {len(products):,}")
        self.stdout.write(f"  Held out for AI-generation testing (left blank): {holdout_count}")
        self.stdout.write(f"  To be enriched with real name/description: {len(updated):,}")
        self.stdout.write(f"  ...of which real price also applied: {price_from_real_data:,}")
        self.stdout.write(
            f"  ...of which kept existing (synthetic) price - no real price data for that category: "
            f"{len(updated) - price_from_real_data:,}"
        )
        if missing_categories:
            self.stdout.write(self.style.WARNING(
                f"  No catalog entries for: {', '.join(sorted(missing_categories))} - those products left untouched."
            ))

        if not options["confirm"]:
            self.stdout.write(self.style.WARNING("\nDry run only - nothing was written. Pass --confirm to apply."))
            return

        Product.objects.bulk_update(
            updated, ["name", "description", "price"], batch_size=BULK_UPDATE_BATCH_SIZE
        )
        self.stdout.write(self.style.SUCCESS(f"\nUpdated {len(updated):,} products."))

    def _print_target_database(self):
        db = connection.settings_dict
        self.stdout.write(self.style.WARNING(
            f"Target database: host={db.get('HOST') or '(local file)'}  name={db.get('NAME')}"
        ))
        self.stdout.write("")
