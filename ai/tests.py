import json
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from categories.models import Category
from products.models import Product
from users.models import User
from ai.models import ProductEmbedding
from ai.parsers import DescriptionStreamParser, parse_description_json
from ai.prompts import build_description_prompt
from ai.services.llm_client import CURRENT_EMBEDDING_MODEL_ID, LLMGenerationError
from ai.services.search import MINIMUM_FLOOR, RELEVANCE_THRESHOLD, _select_candidates, semantic_search
from ai.services.tool_runner import GENERATION_FAILED_MESSAGE, MAX_TOOL_CALLS_REACHED_MESSAGE, run_with_tools
from ai.tools.seller_tools import SELLER_TOOL_EXECUTORS, generate_product_description, get_low_stock_products


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


class RunWithToolsTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            email="seller@test.com", password="testpass123", role="SELLER"
        )

    @patch("ai.services.tool_runner.call_with_tools")
    def test_text_only_response_returns_immediately_without_second_call(self, mock_call):
        mock_call.return_value = _tool_response([_text_block("Hello seller")])

        result = run_with_tools("hi", "sys", [], {}, self.seller)

        self.assertEqual(result, "Hello seller")
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

        result = run_with_tools("hi", "sys", [], {"get_thing": executor}, self.seller)

        self.assertEqual(result, "done")
        self.assertEqual(calls, [(self.seller, 1)])

    @patch("ai.services.tool_runner.call_with_tools")
    def test_loop_terminates_at_max_tool_calls(self, mock_call):
        mock_call.side_effect = lambda *a, **k: _tool_response([_tool_use_block("get_thing", {"x": 1})])
        calls = []

        def executor(seller, x):
            calls.append((seller, x))
            return "ok"

        result = run_with_tools("hi", "sys", [], {"get_thing": executor}, self.seller, max_tool_calls=5)

        self.assertEqual(result, MAX_TOOL_CALLS_REACHED_MESSAGE)
        self.assertEqual(len(calls), 5)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_tool_executor_exception_is_fed_back_as_error_string_not_raised(self, mock_call):
        mock_call.side_effect = [
            _tool_response([_tool_use_block("get_thing", {})]),
            _tool_response([_text_block("recovered")]),
        ]

        def failing_executor(seller):
            raise ValueError("boom")

        result = run_with_tools("hi", "sys", [], {"get_thing": failing_executor}, self.seller)

        self.assertEqual(result, "recovered")
        second_call_messages = mock_call.call_args_list[1].args[0]
        tool_result_content = second_call_messages[-1]["content"][0]["content"]
        self.assertIn("Error calling get_thing", tool_result_content)

    @patch("ai.services.tool_runner.call_with_tools")
    def test_generation_error_returns_friendly_message_not_500(self, mock_call):
        mock_call.side_effect = LLMGenerationError("boom")

        result = run_with_tools("hi", "sys", [], {}, self.seller)

        self.assertEqual(result, GENERATION_FAILED_MESSAGE)


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

        with patch.dict(SELLER_TOOL_EXECUTORS, {"get_low_stock_products": mock_executor}):
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
