import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from categories.models import Category
from orders.models import Order, OrderItem
from products.models import Product
from users.models import User
from ai.models import Conversation, Message, ProductEmbedding
from ai.parsers import DescriptionStreamParser, parse_description_json
from ai.prompts import build_description_prompt
from ai.services.context import build_message_history
from ai.services.llm_client import CURRENT_EMBEDDING_MODEL_ID, LLMGenerationError
from ai.services.search import MINIMUM_FLOOR, RELEVANCE_THRESHOLD, _select_candidates, semantic_search
from ai.services.tool_runner import GENERATION_FAILED_MESSAGE, MAX_TOOL_CALLS_REACHED_MESSAGE, run_with_tools
from ai.tools.buyer_tools import get_my_orders, get_order_detail
from ai.tools.seller_actions import execute_create_product, propose_create_product
from ai.tools.seller_tools import (
    find_product_by_name,
    generate_product_description,
    get_category_breakdown,
    get_low_stock_products,
    get_lowest_stock_products,
    get_product_performance,
    get_recent_orders,
    get_stock_forecast,
    get_top_selling_products,
)


def _tool_use_block(name, input_, block_id="tool_1"):
    return SimpleNamespace(type="tool_use", name=name, input=input_, id=block_id)


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_response(blocks):
    return SimpleNamespace(content=blocks)


def _fake_tokens():
    """A realistic token stream: the "description" marker deliberately
    split across two tokens (`desc` + `ription": "`), matching how a real
    provider might chunk text."""
    return [
        '{"title": "Cool Mug", "bullets": ["Keeps drinks hot", "Dishwasher safe"], '
        '"seo_keywords": ["mug", "insulated"], "',
        "desc",
        'ription": "',
        "Keeps your drinks hot for hours.",
        '"}',
    ]


class PromptBuildingTests(TestCase):
    def test_prompt_includes_request_fields(self):
        prompt = build_description_prompt("Steel Mug", "Kitchen", "499")
        self.assertIn("Steel Mug", prompt)
        self.assertIn("Kitchen", prompt)
        self.assertIn("499", prompt)

    def test_prompt_omits_additional_details_section_when_blank(self):
        prompt = build_description_prompt("Steel Mug", "Kitchen", "499")
        self.assertNotIn("Additional details", prompt)

    def test_prompt_includes_additional_details_when_given(self):
        prompt = build_description_prompt(
            "Steel Mug", "Kitchen", "499", additional_details="Double-walled, BPA-free"
        )
        self.assertIn("Double-walled, BPA-free", prompt)


class ParserTests(TestCase):
    def test_extracts_fields_from_complete_json(self):
        raw = json.dumps({
            "title": "Cool Mug",
            "bullets": ["a", "b"],
            "seo_keywords": ["mug"],
            "description": "Great mug.",
        })
        result = parse_description_json(raw)
        self.assertEqual(result["title"], "Cool Mug")
        self.assertEqual(result["bullets"], ["a", "b"])
        self.assertEqual(result["seo_keywords"], ["mug"])
        self.assertEqual(result["description"], "Great mug.")

    def test_handles_markdown_fenced_json_without_crashing(self):
        fenced = "```json\n" + json.dumps({
            "title": "Cool Mug",
            "bullets": ["a"],
            "seo_keywords": ["mug"],
            "description": "Great mug.",
        }) + "\n```"
        result = parse_description_json(fenced)
        self.assertEqual(result["title"], "Cool Mug")
        self.assertEqual(result["description"], "Great mug.")

    def test_handles_truncated_json_without_crashing(self):
        truncated = (
            '{"title": "Cool Mug", "bullets": ["a"], '
            '"seo_keywords": ["mug"], "description": "Great mug tha'
        )
        result = parse_description_json(truncated)
        self.assertEqual(result["title"], "Cool Mug")
        self.assertEqual(result["description"], "Great mug tha")

    def test_stream_parser_only_emits_description_and_handles_split_marker(self):
        parser = DescriptionStreamParser()
        emitted = "".join(parser.feed(t) for t in _fake_tokens())

        # title/bullets/seo_keywords JSON scaffolding must never leak into
        # the live-streamed text - only the description value does.
        self.assertNotIn("Cool Mug", emitted)
        self.assertIn("Keeps your drinks hot for hours.", emitted)

        result = parser.finalize()
        self.assertEqual(result["title"], "Cool Mug")
        self.assertEqual(result["bullets"], ["Keeps drinks hot", "Dishwasher safe"])
        self.assertEqual(result["seo_keywords"], ["mug", "insulated"])
        self.assertEqual(result["description"], "Keeps your drinks hot for hours.")


class GenerateDescriptionViewTests(TestCase):
    def setUp(self):
        # Throttle counters live in Django's cache backend, which (unlike the
        # DB) isn't rolled back between test methods - without this, request
        # counts from earlier tests leak into this one via the shared user.
        cache.clear()
        self.client = APIClient()
        self.seller = User.objects.create_user(
            email="seller@test.com", password="testpass123", role="SELLER"
        )
        self.buyer = User.objects.create_user(
            email="buyer@test.com", password="testpass123", role="BUYER"
        )
        self.url = "/api/ai/generate-description/"
        self.payload = {"name": "Steel Mug", "category": "Kitchen", "price": "499"}

    def _auth_as(self, user):
        token = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

    def _consume(self, response):
        return b"".join(response.streaming_content).decode()

    def test_unauthenticated_request_returns_401(self):
        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 401)

    def test_buyer_request_returns_403(self):
        self._auth_as(self.buyer)
        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 403)

    def test_missing_fields_returns_400(self):
        self._auth_as(self.seller)
        response = self.client.post(self.url, {"name": "Steel Mug"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_additional_details_over_length_limit_returns_400(self):
        self._auth_as(self.seller)
        payload = {**self.payload, "additional_details": "x" * 501}
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 400)

    @patch("ai.views.stream")
    def test_additional_details_are_forwarded_into_the_prompt(self, mock_stream):
        mock_stream.return_value = iter(_fake_tokens())
        self._auth_as(self.seller)

        payload = {**self.payload, "additional_details": "Double-walled, BPA-free"}
        response = self.client.post(self.url, payload, format="json")
        self._consume(response)

        prompt_arg = mock_stream.call_args.args[0]
        self.assertIn("Double-walled, BPA-free", prompt_arg)

    @patch("ai.views.stream")
    def test_sse_stream_formats_chunks_and_sends_result_then_done(self, mock_stream):
        mock_stream.return_value = iter(_fake_tokens())
        self._auth_as(self.seller)

        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")

        body = self._consume(response)
        lines = [line for line in body.split("\n\n") if line]

        for line in lines:
            self.assertTrue(line.startswith("data: "), f"malformed SSE line: {line!r}")

        self.assertEqual(lines[-1], "data: [DONE]")

        result_line = next(l for l in lines if l.startswith("data: [RESULT]"))
        result = json.loads(result_line[len("data: [RESULT]"):])
        self.assertEqual(result["title"], "Cool Mug")
        self.assertEqual(result["seo_keywords"], ["mug", "insulated"])

    @patch("ai.views.stream")
    def test_llm_generation_error_mid_stream_does_not_500(self, mock_stream):
        def raising_stream(*args, **kwargs):
            yield '{"title": "Cool Mug", "'
            raise LLMGenerationError("boom")

        mock_stream.return_value = raising_stream()
        self._auth_as(self.seller)

        response = self.client.post(self.url, self.payload, format="json")
        # Headers are already committed once StreamingHttpResponse starts,
        # so a mid-stream failure can't become an HTTP 500 - it has to be
        # signaled inside the SSE body instead.
        self.assertEqual(response.status_code, 200)

        body = self._consume(response)
        self.assertIn("data: [ERROR]", body)
        self.assertTrue(body.rstrip().endswith("data: [DONE]"))

    @patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"ai_generate": "1/minute"})
    @patch("ai.views.stream")
    def test_throttle_returns_429_when_scope_limit_exceeded(self, mock_stream):
        # GenerateDescriptionView sets `throttle_classes` directly on the class
        # (matching authentication/views.py's "auth" scope convention), so it
        # isn't affected by settings.py's test-time wipe of DEFAULT_THROTTLE_CLASSES.
        #
        # override_settings(REST_FRAMEWORK=...) would NOT work to lower the rate
        # here though: DRF's SimpleRateThrottle.THROTTLE_RATES is a plain class
        # attribute snapshotted from api_settings.DEFAULT_THROTTLE_RATES once,
        # at import time (rest_framework/throttling.py:66) - replacing
        # settings.REST_FRAMEWORK later doesn't touch that already-bound dict
        # reference. patch.dict mutates the actual dict object in place instead.
        mock_stream.return_value = iter(_fake_tokens())
        self._auth_as(self.seller)

        first = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(first.status_code, 200)
        self._consume(first)

        second = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(second.status_code, 429)


class SelectCandidatesTests(TestCase):
    """Pure boundary tests for the floor/threshold/ceiling selection rule -
    the ranking itself needs Postgres, this arithmetic doesn't."""

    def test_all_pass_threshold_above_floor_returns_unpadded(self):
        ranked = [(i, 0.01 * i) for i in range(1, 30)]  # 29 items, all under 0.50
        selected_ids, is_fallback, confident_count = _select_candidates(ranked)
        self.assertFalse(is_fallback)
        self.assertEqual(confident_count, 29)
        self.assertEqual(selected_ids, list(range(1, 30)))

    def test_none_pass_threshold_pads_to_floor(self):
        ranked = [(i, 0.9) for i in range(1, 50)]  # all well over 0.50
        selected_ids, is_fallback, confident_count = _select_candidates(ranked)
        self.assertTrue(is_fallback)
        self.assertEqual(confident_count, 0)
        self.assertEqual(selected_ids, list(range(1, MINIMUM_FLOOR + 1)))

    def test_exactly_at_floor_is_not_fallback(self):
        ranked = [(i, 0.1) for i in range(1, MINIMUM_FLOOR + 1)]  # exactly 24 passing
        selected_ids, is_fallback, confident_count = _select_candidates(ranked)
        self.assertFalse(is_fallback)
        self.assertEqual(confident_count, MINIMUM_FLOOR)

    def test_one_below_floor_pads_with_next_closest(self):
        passing = [(i, 0.1) for i in range(1, MINIMUM_FLOOR)]  # 23 passing
        padding = [(100 + i, 0.9) for i in range(5)]  # closest of the rest first
        selected_ids, is_fallback, confident_count = _select_candidates(passing + padding)
        self.assertTrue(is_fallback)
        self.assertEqual(confident_count, 23)
        self.assertEqual(len(selected_ids), MINIMUM_FLOOR)
        self.assertEqual(selected_ids[-1], 100)

    def test_distance_exactly_at_threshold_counts_as_passing(self):
        selected_ids, is_fallback, confident_count = _select_candidates([(1, RELEVANCE_THRESHOLD)])
        self.assertEqual(confident_count, 1)

    def test_fewer_candidates_than_floor_returns_all_of_them(self):
        ranked = [(i, 0.9) for i in range(1, 6)]  # only 5 total candidates, none pass
        selected_ids, is_fallback, confident_count = _select_candidates(ranked)
        self.assertTrue(is_fallback)
        self.assertEqual(confident_count, 0)
        self.assertEqual(selected_ids, [1, 2, 3, 4, 5])

    def test_empty_ranked_list_returns_empty_fallback(self):
        selected_ids, is_fallback, confident_count = _select_candidates([])
        self.assertEqual(selected_ids, [])
        self.assertTrue(is_fallback)
        self.assertEqual(confident_count, 0)


class SemanticSearchEarlyReturnTests(TestCase):
    """semantic_search()'s early-return paths never touch CosineDistance, so
    unlike the ranking itself, they're safe to test directly against SQLite."""

    def test_no_indexed_embeddings_returns_empty_fallback(self):
        queryset, is_fallback, confident_count = semantic_search("anything")
        self.assertEqual(list(queryset), [])
        self.assertTrue(is_fallback)
        self.assertEqual(confident_count, 0)

    @patch("ai.services.search.embed")
    def test_embedding_failure_returns_empty_fallback(self, mock_embed):
        seller = User.objects.create_user(
            email="seller2@test.com", password="testpass123", role="SELLER"
        )
        category = Category.objects.create(name="Kitchen", slug="kitchen")
        product = Product.objects.create(
            seller=seller, category=category, name="Mug", price=Decimal("10.00"), stock=5,
        )
        ProductEmbedding.objects.create(
            product=product, embedding=[0.0] * 512, model_id=CURRENT_EMBEDDING_MODEL_ID,
        )
        mock_embed.side_effect = LLMGenerationError("boom")

        queryset, is_fallback, confident_count = semantic_search("anything")
        self.assertEqual(list(queryset), [])
        self.assertTrue(is_fallback)
        self.assertEqual(confident_count, 0)


class BackfillDescriptionsCommandTests(TestCase):
    def setUp(self):
        seller = User.objects.create_user(
            email="seller@test.com", password="testpass123", role="SELLER"
        )
        category = Category.objects.create(name="Kitchen", slug="kitchen")
        self.products = [
            Product.objects.create(
                seller=seller,
                category=category,
                name=f"Product {i}",
                description="",
                price=Decimal("10.00"),
                stock=5,
            )
            for i in range(5)
        ]

    def test_dry_run_writes_nothing(self):
        out = StringIO()
        call_command("backfill_descriptions", "--dry-run", stdout=out)

        self.assertIn("Products needing descriptions: 5", out.getvalue())
        self.assertIn("Run with --confirm to proceed.", out.getvalue())
        for product in self.products:
            product.refresh_from_db()
            self.assertEqual(product.description, "")

    @patch("ai.management.commands.backfill_descriptions.time.sleep")
    @patch("ai.management.commands.backfill_descriptions.generate")
    def test_confirm_processes_every_product_across_batches(self, mock_generate, mock_sleep):
        # Regression test: an earlier version paginated with queryset[offset:offset+n]
        # on Product.objects.filter(description=""). Since --confirm writes a
        # description as it goes, that filter's result set shrinks mid-run, so
        # offset-based pagination on a live requery silently skips products into
        # the next batch. Fixed by snapshotting all matching IDs up front.
        mock_generate.return_value = json.dumps({
            "title": "T", "bullets": ["a"], "seo_keywords": ["k"], "description": "Generated.",
        })

        call_command("backfill_descriptions", "--confirm", "--batch-size=2", stdout=StringIO())

        for product in self.products:
            product.refresh_from_db()
            self.assertEqual(product.description, "Generated.")
            self.assertEqual(product.seo_keywords, ["k"])
        self.assertEqual(mock_generate.call_count, 5)

    @patch("ai.management.commands.backfill_descriptions.time.sleep")
    @patch("ai.management.commands.backfill_descriptions.generate")
    def test_single_product_failure_does_not_abort_run(self, mock_generate, mock_sleep):
        from ai.services.llm_client import LLMGenerationError

        def side_effect(*args, **kwargs):
            if mock_generate.call_count == 2:
                raise LLMGenerationError("boom")
            return json.dumps({
                "title": "T", "bullets": ["a"], "seo_keywords": ["k"], "description": "Generated.",
            })

        mock_generate.side_effect = side_effect

        call_command("backfill_descriptions", "--confirm", stdout=StringIO())

        descriptions = [
            Product.objects.get(id=p.id).description for p in self.products
        ]
        self.assertEqual(descriptions.count("Generated."), 4)
        self.assertEqual(descriptions.count(""), 1)


class SellerToolsTests(TestCase):
    """Ownership scoping is a query-level guarantee, not a prompt instruction -
    these test the executors directly, independent of the tool-calling loop."""

    def setUp(self):
        self.seller = User.objects.create_user(
            email="seller@test.com", password="testpass123", role="SELLER"
        )
        self.other_seller = User.objects.create_user(
            email="other-seller@test.com", password="testpass123", role="SELLER"
        )
        self.category = Category.objects.create(name="Kitchen", slug="kitchen")

    def test_get_low_stock_products_excludes_other_sellers_products(self):
        Product.objects.create(
            seller=self.seller, category=self.category, name="Mine Low",
            price=Decimal("10.00"), stock=2,
        )
        Product.objects.create(
            seller=self.other_seller, category=self.category, name="Theirs Low",
            price=Decimal("10.00"), stock=1,
        )

        names = [p["name"] for p in get_low_stock_products(self.seller, threshold=10)]

        self.assertIn("Mine Low", names)
        self.assertNotIn("Theirs Low", names)

    def test_get_low_stock_products_excludes_inactive_products(self):
        Product.objects.create(
            seller=self.seller, category=self.category, name="Inactive Low",
            price=Decimal("10.00"), stock=1, is_active=False,
        )

        names = [p["name"] for p in get_low_stock_products(self.seller, threshold=10)]

        self.assertNotIn("Inactive Low", names)

    def test_generate_product_description_cross_seller_raises_does_not_exist(self):
        other_product = Product.objects.create(
            seller=self.other_seller, category=self.category, name="Theirs",
            price=Decimal("10.00"), stock=5,
        )

        with self.assertRaises(Product.DoesNotExist):
            generate_product_description(self.seller, other_product.id)

    def test_find_product_by_name_matches_partial_case_insensitive(self):
        Product.objects.create(
            seller=self.seller, category=self.category, name="Steel Water Bottle",
            price=Decimal("10.00"), stock=5,
        )

        results = find_product_by_name(self.seller, "water")

        self.assertEqual([r["name"] for r in results], ["Steel Water Bottle"])

    def test_find_product_by_name_excludes_other_sellers_products(self):
        Product.objects.create(
            seller=self.seller, category=self.category, name="Shared Name Widget",
            price=Decimal("10.00"), stock=5,
        )
        Product.objects.create(
            seller=self.other_seller, category=self.category, name="Shared Name Widget",
            price=Decimal("10.00"), stock=5,
        )

        results = find_product_by_name(self.seller, "Shared Name Widget")

        self.assertEqual(len(results), 1)

    def test_find_product_by_name_no_match_returns_empty_list(self):
        results = find_product_by_name(self.seller, "nothing matches this")

        self.assertEqual(results, [])

    def _create_order_item(self, seller, product, buyer, quantity, price, created_at=None):
        order = Order.objects.create(buyer=buyer, total_amount=price * quantity)
        item = OrderItem.objects.create(
            order=order, product=product, seller=seller,
            quantity=quantity, price_at_purchase=price,
        )
        if created_at is not None:
            Order.objects.filter(id=order.id).update(created_at=created_at)
        return item

    def test_get_top_selling_products_ranks_by_quantity_sold(self):
        buyer = User.objects.create_user(email="buyer@test.com", password="testpass123", role="BUYER")
        best = Product.objects.create(
            seller=self.seller, category=self.category, name="Best Seller",
            price=Decimal("10.00"), stock=100,
        )
        worst = Product.objects.create(
            seller=self.seller, category=self.category, name="Slow Mover",
            price=Decimal("10.00"), stock=100,
        )
        self._create_order_item(self.seller, best, buyer, quantity=8, price=Decimal("10.00"))
        self._create_order_item(self.seller, worst, buyer, quantity=1, price=Decimal("10.00"))

        results = get_top_selling_products(self.seller, limit=2)

        self.assertEqual(results[0]["name"], "Best Seller")
        self.assertEqual(results[0]["total_quantity_sold"], 8)
        self.assertEqual(results[1]["name"], "Slow Mover")

    def test_get_top_selling_products_excludes_other_sellers(self):
        buyer = User.objects.create_user(email="buyer@test.com", password="testpass123", role="BUYER")
        mine = Product.objects.create(
            seller=self.seller, category=self.category, name="Mine",
            price=Decimal("10.00"), stock=100,
        )
        theirs = Product.objects.create(
            seller=self.other_seller, category=self.category, name="Theirs",
            price=Decimal("10.00"), stock=100,
        )
        self._create_order_item(self.seller, mine, buyer, quantity=3, price=Decimal("10.00"))
        self._create_order_item(self.other_seller, theirs, buyer, quantity=99, price=Decimal("10.00"))

        names = [r["name"] for r in get_top_selling_products(self.seller)]

        self.assertEqual(names, ["Mine"])

    def test_get_top_selling_products_respects_days_window(self):
        buyer = User.objects.create_user(email="buyer@test.com", password="testpass123", role="BUYER")
        product = Product.objects.create(
            seller=self.seller, category=self.category, name="Old Sale",
            price=Decimal("10.00"), stock=100,
        )
        old_date = timezone.now() - timedelta(days=60)
        self._create_order_item(self.seller, product, buyer, quantity=5, price=Decimal("10.00"), created_at=old_date)

        results = get_top_selling_products(self.seller, days=30)

        self.assertEqual(results, [])

    def test_get_category_breakdown_groups_by_category_and_excludes_other_sellers(self):
        buyer = User.objects.create_user(email="buyer@test.com", password="testpass123", role="BUYER")
        electronics = Category.objects.create(name="Electronics", slug="electronics")
        mine = Product.objects.create(
            seller=self.seller, category=self.category, name="Mine",
            price=Decimal("10.00"), stock=100,
        )
        mine_electronics = Product.objects.create(
            seller=self.seller, category=electronics, name="Mine Electronics",
            price=Decimal("50.00"), stock=100,
        )
        theirs = Product.objects.create(
            seller=self.other_seller, category=self.category, name="Theirs",
            price=Decimal("10.00"), stock=100,
        )
        self._create_order_item(self.seller, mine, buyer, quantity=2, price=Decimal("10.00"))
        self._create_order_item(self.seller, mine_electronics, buyer, quantity=1, price=Decimal("50.00"))
        self._create_order_item(self.other_seller, theirs, buyer, quantity=99, price=Decimal("10.00"))

        results = get_category_breakdown(self.seller)
        by_category = {r["category"]: r for r in results}

        self.assertEqual(set(by_category), {"Kitchen", "Electronics"})
        self.assertEqual(by_category["Kitchen"]["total_quantity_sold"], 2)
        self.assertEqual(by_category["Electronics"]["total_quantity_sold"], 1)

    def test_get_category_breakdown_respects_days_window(self):
        buyer = User.objects.create_user(email="buyer@test.com", password="testpass123", role="BUYER")
        product = Product.objects.create(
            seller=self.seller, category=self.category, name="Old Sale",
            price=Decimal("10.00"), stock=100,
        )
        old_date = timezone.now() - timedelta(days=60)
        self._create_order_item(self.seller, product, buyer, quantity=5, price=Decimal("10.00"), created_at=old_date)

        results = get_category_breakdown(self.seller, days=30)

        self.assertEqual(results, [])

    def test_get_recent_orders_orders_most_recent_first_and_excludes_other_sellers(self):
        buyer = User.objects.create_user(email="buyer@test.com", password="testpass123", role="BUYER")
        older_product = Product.objects.create(
            seller=self.seller, category=self.category, name="Older Sale",
            price=Decimal("10.00"), stock=100,
        )
        newer_product = Product.objects.create(
            seller=self.seller, category=self.category, name="Newer Sale",
            price=Decimal("10.00"), stock=100,
        )
        theirs = Product.objects.create(
            seller=self.other_seller, category=self.category, name="Theirs",
            price=Decimal("10.00"), stock=100,
        )
        self._create_order_item(
            self.seller, older_product, buyer, quantity=1, price=Decimal("10.00"),
            created_at=timezone.now() - timedelta(days=5),
        )
        self._create_order_item(
            self.seller, newer_product, buyer, quantity=2, price=Decimal("10.00"),
            created_at=timezone.now() - timedelta(days=1),
        )
        self._create_order_item(self.other_seller, theirs, buyer, quantity=1, price=Decimal("10.00"))

        results = get_recent_orders(self.seller)

        self.assertEqual([r["product_name"] for r in results], ["Newer Sale", "Older Sale"])

    def test_get_recent_orders_respects_limit(self):
        buyer = User.objects.create_user(email="buyer@test.com", password="testpass123", role="BUYER")
        product = Product.objects.create(
            seller=self.seller, category=self.category, name="Repeat Buy",
            price=Decimal("10.00"), stock=100,
        )
        for _ in range(3):
            self._create_order_item(self.seller, product, buyer, quantity=1, price=Decimal("10.00"))

        results = get_recent_orders(self.seller, limit=2)

        self.assertEqual(len(results), 2)

    def test_get_lowest_stock_products_orders_lowest_first(self):
        Product.objects.create(
            seller=self.seller, category=self.category, name="High Stock",
            price=Decimal("10.00"), stock=50,
        )
        Product.objects.create(
            seller=self.seller, category=self.category, name="Lowest Stock",
            price=Decimal("10.00"), stock=1,
        )
        Product.objects.create(
            seller=self.seller, category=self.category, name="Mid Stock",
            price=Decimal("10.00"), stock=10,
        )

        names = [p["name"] for p in get_lowest_stock_products(self.seller, limit=2)]

        self.assertEqual(names, ["Lowest Stock", "Mid Stock"])

    def test_get_lowest_stock_products_excludes_other_sellers_and_inactive(self):
        Product.objects.create(
            seller=self.seller, category=self.category, name="Inactive",
            price=Decimal("10.00"), stock=0, is_active=False,
        )
        Product.objects.create(
            seller=self.other_seller, category=self.category, name="Theirs",
            price=Decimal("10.00"), stock=0,
        )
        Product.objects.create(
            seller=self.seller, category=self.category, name="Mine",
            price=Decimal("10.00"), stock=5,
        )

        names = [p["name"] for p in get_lowest_stock_products(self.seller)]

        self.assertEqual(names, ["Mine"])

    def test_get_product_performance_aggregates_sales_and_includes_current_state(self):
        buyer = User.objects.create_user(email="buyer@test.com", password="testpass123", role="BUYER")
        product = Product.objects.create(
            seller=self.seller, category=self.category, name="Tracked Product",
            price=Decimal("15.00"), stock=42,
        )
        self._create_order_item(self.seller, product, buyer, quantity=3, price=Decimal("15.00"))
        self._create_order_item(self.seller, product, buyer, quantity=2, price=Decimal("15.00"))

        result = get_product_performance(self.seller, product.id)

        self.assertEqual(result["name"], "Tracked Product")
        self.assertEqual(result["current_stock"], 42)
        self.assertTrue(result["is_active"])
        self.assertEqual(result["total_quantity_sold"], 5)
        self.assertEqual(Decimal(result["total_revenue"]), Decimal("75.00"))

    def test_get_product_performance_with_no_sales_returns_zeroes(self):
        product = Product.objects.create(
            seller=self.seller, category=self.category, name="Never Sold",
            price=Decimal("15.00"), stock=10,
        )

        result = get_product_performance(self.seller, product.id)

        self.assertEqual(result["total_quantity_sold"], 0)
        self.assertEqual(result["total_revenue"], "0")

    def test_get_product_performance_cross_seller_raises_does_not_exist(self):
        other_product = Product.objects.create(
            seller=self.other_seller, category=self.category, name="Theirs",
            price=Decimal("10.00"), stock=5,
        )

        with self.assertRaises(Product.DoesNotExist):
            get_product_performance(self.seller, other_product.id)

    def test_get_stock_forecast_estimates_days_remaining_from_recent_velocity(self):
        buyer = User.objects.create_user(email="buyer@test.com", password="testpass123", role="BUYER")
        product = Product.objects.create(
            seller=self.seller, category=self.category, name="Selling Fast",
            price=Decimal("10.00"), stock=30,
        )
        # 10 units sold over the last 10 days -> 1/day -> 30 days of stock left
        for days_ago in range(10):
            self._create_order_item(
                self.seller, product, buyer, quantity=1, price=Decimal("10.00"),
                created_at=timezone.now() - timedelta(days=days_ago),
            )

        result = get_stock_forecast(self.seller, product.id, days=30)

        self.assertEqual(result["current_stock"], 30)
        self.assertEqual(result["units_sold_recently"], 10)
        self.assertAlmostEqual(result["average_daily_sales"], 10 / 30, places=2)
        self.assertAlmostEqual(result["estimated_days_of_stock_remaining"], 30 / (10 / 30), places=1)

    def test_get_stock_forecast_with_no_recent_sales_returns_none_estimate(self):
        product = Product.objects.create(
            seller=self.seller, category=self.category, name="Never Sold",
            price=Decimal("10.00"), stock=30,
        )

        result = get_stock_forecast(self.seller, product.id)

        self.assertEqual(result["units_sold_recently"], 0)
        self.assertIsNone(result["estimated_days_of_stock_remaining"])

    def test_get_stock_forecast_ignores_sales_outside_window(self):
        buyer = User.objects.create_user(email="buyer@test.com", password="testpass123", role="BUYER")
        product = Product.objects.create(
            seller=self.seller, category=self.category, name="Old Sale Only",
            price=Decimal("10.00"), stock=30,
        )
        self._create_order_item(
            self.seller, product, buyer, quantity=5, price=Decimal("10.00"),
            created_at=timezone.now() - timedelta(days=60),
        )

        result = get_stock_forecast(self.seller, product.id, days=30)

        self.assertEqual(result["units_sold_recently"], 0)
        self.assertIsNone(result["estimated_days_of_stock_remaining"])

    def test_get_stock_forecast_cross_seller_raises_does_not_exist(self):
        other_product = Product.objects.create(
            seller=self.other_seller, category=self.category, name="Theirs",
            price=Decimal("10.00"), stock=5,
        )

        with self.assertRaises(Product.DoesNotExist):
            get_stock_forecast(self.seller, other_product.id)


class SellerActionsTests(TestCase):
    """propose_create_product / execute_create_product get direct unit tests -
    unlike the older propose/execute pairs, category resolution is new,
    non-trivial logic and row creation is higher-stakes than a field update."""

    def setUp(self):
        self.seller = User.objects.create_user(
            email="seller@test.com", password="testpass123", role="SELLER"
        )
        self.category = Category.objects.create(name="Kitchen", slug="kitchen")
        self.inactive_category = Category.objects.create(
            name="Discontinued", slug="discontinued", is_active=False,
        )

    def test_propose_create_product_returns_summary_and_confirm_fields(self):
        result = propose_create_product(self.seller, "Steel Kettle", "Kitchen", "24.99", 10)

        self.assertEqual(result["action"], "create_product")
        self.assertIsNone(result["product_id"])
        self.assertEqual(result["product_name"], "Steel Kettle")
        self.assertEqual(result["category_id"], self.category.id)
        self.assertEqual(result["price"], "24.99")
        self.assertEqual(result["stock"], 10)
        self.assertIn("Kitchen", result["summary"])

    def test_propose_create_product_resolves_category_case_insensitively(self):
        result = propose_create_product(self.seller, "Steel Kettle", "kitchen", "24.99", 10)

        self.assertEqual(result["category_id"], self.category.id)

    def test_propose_create_product_no_matching_category_raises_value_error(self):
        with self.assertRaises(ValueError):
            propose_create_product(self.seller, "Steel Kettle", "Nonexistent Category", "24.99", 10)

    def test_propose_create_product_ambiguous_category_raises_value_error(self):
        Category.objects.create(name="Kitchen Tools", slug="kitchen-tools")

        # "Kitch" exact-matches neither "Kitchen" nor "Kitchen Tools", so
        # resolution falls through to the ambiguous icontains match.
        with self.assertRaises(ValueError):
            propose_create_product(self.seller, "Steel Kettle", "Kitch", "24.99", 10)

    def test_propose_create_product_excludes_inactive_categories(self):
        with self.assertRaises(ValueError):
            propose_create_product(self.seller, "Old Stock", "Discontinued", "24.99", 10)

    def test_propose_create_product_zero_price_raises_value_error(self):
        with self.assertRaises(ValueError):
            propose_create_product(self.seller, "Freebie", "Kitchen", "0", 10)

    def test_propose_create_product_negative_stock_raises_value_error(self):
        with self.assertRaises(ValueError):
            propose_create_product(self.seller, "Steel Kettle", "Kitchen", "24.99", -5)

    def test_execute_create_product_creates_product_owned_by_seller(self):
        result = execute_create_product(
            self.seller, name="Steel Kettle", category_id=self.category.id, price="24.99", stock=10,
        )

        self.assertEqual(result["field"], "listing")
        self.assertEqual(result["new_value"], "created")
        product = Product.objects.get(id=result["product_id"])
        self.assertEqual(product.seller, self.seller)
        self.assertEqual(product.category, self.category)
        self.assertEqual(product.price, Decimal("24.99"))
        self.assertEqual(product.stock, 10)

    def test_execute_create_product_invalid_category_id_raises_value_error(self):
        with self.assertRaises(ValueError):
            execute_create_product(self.seller, name="Steel Kettle", category_id=999999, price="24.99", stock=10)


class RunWithToolsTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            email="seller@test.com", password="testpass123", role="SELLER"
        )

    @patch("ai.services.tool_runner.call_with_tools")
    def test_text_only_response_returns_immediately_without_second_call(self, mock_call):
        mock_call.return_value = _tool_response([_text_block("Hello seller")])

        reply = run_with_tools("hi", "sys", [], {}, self.seller)

        self.assertEqual(reply.text, "Hello seller")
        self.assertEqual(reply.pending_actions, [])
        self.assertEqual(mock_call.call_count, 1)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_tool_call_resolves_to_final_text(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_tool_use_block("get_thing", {"x": 1})]),
            _tool_response([_text_block("done")]),
        ]
        calls = []

        def executor(seller, x):
            calls.append((seller, x))
            return "ok"

        reply = run_with_tools("hi", "sys", [], {"get_thing": executor}, self.seller)

        self.assertEqual(reply.text, "done")
        self.assertEqual(reply.pending_actions, [])
        self.assertEqual(calls, [(self.seller, 1)])

    @patch("ai.services.tool_runner.call_with_tools")
    def test_loop_terminates_at_max_tool_calls(self, mock_call):
        mock_call.side_effect = lambda *a, **k: _tool_response([_tool_use_block("get_thing", {"x": 1})])
        calls = []

        def executor(seller, x):
            calls.append((seller, x))
            return "ok"

        reply = run_with_tools("hi", "sys", [], {"get_thing": executor}, self.seller, max_tool_calls=5)

        self.assertEqual(reply.text, MAX_TOOL_CALLS_REACHED_MESSAGE)
        self.assertEqual(reply.pending_actions, [])
        self.assertEqual(len(calls), 5)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_tool_executor_exception_is_fed_back_as_error_string_not_raised(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_tool_use_block("get_thing", {})]),
            _tool_response([_text_block("recovered")]),
        ]

        def failing_executor(seller):
            raise ValueError("boom")

        reply = run_with_tools("hi", "sys", [], {"get_thing": failing_executor}, self.seller)

        self.assertEqual(reply.text, "recovered")
        second_call_messages = mock_call.call_args_list[1].args[0]
        tool_result_content = second_call_messages[-1]["content"][0]["content"]
        self.assertIn("Error calling get_thing", tool_result_content)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_generation_error_returns_friendly_message_not_500(self, mock_call):
        mock_call.side_effect = LLMGenerationError("boom")

        reply = run_with_tools("hi", "sys", [], {}, self.seller)

        self.assertEqual(reply.text, GENERATION_FAILED_MESSAGE)
        self.assertEqual(reply.pending_actions, [])

    @patch("ai.services.tool_runner.call_with_tools")
    def test_proposal_tool_is_captured_and_surfaced_in_pending_actions(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_tool_use_block("propose_stock_update", {"product_id": 1, "new_stock": 50})]),
            _tool_response([_text_block("Update stock to 50?")]),
        ]

        def propose(seller, product_id, new_stock):
            return {
                "action": "update_product_stock", "product_id": product_id,
                "product_name": "Widget", "field": "stock", "current_value": 10, "new_value": new_stock,
            }

        reply = run_with_tools(
            "update stock", "sys", [], {"propose_stock_update": propose}, self.seller,
            proposal_tool_names={"propose_stock_update"},
        )

        self.assertEqual(reply.text, "Update stock to 50?")
        self.assertEqual(len(reply.pending_actions), 1)
        self.assertEqual(reply.pending_actions[0]["product_id"], 1)
        self.assertEqual(reply.pending_actions[0]["new_value"], 50)
        self.assertEqual(mock_call.call_count, 2)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_model_may_keep_chaining_calls_after_a_proposal(self, mock_call):
        """The whole point of the fix: the loop no longer stops the instant a
        proposal is captured - the model can chain a second proposal, or a
        lookup, afterwards and both still surface."""
        mock_call.side_effect = [
            _tool_response([_tool_use_block("propose_stock_update", {"product_id": 1, "new_stock": 50}, block_id="t1")]),
            _tool_response([_tool_use_block("get_seller_stats", {}, block_id="t2")]),
            _tool_response([_text_block("Updated stock, and here are your stats.")]),
        ]

        def propose(seller, product_id, new_stock):
            return {
                "action": "update_product_stock", "product_id": product_id,
                "product_name": "Widget", "field": "stock", "current_value": 10, "new_value": new_stock,
            }

        stats_calls = []

        def get_stats(seller):
            stats_calls.append(seller)
            return {"total_orders": 3}

        reply = run_with_tools(
            "update stock then tell me my stats", "sys", [],
            {"propose_stock_update": propose, "get_seller_stats": get_stats}, self.seller,
            proposal_tool_names={"propose_stock_update"},
        )

        self.assertEqual(reply.text, "Updated stock, and here are your stats.")
        self.assertEqual(len(reply.pending_actions), 1)
        self.assertEqual(stats_calls, [self.seller])
        self.assertEqual(mock_call.call_count, 3)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_multiple_proposals_across_turns_are_all_captured(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_tool_use_block("propose_stock_update", {"product_id": 1, "new_stock": 50}, block_id="t1")]),
            _tool_response([_tool_use_block("propose_price_update", {"product_id": 1, "new_price": "25.99"}, block_id="t2")]),
            _tool_response([_text_block("I've proposed both changes.")]),
        ]

        def propose_stock(seller, product_id, new_stock):
            return {
                "action": "update_product_stock", "product_id": product_id,
                "product_name": "Widget", "field": "stock", "current_value": 10, "new_value": new_stock,
            }

        def propose_price(seller, product_id, new_price):
            return {
                "action": "update_product_price", "product_id": product_id,
                "product_name": "Widget", "field": "price", "current_value": "19.99", "new_value": new_price,
            }

        reply = run_with_tools(
            "update stock and price", "sys", [],
            {"propose_stock_update": propose_stock, "propose_price_update": propose_price}, self.seller,
            proposal_tool_names={"propose_stock_update", "propose_price_update"},
        )

        self.assertEqual(reply.text, "I've proposed both changes.")
        self.assertEqual(len(reply.pending_actions), 2)
        self.assertEqual(reply.pending_actions[0]["field"], "stock")
        self.assertEqual(reply.pending_actions[1]["field"], "price")

    @patch("ai.services.tool_runner.call_with_tools")
    def test_multiple_proposals_in_the_same_batch_are_all_captured(self, mock_call):
        mock_call.side_effect = [
            _tool_response([
                _tool_use_block("propose_stock_update", {"product_id": 1, "new_stock": 50}, block_id="t1"),
                _tool_use_block("propose_price_update", {"product_id": 1, "new_price": "25.99"}, block_id="t2"),
            ]),
            _tool_response([_text_block("I've proposed both changes.")]),
        ]

        def propose_stock(seller, product_id, new_stock):
            return {
                "action": "update_product_stock", "product_id": product_id,
                "product_name": "Widget", "field": "stock", "current_value": 10, "new_value": new_stock,
            }

        def propose_price(seller, product_id, new_price):
            return {
                "action": "update_product_price", "product_id": product_id,
                "product_name": "Widget", "field": "price", "current_value": "19.99", "new_value": new_price,
            }

        reply = run_with_tools(
            "update stock and price", "sys", [],
            {"propose_stock_update": propose_stock, "propose_price_update": propose_price}, self.seller,
            proposal_tool_names={"propose_stock_update", "propose_price_update"},
        )

        self.assertEqual(len(reply.pending_actions), 2)
        self.assertEqual(mock_call.call_count, 2)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_failed_proposal_does_not_appear_in_pending_actions(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_tool_use_block("propose_stock_update", {"product_id": 999, "new_stock": 50})]),
            _tool_response([_text_block("I couldn't find that product.")]),
        ]

        def failing_propose(seller, product_id, new_stock):
            raise ValueError("boom")

        reply = run_with_tools(
            "update stock", "sys", [], {"propose_stock_update": failing_propose}, self.seller,
            proposal_tool_names={"propose_stock_update"},
        )

        self.assertEqual(reply.text, "I couldn't find that product.")
        self.assertEqual(reply.pending_actions, [])

    @patch("ai.services.tool_runner.call_with_tools")
    def test_generation_error_after_proposal_still_returns_pending_actions(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_tool_use_block("propose_price_update", {"product_id": 1, "new_price": "9.99"})]),
            LLMGenerationError("boom"),
        ]

        def propose(seller, product_id, new_price):
            return {
                "action": "update_product_price", "product_id": product_id,
                "product_name": "Widget", "field": "price", "current_value": "19.99", "new_value": new_price,
            }

        reply = run_with_tools(
            "update price", "sys", [], {"propose_price_update": propose}, self.seller,
            proposal_tool_names={"propose_price_update"},
        )

        self.assertEqual(reply.text, GENERATION_FAILED_MESSAGE)
        self.assertEqual(len(reply.pending_actions), 1)
        self.assertEqual(reply.pending_actions[0]["new_value"], "9.99")

    @patch("ai.services.tool_runner.call_with_tools")
    def test_empty_final_text_falls_back_to_default_proposal_summary(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_tool_use_block("propose_stock_update", {"product_id": 1, "new_stock": 50})]),
            _tool_response([]),
        ]

        def propose(seller, product_id, new_stock):
            return {
                "action": "update_product_stock", "product_id": product_id,
                "product_name": "Widget", "field": "stock", "current_value": 10, "new_value": new_stock,
            }

        reply = run_with_tools(
            "update stock", "sys", [], {"propose_stock_update": propose}, self.seller,
            proposal_tool_names={"propose_stock_update"},
        )

        self.assertIn("Widget", reply.text)
        self.assertIn("stock", reply.text)
        self.assertEqual(len(reply.pending_actions), 1)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_actor_kwarg_controls_the_keyword_used_to_call_executors(self, mock_call):
        """actor_kwarg defaults to "seller" (Phase 3 tools take `seller`) but
        callers scoping to a different role, e.g. the buyer assistant's
        `user`-keyword tools, pass a different name - the loop itself never
        hardcodes a role."""
        mock_call.side_effect = [
            _tool_response([_tool_use_block("get_thing", {"x": 1})]),
            _tool_response([_text_block("done")]),
        ]
        calls = []

        def executor(user, x):
            calls.append((user, x))
            return "ok"

        reply = run_with_tools(
            "hi", "sys", [], {"get_thing": executor}, self.seller, actor_kwarg="user",
        )

        self.assertEqual(reply.text, "done")
        self.assertEqual(calls, [(self.seller, 1)])

    @patch("ai.services.tool_runner.call_with_tools")
    def test_prior_messages_are_prepended_ahead_of_the_current_prompt(self, mock_call):
        mock_call.return_value = _tool_response([_text_block("done")])

        run_with_tools(
            "current question", "sys", [], {}, self.seller,
            prior_messages=[
                {"role": "user", "content": "earlier"},
                {"role": "assistant", "content": "earlier reply"},
            ],
        )

        sent_messages = mock_call.call_args.args[0]
        self.assertEqual(sent_messages, [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "earlier reply"},
            {"role": "user", "content": "current question"},
        ])

    @patch("ai.services.tool_runner.call_with_tools")
    def test_last_product_id_captured_from_a_tool_call_with_a_product_id_argument(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_tool_use_block("get_product_performance", {"product_id": 42})]),
            _tool_response([_text_block("done")]),
        ]

        def get_product_performance(seller, product_id):
            return {"name": "Widget", "current_stock": 5}

        reply = run_with_tools(
            "how's widget doing", "sys", [], {"get_product_performance": get_product_performance}, self.seller,
        )

        self.assertEqual(reply.last_product_id, 42)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_last_product_id_captured_from_a_single_find_product_by_name_match(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_tool_use_block("find_product_by_name", {"name": "widget"})]),
            _tool_response([_text_block("done")]),
        ]

        def find_product_by_name(seller, name):
            return [{"id": 7, "name": "Widget"}]

        reply = run_with_tools(
            "find widget", "sys", [], {"find_product_by_name": find_product_by_name}, self.seller,
        )

        self.assertEqual(reply.last_product_id, 7)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_last_product_id_not_captured_from_an_ambiguous_find_product_by_name_match(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_tool_use_block("find_product_by_name", {"name": "widget"})]),
            _tool_response([_text_block("Which one did you mean?")]),
        ]

        def find_product_by_name(seller, name):
            return [{"id": 7, "name": "Widget A"}, {"id": 8, "name": "Widget B"}]

        reply = run_with_tools(
            "find widget", "sys", [], {"find_product_by_name": find_product_by_name}, self.seller,
        )

        self.assertIsNone(reply.last_product_id)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_last_product_id_defaults_to_none_when_no_product_tool_is_called(self, mock_call):
        mock_call.return_value = _tool_response([_text_block("Hello")])

        reply = run_with_tools("hi", "sys", [], {}, self.seller)

        self.assertIsNone(reply.last_product_id)


class SellerAssistantViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.seller = User.objects.create_user(
            email="seller@test.com", password="testpass123", role="SELLER"
        )
        self.buyer = User.objects.create_user(
            email="buyer@test.com", password="testpass123", role="BUYER"
        )
        self.url = "/api/ai/seller-assistant/"

    def _auth_as(self, user):
        token = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

    def test_unauthenticated_request_returns_401(self):
        response = self.client.post(self.url, {"question": "hi"}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_buyer_request_returns_403(self):
        self._auth_as(self.buyer)
        response = self.client.post(self.url, {"question": "hi"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_empty_question_returns_400(self):
        self._auth_as(self.seller)
        response = self.client.post(self.url, {"question": "   "}, format="json")
        self.assertEqual(response.status_code, 400)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_tool_executor_is_called_with_the_authenticated_seller(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_tool_use_block("get_low_stock_products", {})]),
            _tool_response([_text_block("You have no low stock products.")]),
        ]
        mock_executor = MagicMock(return_value=[])
        self._auth_as(self.seller)

        with patch.dict("ai.views.SELLER_ASSISTANT_TOOL_EXECUTORS", {"get_low_stock_products": mock_executor}):
            response = self.client.post(self.url, {"question": "low stock?"}, format="json")

        self.assertEqual(response.status_code, 200)
        mock_executor.assert_called_once_with(seller=self.seller)

    @patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"ai_seller_assistant": "1/minute"})
    @patch("ai.services.tool_runner.call_with_tools")
    def test_throttle_returns_429_when_scope_limit_exceeded(self, mock_call):
        mock_call.return_value = _tool_response([_text_block("done")])
        self._auth_as(self.seller)

        first = self.client.post(self.url, {"question": "hi"}, format="json")
        self.assertEqual(first.status_code, 200)

        second = self.client.post(self.url, {"question": "hi"}, format="json")
        self.assertEqual(second.status_code, 429)


class ConfirmSellerActionViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.seller = User.objects.create_user(
            email="seller@test.com", password="testpass123", role="SELLER"
        )
        self.other_seller = User.objects.create_user(
            email="other-seller@test.com", password="testpass123", role="SELLER"
        )
        self.buyer = User.objects.create_user(
            email="buyer@test.com", password="testpass123", role="BUYER"
        )
        self.category = Category.objects.create(name="Kitchen", slug="kitchen")
        self.product = Product.objects.create(
            seller=self.seller, category=self.category, name="Widget",
            price=Decimal("20.00"), stock=5,
        )
        self.url = "/api/ai/seller-assistant/confirm-action/"

    def _auth_as(self, user):
        token = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

    def _payload(self, **overrides):
        payload = {"action": "update_product_stock", "product_id": self.product.id, "new_value": 42}
        payload.update(overrides)
        return payload

    def test_unauthenticated_request_returns_401(self):
        response = self.client.post(self.url, self._payload(), format="json")
        self.assertEqual(response.status_code, 401)

    def test_buyer_request_returns_403(self):
        self._auth_as(self.buyer)
        response = self.client.post(self.url, self._payload(), format="json")
        self.assertEqual(response.status_code, 403)

    def test_unknown_action_returns_400(self):
        self._auth_as(self.seller)
        response = self.client.post(self.url, self._payload(action="delete_everything"), format="json")
        self.assertEqual(response.status_code, 400)

    def test_missing_new_value_returns_400(self):
        self._auth_as(self.seller)
        response = self.client.post(self.url, self._payload(new_value=""), format="json")
        self.assertEqual(response.status_code, 400)

    def test_confirm_stock_update_persists_change(self):
        self._auth_as(self.seller)
        response = self.client.post(self.url, self._payload(new_value=42), format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 42)

    def test_confirm_price_update_persists_change(self):
        self._auth_as(self.seller)
        payload = self._payload(action="update_product_price", new_value="35.50")
        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal("35.50"))

    def test_cross_seller_product_returns_404_and_does_not_mutate(self):
        self._auth_as(self.other_seller)
        response = self.client.post(self.url, self._payload(new_value=999), format="json")

        self.assertEqual(response.status_code, 404)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_negative_stock_returns_400_and_does_not_mutate(self):
        self._auth_as(self.seller)
        response = self.client.post(self.url, self._payload(new_value=-1), format="json")

        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_negative_price_returns_400_and_does_not_mutate(self):
        self._auth_as(self.seller)
        payload = self._payload(action="update_product_price", new_value="-5.00")
        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price, Decimal("20.00"))

    def test_confirm_toggle_active_persists_change(self):
        self._auth_as(self.seller)
        payload = self._payload(action="toggle_product_active", new_value="inactive")
        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["field"], "status")
        self.assertEqual(response.data["new_value"], "inactive")
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)

    def test_confirm_toggle_active_accepts_boolean_new_value(self):
        self._auth_as(self.seller)
        payload = self._payload(action="toggle_product_active", new_value=False)
        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 200)
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)

    def test_toggle_active_invalid_value_returns_400_and_does_not_mutate(self):
        self._auth_as(self.seller)
        payload = self._payload(action="toggle_product_active", new_value="banana")
        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)

    def test_cross_seller_toggle_active_returns_404_and_does_not_mutate(self):
        self._auth_as(self.other_seller)
        payload = self._payload(action="toggle_product_active", new_value="inactive")
        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 404)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)

    def test_confirm_create_product_persists_new_product_owned_by_seller(self):
        self._auth_as(self.seller)
        payload = {
            "action": "create_product",
            "name": "Steel Kettle",
            "category_id": self.category.id,
            "price": "24.99",
            "stock": 10,
        }
        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["product_name"], "Steel Kettle")
        product = Product.objects.get(id=response.data["product_id"])
        self.assertEqual(product.seller, self.seller)
        self.assertEqual(product.price, Decimal("24.99"))
        self.assertEqual(product.stock, 10)

    def test_confirm_create_product_missing_fields_returns_400(self):
        self._auth_as(self.seller)
        payload = {"action": "create_product", "name": "Steel Kettle", "category_id": self.category.id}
        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 400)

    def test_confirm_create_product_invalid_category_returns_400_and_does_not_create(self):
        self._auth_as(self.seller)
        payload = {
            "action": "create_product",
            "name": "Steel Kettle",
            "category_id": 999999,
            "price": "24.99",
            "stock": 10,
        }
        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Product.objects.filter(name="Steel Kettle").exists())

    def test_confirm_create_product_negative_stock_returns_400_and_does_not_create(self):
        self._auth_as(self.seller)
        payload = {
            "action": "create_product",
            "name": "Steel Kettle",
            "category_id": self.category.id,
            "price": "24.99",
            "stock": -1,
        }
        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Product.objects.filter(name="Steel Kettle").exists())

    @patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"ai_seller_action": "1/minute"})
    def test_throttle_returns_429_when_scope_limit_exceeded(self):
        self._auth_as(self.seller)

        first = self.client.post(self.url, self._payload(new_value=10), format="json")
        self.assertEqual(first.status_code, 200)

        second = self.client.post(self.url, self._payload(new_value=11), format="json")
        self.assertEqual(second.status_code, 429)


class BuyerToolsTests(TestCase):
    """Ownership scoping is a query-level guarantee, not a prompt instruction -
    these test the executors directly, independent of the tool-calling loop."""

    def setUp(self):
        self.buyer = User.objects.create_user(
            email="buyer@test.com", password="testpass123", role="BUYER"
        )
        self.other_buyer = User.objects.create_user(
            email="other-buyer@test.com", password="testpass123", role="BUYER"
        )
        self.seller = User.objects.create_user(
            email="seller@test.com", password="testpass123", role="SELLER"
        )
        self.category = Category.objects.create(name="Kitchen", slug="kitchen")
        self.product = Product.objects.create(
            seller=self.seller, category=self.category, name="Steel Mug",
            price=Decimal("10.00"), stock=50,
        )

    def _create_order(self, buyer, quantity=1, price=None, created_at=None, order_status=Order.Status.PLACED):
        price = price if price is not None else self.product.price
        order = Order.objects.create(buyer=buyer, total_amount=price * quantity, status=order_status)
        OrderItem.objects.create(
            order=order, product=self.product, seller=self.seller,
            quantity=quantity, price_at_purchase=price,
        )
        if created_at is not None:
            Order.objects.filter(id=order.id).update(created_at=created_at)
        return order

    def test_get_my_orders_excludes_other_buyers_orders(self):
        mine = self._create_order(self.buyer)
        self._create_order(self.other_buyer)

        results = get_my_orders(self.buyer)

        self.assertEqual([o["id"] for o in results], [mine.id])

    def test_get_my_orders_respects_days_filter(self):
        self._create_order(self.buyer, created_at=timezone.now() - timedelta(days=60))
        recent = self._create_order(self.buyer)

        results = get_my_orders(self.buyer, days=30)

        self.assertEqual([o["id"] for o in results], [recent.id])

    def test_get_my_orders_respects_status_filter(self):
        placed = self._create_order(self.buyer, order_status=Order.Status.PLACED)
        self._create_order(self.buyer, order_status=Order.Status.CANCELLED)

        results = get_my_orders(self.buyer, status="PLACED")

        self.assertEqual([o["id"] for o in results], [placed.id])

    def test_get_my_orders_respects_limit(self):
        for _ in range(3):
            self._create_order(self.buyer)

        results = get_my_orders(self.buyer, limit=2)

        self.assertEqual(len(results), 2)

    def test_get_my_orders_total_matches_order_total_amount(self):
        order = self._create_order(self.buyer, quantity=2, price=Decimal("10.00"))

        results = get_my_orders(self.buyer)

        self.assertEqual(Decimal(results[0]["total"]), order.total_amount)

    def test_get_order_detail_includes_seller_email_per_item(self):
        order = self._create_order(self.buyer)

        result = get_order_detail(self.buyer, order.id)

        self.assertEqual(result["items"][0]["seller"], self.seller.email)

    def test_get_order_detail_cross_buyer_raises_does_not_exist(self):
        order = self._create_order(self.other_buyer)

        with self.assertRaises(Order.DoesNotExist):
            get_order_detail(self.buyer, order.id)


class ConversationModelTests(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(
            email="buyer@test.com", password="testpass123", role="BUYER"
        )

    def test_get_recent_messages_returns_last_n_in_chronological_order(self):
        conversation = Conversation.objects.create(user=self.buyer)
        base = timezone.now() - timedelta(minutes=30)
        for i in range(25):
            message = Message.objects.create(
                conversation=conversation,
                role=Message.ROLE_USER if i % 2 == 0 else Message.ROLE_ASSISTANT,
                content=f"message {i}",
            )
            # Explicit, distinct timestamps - 25 rapid in-test creates can tie
            # at auto_now_add's resolution, which would make ordering flaky.
            Message.objects.filter(id=message.id).update(created_at=base + timedelta(seconds=i))

        recent = conversation.get_recent_messages(n=20)

        self.assertEqual(len(recent), 20)
        self.assertEqual([m.content for m in recent], [f"message {i}" for i in range(5, 25)])

    def test_build_message_history_caps_at_the_configured_window(self):
        conversation = Conversation.objects.create(user=self.buyer)
        base = timezone.now() - timedelta(minutes=30)
        for i in range(15):
            message = Message.objects.create(
                conversation=conversation,
                role=Message.ROLE_USER if i % 2 == 0 else Message.ROLE_ASSISTANT,
                content=f"message {i}",
            )
            Message.objects.filter(id=message.id).update(created_at=base + timedelta(seconds=i))

        history = build_message_history(conversation)

        # Cost-driven default: 5 messages, not 20 - see
        # ai/services/context.py's HISTORY_WINDOW comment.
        self.assertEqual(len(history), 5)
        self.assertEqual([m["content"] for m in history], [f"message {i}" for i in range(10, 15)])


class BuyerOrderAssistantViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.buyer = User.objects.create_user(
            email="buyer@test.com", password="testpass123", role="BUYER"
        )
        self.other_buyer = User.objects.create_user(
            email="other-buyer@test.com", password="testpass123", role="BUYER"
        )
        self.seller = User.objects.create_user(
            email="seller@test.com", password="testpass123", role="SELLER"
        )
        self.url = "/api/ai/order-assistant/"

    def _auth_as(self, user):
        token = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

    def test_unauthenticated_request_returns_401(self):
        response = self.client.post(self.url, {"message": "hi"}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_seller_request_returns_403(self):
        self._auth_as(self.seller)
        response = self.client.post(self.url, {"message": "hi"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_empty_message_returns_400(self):
        self._auth_as(self.buyer)
        response = self.client.post(self.url, {"message": "   "}, format="json")
        self.assertEqual(response.status_code, 400)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_first_message_creates_conversation_owned_by_buyer(self, mock_call):
        mock_call.return_value = _tool_response([_text_block("Hi there!")])
        self._auth_as(self.buyer)

        response = self.client.post(self.url, {"message": "hi"}, format="json")

        self.assertEqual(response.status_code, 200)
        conversation = Conversation.objects.get(id=response.data["conversation_id"])
        self.assertEqual(conversation.user, self.buyer)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_user_and_assistant_messages_are_persisted(self, mock_call):
        mock_call.return_value = _tool_response([_text_block("Hi there!")])
        self._auth_as(self.buyer)

        response = self.client.post(self.url, {"message": "hi"}, format="json")

        conversation = Conversation.objects.get(id=response.data["conversation_id"])
        roles_and_content = [(m.role, m.content) for m in conversation.messages.all()]
        self.assertEqual(
            roles_and_content,
            [(Message.ROLE_USER, "hi"), (Message.ROLE_ASSISTANT, "Hi there!")],
        )

    @patch("ai.services.tool_runner.call_with_tools")
    def test_second_message_reuses_conversation_and_history_is_not_duplicated(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_text_block("Hello! How can I help?")]),
            _tool_response([_text_block("Sure, here you go.")]),
        ]
        self._auth_as(self.buyer)

        first = self.client.post(self.url, {"message": "hi"}, format="json")
        conversation_id = first.data["conversation_id"]

        second = self.client.post(
            self.url, {"message": "tell me more", "conversation_id": conversation_id}, format="json",
        )

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["conversation_id"], conversation_id)

        # Regression guard: the prior exchange must appear exactly once,
        # immediately followed by the new prompt - not duplicated by also
        # being the last entry in prior_messages AND re-appended as prompt.
        second_call_messages = mock_call.call_args_list[1].args[0]
        self.assertEqual(
            second_call_messages,
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "Hello! How can I help?"},
                {"role": "user", "content": "tell me more"},
            ],
        )

    @patch("ai.services.tool_runner.call_with_tools")
    def test_conversation_belonging_to_different_buyer_returns_404(self, mock_call):
        mock_call.return_value = _tool_response([_text_block("Hi there!")])
        self._auth_as(self.buyer)
        first = self.client.post(self.url, {"message": "hi"}, format="json")
        conversation_id = first.data["conversation_id"]

        self._auth_as(self.other_buyer)
        response = self.client.post(
            self.url, {"message": "hi again", "conversation_id": conversation_id}, format="json",
        )

        self.assertEqual(response.status_code, 404)

    def test_unknown_conversation_id_returns_404(self):
        self._auth_as(self.buyer)
        response = self.client.post(
            self.url, {"message": "hi", "conversation_id": 999999}, format="json",
        )
        self.assertEqual(response.status_code, 404)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_tool_executor_is_called_with_the_authenticated_buyer_as_user_kwarg(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_tool_use_block("get_my_orders", {})]),
            _tool_response([_text_block("You have no orders.")]),
        ]
        mock_executor = MagicMock(return_value=[])
        self._auth_as(self.buyer)

        with patch.dict("ai.views.BUYER_TOOL_EXECUTORS", {"get_my_orders": mock_executor}):
            response = self.client.post(self.url, {"message": "my orders?"}, format="json")

        self.assertEqual(response.status_code, 200)
        mock_executor.assert_called_once_with(user=self.buyer)

    @patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"ai_order_assistant": "1/minute"})
    @patch("ai.services.tool_runner.call_with_tools")
    def test_throttle_returns_429_when_scope_limit_exceeded(self, mock_call):
        mock_call.return_value = _tool_response([_text_block("done")])
        self._auth_as(self.buyer)

        first = self.client.post(self.url, {"message": "hi"}, format="json")
        self.assertEqual(first.status_code, 200)

        second = self.client.post(self.url, {"message": "hi"}, format="json")
        self.assertEqual(second.status_code, 429)
