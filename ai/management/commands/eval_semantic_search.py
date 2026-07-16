import logging

from django.core.management.base import BaseCommand
from pgvector.django import CosineDistance

from ai.eval_data import EVAL_CASES
from ai.services.llm_client import CURRENT_EMBEDDING_MODEL_ID, LLMGenerationError, embed
from ai.services.search import CANDIDATE_CEILING
from categories.models import Category
from products.models import Product

logger = logging.getLogger(__name__)

THRESHOLD_CANDIDATES = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
DEFAULT_TOP_K = 10


class Command(BaseCommand):
    help = (
        "Run the hand-curated eval set in ai/eval_data.py against real ranked "
        "search results, to help choose semantic_search()'s relevance distance "
        "threshold from measurement instead of a guess."
    )

    def handle(self, *args, **options):
        from django.db import connection

        with connection.cursor() as cursor:
            # pgvector's HNSW index only ever examines hnsw.ef_search candidates
            # per query, regardless of the SQL LIMIT requested — defaults to 40,
            # silently capping ranking depth well below our intended ceiling.
            cursor.execute("SET hnsw.ef_search = %s", [CANDIDATE_CEILING])

        case_results = [self._run_case(case) for case in EVAL_CASES]
        self._print_summary(case_results)

    def _rank_candidates(self, query, category_id=None):
        try:
            query_vector = embed(query, input_type="query")
        except LLMGenerationError:
            logger.error("Eval embedding failed for query: %r", query, exc_info=True)
            return []

        queryset = Product.objects.filter(
            is_active=True,
            embedding__model_id=CURRENT_EMBEDDING_MODEL_ID,
        )
        if category_id is not None:
            queryset = queryset.filter(category_id=category_id)

        ranked = queryset.annotate(
            distance=CosineDistance("embedding__embedding", query_vector)
        ).order_by("distance")[:CANDIDATE_CEILING]

        return [(product.name, product.distance) for product in ranked]

    def _run_case(self, case):
        query = case["query"]
        category_id = None
        if "category_name" in case:
            category = Category.objects.filter(name=case["category_name"]).first()
            category_id = category.id if category else None

        candidates = self._rank_candidates(query, category_id=category_id)

        self.stdout.write(f'\n=== "{query}" ===')
        if case.get("note"):
            self.stdout.write(f"  ({case['note']})")

        if "expect_product_name_contains" in case:
            target = case["expect_product_name_contains"]
            match = next(
                ((i, name, dist) for i, (name, dist) in enumerate(candidates) if target in name),
                None,
            )
            if match:
                idx, name, dist = match
                self.stdout.write(f"  expected match at rank {idx + 1}, distance {dist:.4f}: {name}")
            else:
                self.stdout.write(f"  expected match NOT found in top {CANDIDATE_CEILING}: {target!r}")

        if "expect_not_in_top_k" in case:
            excluded = case["expect_not_in_top_k"]
            k = case.get("k", DEFAULT_TOP_K)
            match = next(
                ((i, name, dist) for i, (name, dist) in enumerate(candidates[:k]) if excluded in name),
                None,
            )
            if match:
                idx, name, dist = match
                self.stdout.write(f"  excluded item at rank {idx + 1} (within top {k}), distance {dist:.4f}: {name}")
            else:
                self.stdout.write(f"  excluded item correctly absent from top {k}: {excluded!r}")

        if case.get("expect_fallback"):
            best = candidates[0] if candidates else None
            self.stdout.write(f"  closest available distance (should be notably high): {best[1]:.4f} ({best[0]})" if best else "  no candidates at all")

        if "expect_min_result_count" in case:
            self.stdout.write(f"  candidate pool size (within ceiling {CANDIDATE_CEILING}): {len(candidates)}")

        return {
            "case": case,
            "outcomes": {t: self._evaluate_at_threshold(case, candidates, t) for t in THRESHOLD_CANDIDATES},
        }

    def _evaluate_at_threshold(self, case, candidates, threshold):
        passing = [(name, dist) for name, dist in candidates if dist <= threshold]
        checks = []

        if "expect_product_name_contains" in case:
            target = case["expect_product_name_contains"]
            checks.append(any(target in name for name, _ in passing))

        if "expect_not_in_top_k" in case:
            excluded = case["expect_not_in_top_k"]
            k = case.get("k", DEFAULT_TOP_K)
            checks.append(not any(excluded in name for name, _ in passing[:k]))

        if case.get("expect_fallback"):
            checks.append(len(passing) == 0)

        if "expect_min_result_count" in case:
            checks.append(len(passing) >= case["expect_min_result_count"])

        return all(checks) if checks else None

    def _print_summary(self, case_results):
        self.stdout.write("\n=== Threshold summary (cases passing every assertion) ===")
        for t in THRESHOLD_CANDIDATES:
            outcomes = [r["outcomes"][t] for r in case_results if r["outcomes"][t] is not None]
            passes = sum(1 for o in outcomes if o)
            self.stdout.write(f"  distance <= {t:.2f}: {passes}/{len(outcomes)} cases pass")
