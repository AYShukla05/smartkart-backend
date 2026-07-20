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
from ai.models import ProductEmbedding
from ai.parsers import DescriptionStreamParser, parse_description_json
from ai.prompts import build_description_prompt
from ai.services.llm_client import CURRENT_EMBEDDING_MODEL_ID, LLMGenerationError
from ai.services.search import MINIMUM_FLOOR, RELEVANCE_THRESHOLD, _select_candidates, semantic_search
from ai.services.tool_runner import GENERATION_FAILED_MESSAGE, MAX_TOOL_CALLS_REACHED_MESSAGE, run_with_tools
from ai.tools.seller_tools import (
    find_product_by_name,
    generate_product_description,
    get_category_breakdown,
    get_low_stock_products,
    get_recent_orders,
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
        self.assertIsNone(reply.pending_action)
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
        self.assertIsNone(reply.pending_action)
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
        self.assertIsNone(reply.pending_action)
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
        self.assertIsNone(reply.pending_action)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_proposal_tool_is_captured_and_loop_stops_after_one_closing_call(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_tool_use_block("propose_stock_update", {"product_id": 1, "new_stock": 50})]),
            _tool_response([_text_block("Update stock to 50?"), _tool_use_block("get_seller_stats", {})]),
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
        self.assertEqual(reply.pending_action["product_id"], 1)
        self.assertEqual(reply.pending_action["new_value"], 50)
        # The closing response's get_seller_stats tool_use must be ignored, not executed
        self.assertEqual(mock_call.call_count, 2)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_failed_proposal_does_not_set_pending_action(self, mock_call):
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
        self.assertIsNone(reply.pending_action)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_proposal_closing_call_failure_still_returns_pending_action(self, mock_call):
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

        self.assertIn("Widget", reply.text)
        self.assertEqual(reply.pending_action["new_value"], "9.99")


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

    @patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"ai_seller_action": "1/minute"})
    def test_throttle_returns_429_when_scope_limit_exceeded(self):
        self._auth_as(self.seller)

        first = self.client.post(self.url, self._payload(new_value=10), format="json")
        self.assertEqual(first.status_code, 200)

        second = self.client.post(self.url, self._payload(new_value=11), format="json")
        self.assertEqual(second.status_code, 429)
