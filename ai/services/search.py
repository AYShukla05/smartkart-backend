import logging

from pgvector.django import CosineDistance

from ai.models import ProductEmbedding
from ai.services.llm_client import CURRENT_EMBEDDING_MODEL_ID, LLMGenerationError, embed
from products.models import Product

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 10


def semantic_search(query, limit=DEFAULT_LIMIT, category_id=None):
    """
    Embed the query and return a QuerySet of Products ordered by cosine
    similarity to the query vector.

    Returns an empty QuerySet (not an exception) if no embeddings exist
    yet under the current embedding model, or if the query embedding
    fails. Only matches is_active products, regardless of whether a
    product was deactivated after it was indexed.
    """
    if not ProductEmbedding.objects.filter(model_id=CURRENT_EMBEDDING_MODEL_ID).exists():
        return Product.objects.none()

    try:
        query_vector = embed(query, input_type="query")
    except LLMGenerationError:
        logger.error("Semantic search embedding failed for query: %r", query, exc_info=True)
        return Product.objects.none()

    queryset = Product.objects.filter(
        is_active=True,
        embedding__model_id=CURRENT_EMBEDDING_MODEL_ID,
    ).select_related("category").prefetch_related("images")

    if category_id is not None:
        queryset = queryset.filter(category_id=category_id)

    return queryset.annotate(
        distance=CosineDistance("embedding__embedding", query_vector)
    ).order_by("distance")[:limit]
