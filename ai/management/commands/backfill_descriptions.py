import logging
import time

from django.core.management.base import BaseCommand

from ai.parsers import parse_description_json
from ai.prompts import DESCRIPTION_SYSTEM_PROMPT, build_description_prompt
from ai.services.llm_client import LLMGenerationError, generate
from products.models import Product

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
INPUT_PRICE_PER_MILLION = 1.00
OUTPUT_PRICE_PER_MILLION = 5.00
ESTIMATED_INPUT_TOKENS_PER_REQUEST = 420
ESTIMATED_OUTPUT_TOKENS_PER_REQUEST = 280
BATCH_SLEEP_SECONDS = 2


class Command(BaseCommand):
    help = "Backfill AI-generated descriptions for products with a blank description."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the cost estimate and exit without writing anything (default).",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Run the backfill. Without this flag, only prints a cost estimate.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=10,
            help="Products to process per batch (default 10).",
        )

    def handle(self, *args, **options):
        product_ids = list(
            Product.objects.filter(description="").values_list("id", flat=True)
        )
        count = len(product_ids)

        if not options["confirm"]:
            self._print_dry_run(count)
            return

        self._run_backfill(product_ids, options["batch_size"])

    def _print_dry_run(self, count):
        input_cost = count * ESTIMATED_INPUT_TOKENS_PER_REQUEST / 1_000_000 * INPUT_PRICE_PER_MILLION
        output_cost = count * ESTIMATED_OUTPUT_TOKENS_PER_REQUEST / 1_000_000 * OUTPUT_PRICE_PER_MILLION
        total_cost = input_cost + output_cost

        self.stdout.write(f"Products needing descriptions: {count:,}")
        self.stdout.write(f"Model: {MODEL}")
        self.stdout.write(f"Estimated input tokens/request: ~{ESTIMATED_INPUT_TOKENS_PER_REQUEST}")
        self.stdout.write(f"Estimated output tokens/request: ~{ESTIMATED_OUTPUT_TOKENS_PER_REQUEST}")
        self.stdout.write(f"Estimated total cost: ${total_cost:.2f}")
        self.stdout.write("Run with --confirm to proceed.")

    def _run_backfill(self, product_ids, batch_size):
        total = len(product_ids)
        total_batches = (total + batch_size - 1) // batch_size
        processed = 0

        for batch_num in range(total_batches):
            batch_ids = product_ids[batch_num * batch_size : (batch_num + 1) * batch_size]
            batch = Product.objects.filter(id__in=batch_ids).select_related("category")

            for product in batch:
                try:
                    self._generate_and_save(product)
                except LLMGenerationError:
                    logger.error(
                        "Failed to generate description for product %s", product.id, exc_info=True
                    )
                processed += 1

            self.stdout.write(
                f"Batch {batch_num + 1}/{total_batches} complete ({processed}/{total} products)"
            )
            if batch_num + 1 < total_batches:
                time.sleep(BATCH_SLEEP_SECONDS)

    def _generate_and_save(self, product):
        prompt = build_description_prompt(product.name, product.category.name, product.price)
        raw = generate(prompt, system=DESCRIPTION_SYSTEM_PROMPT, model=MODEL)
        parsed = parse_description_json(raw)
        product.description = parsed["description"]
        product.save(update_fields=["description"])
