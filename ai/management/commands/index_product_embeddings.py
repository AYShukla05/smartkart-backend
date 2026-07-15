import logging
import time

from django.core.management.base import BaseCommand

from ai.models import ProductEmbedding
from ai.services.llm_client import (
    CURRENT_EMBEDDING_MODEL_ID,
    DEFAULT_EMBEDDING_MODEL,
    LLMGenerationError,
    embed_batch,
)
from products.models import Product

logger = logging.getLogger(__name__)

PRICE_PER_MILLION_TOKENS = 0.02
ESTIMATED_CHARS_PER_TOKEN = 4
BATCH_SLEEP_SECONDS = 1


def _build_embedding_text(product):
    lines = [
        f"Product: {product.name}",
        f"Category: {product.category.name}",
        f"Description: {product.description}",
    ]
    if product.seo_keywords:
        lines.append(f"Keywords: {', '.join(product.seo_keywords)}")
    return "\n".join(lines)


class Command(BaseCommand):
    help = "Embed the product catalog and store vectors in ProductEmbedding."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the cost estimate and exit without writing anything (default).",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Run the indexing. Without this flag, only prints a cost estimate.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Products to embed per API call (default 100).",
        )
        parser.add_argument(
            "--reindex-all",
            action="store_true",
            help="Delete existing embeddings and re-embed every active product. Use when switching models.",
        )

    def handle(self, *args, **options):
        reindex_all = options["reindex_all"]
        active_products = Product.objects.filter(is_active=True).select_related("category")

        if reindex_all:
            products = list(active_products)
            already_indexed_count = 0
        else:
            already_indexed_ids = ProductEmbedding.objects.filter(
                model_id=CURRENT_EMBEDDING_MODEL_ID
            ).values_list("product_id", flat=True)
            products = list(active_products.exclude(id__in=already_indexed_ids))
            already_indexed_count = active_products.count() - len(products)

        if not options["confirm"]:
            self._print_dry_run(products, already_indexed_count)
            return

        if reindex_all:
            ProductEmbedding.objects.filter(product__in=active_products).delete()

        self._run_indexing(products, options["batch_size"])

    def _print_dry_run(self, products, already_indexed_count):
        total_chars = sum(len(_build_embedding_text(p)) for p in products)
        estimated_tokens = total_chars // ESTIMATED_CHARS_PER_TOKEN
        estimated_cost = estimated_tokens / 1_000_000 * PRICE_PER_MILLION_TOKENS

        self.stdout.write(f"Products to index: {len(products):,}")
        self.stdout.write(f"Products already indexed (will skip): {already_indexed_count:,}")
        self.stdout.write(f"Embedding model: {DEFAULT_EMBEDDING_MODEL}")
        self.stdout.write(f"Estimated tokens: ~{estimated_tokens:,}")
        self.stdout.write(f"Estimated cost: ~${estimated_cost:.2f}")
        self.stdout.write("Run with --confirm to proceed.")

    def _run_indexing(self, products, batch_size):
        total = len(products)
        total_batches = (total + batch_size - 1) // batch_size
        indexed = 0
        failed = 0
        total_tokens = 0

        for batch_num in range(total_batches):
            batch = products[batch_num * batch_size: (batch_num + 1) * batch_size]
            tokens = self._index_batch(batch)
            if tokens is None:
                failed += len(batch)
            else:
                indexed += len(batch)
                total_tokens += tokens

            self.stdout.write(
                f"Batch {batch_num + 1}/{total_batches} complete "
                f"({indexed + failed}/{total} products processed)"
            )
            if batch_num + 1 < total_batches:
                time.sleep(BATCH_SLEEP_SECONDS)

        self.stdout.write(f"Indexed: {indexed:,}")
        self.stdout.write(f"Failed (skipped, retry on next run): {failed:,}")
        if total_tokens:
            actual_cost = total_tokens / 1_000_000 * PRICE_PER_MILLION_TOKENS
            self.stdout.write(f"Actual tokens used: {total_tokens:,}")
            self.stdout.write(f"Actual cost: ~${actual_cost:.4f}")

    def _index_batch(self, batch):
        """Embed one batch and upsert ProductEmbedding rows. Returns the
        batch's token usage, or None if the whole batch failed.

        A failure here isn't per-product: embed_batch() is a single API
        call for the whole batch, so one bad call means the whole batch
        is skipped, not one product. Skipped products simply remain
        unindexed and get picked up automatically on the next --confirm
        run, since indexing only ever targets not-yet-indexed products.
        """
        texts = [_build_embedding_text(product) for product in batch]
        try:
            vectors, total_tokens = embed_batch(texts, input_type="document")
        except LLMGenerationError:
            product_ids = [product.id for product in batch]
            logger.error(
                "Embedding batch failed for product IDs %s", product_ids, exc_info=True
            )
            return None

        for product, vector in zip(batch, vectors):
            ProductEmbedding.objects.update_or_create(
                product=product,
                defaults={"embedding": vector, "model_id": CURRENT_EMBEDDING_MODEL_ID},
            )
        return total_tokens
