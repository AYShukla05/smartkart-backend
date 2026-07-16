import logging

from django.db import connection
from pgvector.django import CosineDistance

from ai.models import ProductEmbedding
from ai.services.llm_client import CURRENT_EMBEDDING_MODEL_ID, LLMGenerationError, embed
from products.models import Product

logger = logging.getLogger(__name__)

# How deep to rank candidates at all, not how many a buyer sees - bounds the
# HNSW search itself. 144 = 12 x 12, so a buyer paging all the way to the
# bottom at 12/page (see smartkart.pagination.ProductPagination) never hits
# a dangling partial last page.
CANDIDATE_CEILING = 144

# Cosine distance cutoff for "confidently relevant", picked by running
# ai/eval_data.py's hand-curated cases against measured distances rather
# than guessing - see ai/management/commands/eval_semantic_search.py.
RELEVANCE_THRESHOLD = 0.50

# Minimum results to return even when fewer than this pass the threshold,
# padded with the next-closest candidates beyond it. Also a multiple of the
# page size, for the same clean-last-page reason as the ceiling.
MINIMUM_FLOOR = 24


def _select_candidates(ranked):
    """
    Given ranked as a list of (id, distance) tuples sorted by distance
    ascending, apply the floor/threshold/ceiling rule and return
    (selected_ids, is_fallback, confident_count). Pure, no I/O - the actual
    ranking needs Postgres, but this boundary logic doesn't, so it's unit
    tested directly against hand-built ranked lists.
    """
    within_threshold = [pid for pid, distance in ranked if distance <= RELEVANCE_THRESHOLD]
    confident_count = len(within_threshold)
    is_fallback = confident_count < MINIMUM_FLOOR
    selected_ids = [pid for pid, _ in ranked[:MINIMUM_FLOOR]] if is_fallback else within_threshold
    return selected_ids, is_fallback, confident_count


def semantic_search(query, category_id=None):
    """
    Embed the query and return (queryset, is_fallback, confident_count):

    - queryset: Products ordered by cosine similarity to the query vector.
      Empty (not an exception) if no embeddings exist yet under the current
      embedding model, or if the query embedding fails. Only matches
      is_active products, regardless of whether a product was deactivated
      after it was indexed.
    - is_fallback: True if fewer than MINIMUM_FLOOR candidates passed
      RELEVANCE_THRESHOLD, meaning the result was padded with weaker
      matches rather than being genuinely confident - callers can use this
      to show "no exact matches, but here's what's related" instead of
      presenting a padded result as if it were exact.
    - confident_count: how many of the leading results (queryset is sorted
      by distance ascending, so this is a prefix) actually passed
      RELEVANCE_THRESHOLD - the rest, if any, are padding. Lets callers
      split genuine matches from padding instead of treating a whole batch
      as fallback just because it needed padding to reach MINIMUM_FLOOR.
    """
    if not ProductEmbedding.objects.filter(model_id=CURRENT_EMBEDDING_MODEL_ID).exists():
        return Product.objects.none(), True, 0

    try:
        query_vector = embed(query, input_type="query")
    except LLMGenerationError:
        logger.error("Semantic search embedding failed for query: %r", query, exc_info=True)
        return Product.objects.none(), True, 0

    candidates = Product.objects.filter(
        is_active=True,
        embedding__model_id=CURRENT_EMBEDDING_MODEL_ID,
    )
    if category_id is not None:
        candidates = candidates.filter(category_id=category_id)

    if connection.vendor == "postgresql":
        # pgvector's HNSW index only ever examines hnsw.ef_search candidates
        # per query, regardless of the SQL LIMIT requested - defaults to 40,
        # silently capping ranking depth well below CANDIDATE_CEILING.
        with connection.cursor() as cursor:
            cursor.execute("SET hnsw.ef_search = %s", [CANDIDATE_CEILING])

    ranked = list(
        candidates.annotate(
            distance=CosineDistance("embedding__embedding", query_vector)
        ).order_by("distance")[:CANDIDATE_CEILING].values_list("id", "distance")
    )

    selected_ids, is_fallback, confident_count = _select_candidates(ranked)

    queryset = Product.objects.filter(
        id__in=selected_ids
    ).select_related("category").prefetch_related("images").annotate(
        distance=CosineDistance("embedding__embedding", query_vector)
    ).order_by("distance")

    return queryset, is_fallback, confident_count
