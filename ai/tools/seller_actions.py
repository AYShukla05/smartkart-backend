from decimal import Decimal, InvalidOperation

from products.models import Product

# Names of tools that PROPOSE a change rather than performing it. The
# tool-calling loop treats these specially: it collects every structured
# proposal made during the turn (the model can chain more calls afterwards,
# e.g. to propose a second change). The model can request as many proposals
# as it wants; only a seller's explicit confirmation (via
# ConfirmSellerActionView, never through the LLM loop) executes any of them.
PROPOSAL_TOOL_NAMES = frozenset({
    "propose_stock_update",
    "propose_price_update",
    "propose_toggle_product_active",
    "propose_create_product",
})

PROPOSE_STOCK_UPDATE_DEFINITION = {
    "name": "propose_stock_update",
    "description": (
        "Propose updating a product's stock count. This does NOT change anything yet - "
        "the seller must explicitly confirm the proposal in the UI before it takes effect. "
        "Use this whenever the seller asks to change, update, restock, or set stock for a "
        "specific product. Never claim the stock has already been updated."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "product_id": {
                "type": "integer",
                "description": "The ID of the product to update.",
            },
            "new_stock": {
                "type": "integer",
                "description": "The new stock count to propose.",
            },
        },
        "required": ["product_id", "new_stock"],
    },
}

PROPOSE_PRICE_UPDATE_DEFINITION = {
    "name": "propose_price_update",
    "description": (
        "Propose updating a product's price. This does NOT change anything yet - the "
        "seller must explicitly confirm the proposal in the UI before it takes effect. "
        "Use this whenever the seller asks to change, update, or set the price for a "
        "specific product. Never claim the price has already been updated."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "product_id": {
                "type": "integer",
                "description": "The ID of the product to update.",
            },
            "new_price": {
                "type": "string",
                "description": "The new price to propose, e.g. '24.99'.",
            },
        },
        "required": ["product_id", "new_price"],
    },
}


PROPOSE_TOGGLE_PRODUCT_ACTIVE_DEFINITION = {
    "name": "propose_toggle_product_active",
    "description": (
        "Propose activating or deactivating one of this seller's product listings. "
        "A deactivated product is hidden from buyers but not deleted, and can be "
        "reactivated later. This does NOT change anything yet - the seller must "
        "explicitly confirm the proposal in the UI before it takes effect. Use this "
        "when the seller asks to deactivate, hide, pause, delist, reactivate, or "
        "relist a specific product. Never claim the change has already been made."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "product_id": {
                "type": "integer",
                "description": "The ID of the product to update.",
            },
            "new_active": {
                "type": "boolean",
                "description": "True to activate/relist the product, false to deactivate/hide it.",
            },
        },
        "required": ["product_id", "new_active"],
    },
}


PROPOSE_CREATE_PRODUCT_DEFINITION = {
    "name": "propose_create_product",
    "description": (
        "Propose creating a brand-new product listing for this seller. This does NOT "
        "create anything yet - the seller must explicitly confirm the proposal in the "
        "UI before the listing goes live. Use this when the seller asks to add, create, "
        "or list a new product. The category must be one of SmartKart's existing "
        "categories (e.g. 'Electronics', 'Home & Kitchen') - if you're not sure it "
        "exists, propose it anyway and relay any error back to the seller."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The new product's name.",
            },
            "category": {
                "type": "string",
                "description": "The name of the category this product belongs to, e.g. 'Electronics'.",
            },
            "price": {
                "type": "string",
                "description": "The product's price, e.g. '29.99'.",
            },
            "stock": {
                "type": "integer",
                "description": "Initial stock count.",
            },
        },
        "required": ["name", "category", "price", "stock"],
    },
}


def _resolve_category(name):
    from categories.models import Category

    cleaned = str(name).strip()
    matches = Category.objects.filter(is_active=True, name__iexact=cleaned)
    if not matches.exists():
        matches = Category.objects.filter(is_active=True, name__icontains=cleaned)

    count = matches.count()
    if count == 0:
        raise ValueError(f"No category found matching '{name}'.")
    if count > 1:
        names = ", ".join(matches.order_by("name").values_list("name", flat=True))
        raise ValueError(f"'{name}' matches multiple categories: {names}. Please specify one exactly.")
    return matches.first()


def propose_stock_update(seller, product_id, new_stock):
    # seller=seller filter is what prevents proposing changes to another seller's product
    product = Product.objects.get(id=product_id, seller=seller)
    new_stock = int(new_stock)
    if new_stock < 0:
        raise ValueError("Stock cannot be negative.")

    return {
        "action": "update_product_stock",
        "product_id": product.id,
        "product_name": product.name,
        "field": "stock",
        "current_value": product.stock,
        "new_value": new_stock,
    }


def propose_price_update(seller, product_id, new_price):
    product = Product.objects.get(id=product_id, seller=seller)
    try:
        new_price_decimal = Decimal(str(new_price))
    except InvalidOperation:
        raise ValueError(f"'{new_price}' is not a valid price.")
    if new_price_decimal <= 0:
        raise ValueError("Price must be greater than zero.")

    return {
        "action": "update_product_price",
        "product_id": product.id,
        "product_name": product.name,
        "field": "price",
        "current_value": str(product.price),
        "new_value": str(new_price_decimal),
    }


def propose_toggle_product_active(seller, product_id, new_active):
    product = Product.objects.get(id=product_id, seller=seller)
    new_active = bool(new_active)

    return {
        "action": "toggle_product_active",
        "product_id": product.id,
        "product_name": product.name,
        "field": "status",
        "current_value": "active" if product.is_active else "inactive",
        "new_value": "active" if new_active else "inactive",
    }


def propose_create_product(seller, name, category, price, stock):
    from products.serializers import ProductCreateUpdateSerializer

    category_obj = _resolve_category(category)
    serializer = ProductCreateUpdateSerializer(data={
        "name": name, "category": category_obj.id, "price": price, "stock": stock,
    })
    if not serializer.is_valid():
        raise ValueError("; ".join(f"{field}: {errors[0]}" for field, errors in serializer.errors.items()))

    validated = serializer.validated_data
    return {
        "action": "create_product",
        "product_id": None,
        "product_name": validated["name"],
        "summary": f"{category_obj.name} · ${validated['price']} · {validated['stock']} in stock",
        "category_id": category_obj.id,
        "price": str(validated["price"]),
        "stock": validated["stock"],
    }


def _parse_active_value(new_value):
    if isinstance(new_value, bool):
        return new_value
    normalized = str(new_value).strip().lower()
    if normalized in ("active", "true", "1"):
        return True
    if normalized in ("inactive", "false", "0"):
        return False
    raise ValueError(f"'{new_value}' is not a valid status.")


def execute_stock_update(seller, product_id, new_value):
    """Only ever called from ConfirmSellerActionView - never reachable from the LLM loop.

    Re-derives current state and re-checks ownership itself rather than trusting
    whatever the earlier proposal (or the client echoing it back) claimed, since
    stock may have changed between the proposal and the seller's confirmation.
    """
    product = Product.objects.get(id=product_id, seller=seller)
    new_stock = int(new_value)
    if new_stock < 0:
        raise ValueError("Stock cannot be negative.")

    product.stock = new_stock
    product.save(update_fields=["stock"])
    return {"product_id": product.id, "product_name": product.name, "field": "stock", "new_value": product.stock}


def execute_price_update(seller, product_id, new_value):
    """Only ever called from ConfirmSellerActionView - never reachable from the LLM loop."""
    product = Product.objects.get(id=product_id, seller=seller)
    try:
        new_price_decimal = Decimal(str(new_value))
    except InvalidOperation:
        raise ValueError(f"'{new_value}' is not a valid price.")
    if new_price_decimal <= 0:
        raise ValueError("Price must be greater than zero.")

    product.price = new_price_decimal
    product.save(update_fields=["price"])
    return {"product_id": product.id, "product_name": product.name, "field": "price", "new_value": str(product.price)}


def execute_toggle_product_active(seller, product_id, new_value):
    """Only ever called from ConfirmSellerActionView - never reachable from the LLM loop."""
    product = Product.objects.get(id=product_id, seller=seller)
    new_active = _parse_active_value(new_value)

    product.is_active = new_active
    product.save(update_fields=["is_active"])
    return {
        "product_id": product.id,
        "product_name": product.name,
        "field": "status",
        "new_value": "active" if new_active else "inactive",
    }


def execute_create_product(seller, name, category_id, price, stock):
    """Only ever called from ConfirmSellerActionView - never reachable from the LLM loop.

    Re-validates from scratch (not just re-saving the proposal's numbers) since the
    category could have been deactivated between the proposal and confirmation.
    """
    from products.serializers import ProductCreateUpdateSerializer

    serializer = ProductCreateUpdateSerializer(data={
        "name": name, "category": category_id, "price": price, "stock": stock,
    })
    if not serializer.is_valid():
        raise ValueError("; ".join(f"{field}: {errors[0]}" for field, errors in serializer.errors.items()))

    product = serializer.save(seller=seller)
    return {
        "product_id": product.id,
        "product_name": product.name,
        "field": "listing",
        "new_value": "created",
    }


SELLER_ACTION_DEFINITIONS = [
    PROPOSE_STOCK_UPDATE_DEFINITION,
    PROPOSE_PRICE_UPDATE_DEFINITION,
    PROPOSE_TOGGLE_PRODUCT_ACTIVE_DEFINITION,
    PROPOSE_CREATE_PRODUCT_DEFINITION,
]

SELLER_ACTION_PROPOSE_EXECUTORS = {
    "propose_stock_update": propose_stock_update,
    "propose_price_update": propose_price_update,
    "propose_toggle_product_active": propose_toggle_product_active,
    "propose_create_product": propose_create_product,
}

SELLER_ACTION_CONFIRM_EXECUTORS = {
    "update_product_stock": execute_stock_update,
    "update_product_price": execute_price_update,
    "toggle_product_active": execute_toggle_product_active,
    "create_product": execute_create_product,
}
