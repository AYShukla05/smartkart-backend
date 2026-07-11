import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from categories.models import Category
from products.models import Product
from users.models import User
from ai.parsers import DescriptionStreamParser, parse_description_json
from ai.prompts import build_description_prompt
from ai.services.llm_client import LLMGenerationError


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
