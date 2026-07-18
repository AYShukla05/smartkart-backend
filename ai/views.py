import json
import logging

from django.http import StreamingHttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle

from users.permissions import IsSeller
from .parsers import DescriptionStreamParser
from .prompts import DESCRIPTION_SYSTEM_PROMPT, SELLER_ASSISTANT_SYSTEM_PROMPT, build_description_prompt
from .services.llm_client import stream, LLMGenerationError
from .services.tool_runner import run_with_tools
from .tools.seller_tools import SELLER_TOOL_DEFINITIONS, SELLER_TOOL_EXECUTORS

logger = logging.getLogger(__name__)

MAX_TOKENS = 600
MAX_ADDITIONAL_DETAILS_LENGTH = 500


class GenerateDescriptionView(APIView):
    permission_classes = [IsSeller]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai_generate"

    def post(self, request):
        name = str(request.data.get("name", "")).strip()
        category = str(request.data.get("category", "")).strip()
        price = request.data.get("price")
        additional_details = str(request.data.get("additional_details", "")).strip()

        if not name or not category or price in (None, ""):
            return Response(
                {"detail": "name, category, and price are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(additional_details) > MAX_ADDITIONAL_DETAILS_LENGTH:
            return Response(
                {"detail": f"additional_details cannot exceed {MAX_ADDITIONAL_DETAILS_LENGTH} characters."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        prompt = build_description_prompt(name, category, price, additional_details)
        return StreamingHttpResponse(
            self._event_stream(prompt),
            content_type="text/event-stream",
        )

    def _event_stream(self, prompt):
        """SSE protocol:
        - `data: <text>`        live description text, as it streams
        - `data: [RESULT]<json>` full parsed {title, bullets, seo_keywords,
          description} once generation finishes - the only way title/bullets/
          keywords reach the client, since those fields are buffered
          server-side and never streamed as raw tokens (see parsers.py)
        - `data: [ERROR]<msg>`  generation failed; always followed by [DONE]
        - `data: [DONE]`        always the final line
        """
        parser = DescriptionStreamParser()
        try:
            for token in stream(
                prompt,
                system=DESCRIPTION_SYSTEM_PROMPT,
                max_tokens=MAX_TOKENS,
            ):
                text = parser.feed(token)
                if text:
                    yield f"data: {text}\n\n"
        except LLMGenerationError:
            logger.error("Description generation failed mid-stream", exc_info=True)
            yield "data: [ERROR] Description generation failed. Please try again.\n\n"
            yield "data: [DONE]\n\n"
            return

        result = parser.finalize()
        yield f"data: [RESULT]{json.dumps(result)}\n\n"
        yield "data: [DONE]\n\n"


class SellerAssistantView(APIView):
    permission_classes = [IsSeller]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai_seller_assistant"

    def post(self, request):
        question = str(request.data.get("question", "")).strip()
        if not question:
            return Response(
                {"detail": "question is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_text = run_with_tools(
            prompt=question,
            system=SELLER_ASSISTANT_SYSTEM_PROMPT,
            tool_definitions=SELLER_TOOL_DEFINITIONS,
            tool_executors=SELLER_TOOL_EXECUTORS,
            seller=request.user,
            max_tool_calls=5,
        )
        return Response({"response": response_text})
