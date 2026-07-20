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


GET_TOP_SELLING_PRODUCTS_DEFINITION = {
    "name": "get_top_selling_products",
    "description": (
        "Get this seller's best-selling products, ranked by total units sold. "
        "Optionally filtered to a recent time window."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of products to return. Default 5.",
                "default": 5,
            },
            "days": {
                "type": "integer",
                "description": "Look back this many days. Omit for all-time.",
            },
        },
    },
}


def get_top_selling_products(seller, limit=5, days=None):
    qs = OrderItem.objects.filter(seller=seller)
    if days:
        since = timezone.now() - timedelta(days=days)
        qs = qs.filter(order__created_at__gte=since)

    results = (
        qs.values("product_id", "product__name")
        .annotate(
            total_quantity_sold=Sum("quantity"),
            total_revenue=Sum(F("price_at_purchase") * F("quantity")),
        )
        .order_by("-total_quantity_sold")[:limit]
    )
    return [
        {
            "product_id": r["product_id"],
            "name": r["product__name"],
            "total_quantity_sold": r["total_quantity_sold"],
            "total_revenue": str(r["total_revenue"]),
        }
        for r in results
    ]


GET_CATEGORY_BREAKDOWN_DEFINITION = {
    "name": "get_category_breakdown",
    "description": (
        "Get this seller's sales broken down by product category - units sold and "
        "revenue per category. Optionally filtered to a recent time window. Use this "
        "for questions like 'which category sells best for me'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Look back this many days. Omit for all-time.",
            }
        },
    },
}


def get_category_breakdown(seller, days=None):
    qs = OrderItem.objects.filter(seller=seller)
    if days:
        since = timezone.now() - timedelta(days=days)
        qs = qs.filter(order__created_at__gte=since)

    results = (
        qs.values("product__category__name")
        .annotate(
            total_quantity_sold=Sum("quantity"),
            total_revenue=Sum(F("price_at_purchase") * F("quantity")),
        )
        .order_by("-total_revenue")
    )
    return [
        {
            "category": r["product__category__name"],
            "total_quantity_sold": r["total_quantity_sold"],
            "total_revenue": str(r["total_revenue"]),
        }
        for r in results
    ]


GET_RECENT_ORDERS_DEFINITION = {
    "name": "get_recent_orders",
    "description": (
        "Get this seller's most recent orders. Each entry is one of this seller's "
        "product line items sold as part of an order, with that order's status and "
        "date. Use this for questions like 'what did I just sell' or 'show me my "
        "recent orders', as opposed to aggregate sales stats."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of order line items to return, most recent first. Default 10.",
                "default": 10,
            }
        },
    },
}


def get_recent_orders(seller, limit=10):
    items = (
        OrderItem.objects.filter(seller=seller)
        .select_related("order", "product")
        .order_by("-order__created_at")[:limit]
    )
    return [
        {
            "order_id": item.order_id,
            "product_id": item.product_id,
            "product_name": item.product.name,
            "quantity": item.quantity,
            "price_at_purchase": str(item.price_at_purchase),
            "status": item.order.status,
            "created_at": item.order.created_at.isoformat(),
        }
        for item in items
    ]


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


GET_LOWEST_STOCK_PRODUCTS_DEFINITION = {
    "name": "get_lowest_stock_products",
    "description": (
        "Get this seller's products with the least stock, ranked lowest first, "
        "regardless of any threshold. Use this for questions like 'what's my "
        "lowest stock item' - use get_low_stock_products instead for questions "
        "like 'what's below 10 units'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "How many products to return, lowest stock first. Default 5.",
                "default": 5,
            }
        },
    },
}


def get_lowest_stock_products(seller, limit=5):
    products = Product.objects.filter(
        seller=seller,
        is_active=True,
    ).order_by("stock").values("id", "name", "stock", "price")[:limit]
    return list(products)


FIND_PRODUCT_BY_NAME_DEFINITION = {
    "name": "find_product_by_name",
    "description": (
        "Search this seller's own products by name to find a product's ID. "
        "Use this whenever the seller refers to a product by name rather than by "
        "ID - sellers don't track IDs, so this is usually the first step before "
        "any action that requires a product_id (generating a description, or "
        "proposing a stock/price update)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The product name, or part of it, to search for.",
            }
        },
        "required": ["name"],
    },
}


def find_product_by_name(seller, name):
    products = Product.objects.filter(
        seller=seller,
        name__icontains=name,
    ).order_by("name").values("id", "name", "stock", "price")[:10]
    return list(products)


GET_PRODUCT_PERFORMANCE_DEFINITION = {
    "name": "get_product_performance",
    "description": (
        "Get a single product's performance: current stock, price, active status, "
        "total units sold, and total revenue generated. Use this when the seller "
        "asks how a specific product is doing - resolve the product's ID with "
        "find_product_by_name first if you only have its name."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "product_id": {
                "type": "integer",
                "description": "The ID of the product to look up.",
            }
        },
        "required": ["product_id"],
    },
}


def get_product_performance(seller, product_id):
    product = Product.objects.get(id=product_id, seller=seller)
    stats = OrderItem.objects.filter(seller=seller, product_id=product_id).aggregate(
        total_quantity_sold=Sum("quantity"),
        total_revenue=Sum(F("price_at_purchase") * F("quantity")),
    )
    return {
        "product_id": product.id,
        "name": product.name,
        "is_active": product.is_active,
        "current_stock": product.stock,
        "price": str(product.price),
        "total_quantity_sold": stats["total_quantity_sold"] or 0,
        "total_revenue": str(stats["total_revenue"] or 0),
    }


GET_STOCK_FORECAST_DEFINITION = {
    "name": "get_stock_forecast",
    "description": (
        "Estimate how many days until a product runs out of stock, based on its "
        "recent sales velocity. Use this for questions like 'when will X run out' or "
        "'do I need to restock X soon' - resolve the product's ID with "
        "find_product_by_name first if you only have its name."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "product_id": {
                "type": "integer",
                "description": "The ID of the product to forecast.",
            },
            "days": {
                "type": "integer",
                "description": "How many days of recent sales history to base the estimate on. Default 30.",
                "default": 30,
            },
        },
        "required": ["product_id"],
    },
}


def get_stock_forecast(seller, product_id, days=30):
    product = Product.objects.get(id=product_id, seller=seller)
    since = timezone.now() - timedelta(days=days)
    units_sold = OrderItem.objects.filter(
        seller=seller, product_id=product_id, order__created_at__gte=since,
    ).aggregate(total=Sum("quantity"))["total"] or 0

    average_daily_sales = units_sold / days
    days_remaining = round(product.stock / average_daily_sales, 1) if average_daily_sales > 0 else None

    return {
        "product_id": product.id,
        "name": product.name,
        "current_stock": product.stock,
        "units_sold_recently": units_sold,
        "window_days": days,
        "average_daily_sales": round(average_daily_sales, 2),
        "estimated_days_of_stock_remaining": days_remaining,
    }


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
    GET_TOP_SELLING_PRODUCTS_DEFINITION,
    GET_CATEGORY_BREAKDOWN_DEFINITION,
    GET_RECENT_ORDERS_DEFINITION,
    GET_LOW_STOCK_DEFINITION,
    GET_LOWEST_STOCK_PRODUCTS_DEFINITION,
    FIND_PRODUCT_BY_NAME_DEFINITION,
    GET_PRODUCT_PERFORMANCE_DEFINITION,
    GET_STOCK_FORECAST_DEFINITION,
    GENERATE_DESCRIPTION_DEFINITION,
    SEARCH_SIMILAR_DEFINITION,
]

SELLER_TOOL_EXECUTORS = {
    "get_seller_stats": get_seller_stats,
    "get_top_selling_products": get_top_selling_products,
    "get_category_breakdown": get_category_breakdown,
    "get_recent_orders": get_recent_orders,
    "get_low_stock_products": get_low_stock_products,
    "get_lowest_stock_products": get_lowest_stock_products,
    "find_product_by_name": find_product_by_name,
    "get_product_performance": get_product_performance,
    "get_stock_forecast": get_stock_forecast,
    "generate_product_description": generate_product_description,
    "search_similar_products": search_similar_products,
}
