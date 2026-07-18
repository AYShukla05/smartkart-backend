from datetime import timedelta

from django.db.models import Count, F, Sum
from django.utils import timezone

from orders.models import OrderItem
from products.models import Product

GET_SELLER_STATS_DEFINITION = {
    "name": "get_seller_stats",
    "description": (
        "Get this seller's total orders, total revenue, and product count. "
        "Optionally filtered to a recent time window."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Look back this many days. Omit for all-time stats.",
            }
        },
    },
}


def get_seller_stats(seller, days=None):
    qs = OrderItem.objects.filter(seller=seller)
    if days:
        since = timezone.now() - timedelta(days=days)
        qs = qs.filter(order__created_at__gte=since)

    stats = qs.aggregate(
        total_orders=Count("order", distinct=True),
        total_revenue=Sum(F("price_at_purchase") * F("quantity")),
    )
    total_products = Product.objects.filter(seller=seller).count()

    return {
        "total_orders": stats["total_orders"] or 0,
        "total_revenue": str(stats["total_revenue"] or 0),
        "total_products": total_products,
        "period": f"last {days} days" if days else "all time",
    }


GET_LOW_STOCK_DEFINITION = {
    "name": "get_low_stock_products",
    "description": "Get this seller's products that are low in stock or out of stock.",
    "input_schema": {
        "type": "object",
        "properties": {
            "threshold": {
                "type": "integer",
                "description": "Products at or below this stock count are returned. Default 10.",
                "default": 10,
            }
        },
    },
}


def get_low_stock_products(seller, threshold=10):
    products = Product.objects.filter(
        seller=seller,
        stock__lte=threshold,
        is_active=True,
    ).values("id", "name", "stock", "price")
    return list(products)


GENERATE_DESCRIPTION_DEFINITION = {
    "name": "generate_product_description",
    "description": (
        "Generate an AI-written product description for one of this seller's products. "
        "Returns the generated title, bullets, keywords, and description."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "product_id": {
                "type": "integer",
                "description": "The ID of the product to generate a description for.",
            }
        },
        "required": ["product_id"],
    },
}


def generate_product_description(seller, product_id):
    # seller=seller filter is what prevents cross-seller access
    product = Product.objects.get(id=product_id, seller=seller)

    from ai.parsers import parse_description_json
    from ai.prompts import DESCRIPTION_SYSTEM_PROMPT, build_description_prompt
    from ai.services.llm_client import generate

    prompt = build_description_prompt(
        name=product.name,
        category=product.category.name,
        price=str(product.price),
    )
    raw = generate(prompt=prompt, system=DESCRIPTION_SYSTEM_PROMPT)
    return parse_description_json(raw)


SEARCH_SIMILAR_DEFINITION = {
    "name": "search_similar_products",
    "description": (
        "Search the full product catalog for listings similar to a query. "
        "Useful for finding competing or complementary products on the platform."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language description of what to search for.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return. Default 5.",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}


def search_similar_products(seller, query, limit=5):
    # Not seller-scoped by design - searches the full public catalog.
    from ai.services.search import semantic_search

    queryset, is_fallback, confident_count = semantic_search(query=query)
    results = queryset.values("id", "name", "category__name", "price")[:limit]
    return {
        "results": list(results),
        "is_fallback": is_fallback,
        "confident_count": confident_count,
    }


SELLER_TOOL_DEFINITIONS = [
    GET_SELLER_STATS_DEFINITION,
    GET_LOW_STOCK_DEFINITION,
    GENERATE_DESCRIPTION_DEFINITION,
    SEARCH_SIMILAR_DEFINITION,
]

SELLER_TOOL_EXECUTORS = {
    "get_seller_stats": get_seller_stats,
    "get_low_stock_products": get_low_stock_products,
    "generate_product_description": generate_product_description,
    "search_similar_products": search_similar_products,
}
