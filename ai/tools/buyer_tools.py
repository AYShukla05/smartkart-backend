from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from ai.services.search import semantic_search
from currency.services import convert, get_rates
from orders.models import Order

GET_MY_ORDERS_DEFINITION = {
    "name": "get_my_orders",
    "description": (
        "Get this buyer's order history. "
        "Optionally filter by number of days back or by order status."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Look back this many days. Omit for all orders.",
            },
            "status": {
                "type": "string",
                "description": "Filter by status: 'PLACED' or 'CANCELLED'. Omit for all statuses.",
                "enum": ["PLACED", "CANCELLED"],
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of orders to return. Default 10.",
                "default": 10,
            },
        },
    },
}


def get_my_orders(user, days=None, status=None, limit=10):
    qs = Order.objects.filter(buyer=user).prefetch_related(
        "items", "items__product"
    ).order_by("-created_at")

    if days:
        since = timezone.now() - timedelta(days=days)
        qs = qs.filter(created_at__gte=since)
    if status:
        qs = qs.filter(status=status)

    orders = qs[:limit]
    return [
        {
            "id": o.id,
            "status": o.status,
            "created_at": o.created_at.isoformat(),
            "items": [
                {
                    "product_name": item.product.name,
                    "quantity": item.quantity,
                    "price_at_purchase": str(item.price_at_purchase),
                    "currency": item.currency,
                }
                for item in o.items.all()
            ],
            "total": str(o.total_amount),
            "total_currency": "INR",  # order total is always the platform's INR settlement figure
        }
        for o in orders
    ]


GET_ORDER_DETAIL_DEFINITION = {
    "name": "get_order_detail",
    "description": "Get full detail on a specific order including all line items.",
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "integer",
                "description": "The ID of the order to retrieve.",
            }
        },
        "required": ["order_id"],
    },
}


def get_order_detail(user, order_id):
    # buyer=user filter is what prevents cross-buyer access
    order = Order.objects.prefetch_related(
        "items", "items__product"
    ).get(id=order_id, buyer=user)

    return {
        "id": order.id,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
        "items": [
            {
                "product_id": item.product.id,
                "product_name": item.product.name,
                "quantity": item.quantity,
                "price_at_purchase": str(item.price_at_purchase),
                "currency": item.currency,
                "seller": item.seller.email,
            }
            for item in order.items.all()
        ],
        "total": str(order.total_amount),
        "total_currency": "INR",  # order total is always the platform's INR settlement figure
    }


SEARCH_PRODUCTS_DEFINITION = {
    "name": "search_products",
    "description": (
        "Search the product catalog using natural language. "
        "Use this for product recommendations, finding items by description, "
        "or answering questions like 'do you have anything for hiking?'"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language description of what to search for.",
            },
            "category_id": {
                "type": "integer",
                "description": "Optional category ID to filter results.",
            },
        },
        "required": ["query"],
    },
}


def search_products(user, query, category_id=None):
    # Not buyer-scoped by design - searches the full public catalog, which
    # can span multiple sellers/currencies, so currency is per-row here.
    queryset, is_fallback, confident_count = semantic_search(
        query=query,
        category_id=category_id,
    )
    results = queryset.values(
        "id", "name", "price", "category__name", currency=F("seller__currency")
    )[:10]
    return {
        "results": list(results),
        "is_fallback": is_fallback,
        "confident_count": confident_count,
    }


CONVERT_PRICE_DEFINITION = {
    "name": "convert_price",
    "description": (
        "Convert an amount from one currency to another for display purposes "
        "only. SmartKart sellers each price in their own currency (not always "
        "INR), so always pass the currency value returned by search_products, "
        "get_order_detail, or get_my_orders as source_currency - never assume "
        "INR. This never affects checkout or stored prices."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "amount": {
                "type": "number",
                "description": "The amount to convert.",
            },
            "source_currency": {
                "type": "string",
                "description": "3-letter ISO code the amount is currently in.",
            },
            "target_currency": {
                "type": "string",
                "description": "3-letter ISO code to convert into, e.g. USD, EUR, GBP.",
            },
        },
        "required": ["amount", "source_currency", "target_currency"],
    },
}


def convert_price(user, amount, source_currency, target_currency):
    # Not buyer-scoped by design - carries no user data.
    rates = get_rates()
    try:
        converted = convert(amount, source_currency, target_currency, rates=rates)
    except ValueError as e:
        return {"error": str(e), "supported_currencies": sorted(rates["rates"].keys())}

    return {
        "amount": amount,
        "source_currency": source_currency.upper(),
        "target_currency": target_currency.upper(),
        "converted_amount": str(converted),
        "is_fallback_rate": rates["is_fallback"],
    }


BUYER_TOOL_DEFINITIONS = [
    GET_MY_ORDERS_DEFINITION,
    GET_ORDER_DETAIL_DEFINITION,
    SEARCH_PRODUCTS_DEFINITION,
    CONVERT_PRICE_DEFINITION,
]

BUYER_TOOL_EXECUTORS = {
    "get_my_orders": get_my_orders,
    "get_order_detail": get_order_detail,
    "search_products": search_products,
    "convert_price": convert_price,
}
