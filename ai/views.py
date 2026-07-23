import json
import logging

from django.http import StreamingHttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle

from products.models import Product
from users.permissions import IsBuyer, IsSeller
from .models import Conversation, Message
from .parsers import DescriptionStreamParser
from .prompts import (
    DESCRIPTION_SYSTEM_PROMPT,
    ORDER_ASSISTANT_SYSTEM_PROMPT,
    SELLER_ASSISTANT_SYSTEM_PROMPT,
    build_description_prompt,
)
from .services.context import build_message_history
from .services.llm_client import stream, LLMGenerationError
from .services.tool_runner import run_with_tools
from .tools.buyer_tools import BUYER_TOOL_DEFINITIONS, BUYER_TOOL_EXECUTORS
from .tools.seller_actions import (
    PROPOSAL_TOOL_NAMES,
    SELLER_ACTION_CONFIRM_EXECUTORS,
    SELLER_ACTION_DEFINITIONS,
    SELLER_ACTION_PROPOSE_EXECUTORS,
)
from .tools.seller_tools import SELLER_TOOL_DEFINITIONS, SELLER_TOOL_EXECUTORS

logger = logging.getLogger(__name__)

MAX_TOKENS = 600
MAX_ADDITIONAL_DETAILS_LENGTH = 500

# The assistant's full tool set: read-only lookups plus proposal-only write
# tools. Combined here, at the point of use, rather than inside the tools
# modules themselves, so it stays explicit that this endpoint deliberately
# uses both - not hidden behind another layer of registry merging.
SELLER_ASSISTANT_TOOL_DEFINITIONS = SELLER_TOOL_DEFINITIONS + SELLER_ACTION_DEFINITIONS
SELLER_ASSISTANT_TOOL_EXECUTORS = {**SELLER_TOOL_EXECUTORS, **SELLER_ACTION_PROPOSE_EXECUTORS}


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


def _with_last_product_hint(question, conversation):
    """Prepend a note naming the product a tool last operated on in this
    conversation, if any - so a follow-up like "what's the price now?" can
    skip re-resolving a product already established, instead of relying on
    the model to notice the reference itself or paying for another
    find_product_by_name round trip. Never persisted - this is added to the
    prompt sent to the model only; the Message row stores the seller's
    original text unchanged.
    """
    if not conversation.last_product_id:
        return question
    try:
        product_name = conversation.last_product.name
    except Product.DoesNotExist:
        return question
    return (
        f"<context>The most recently discussed product is \"{product_name}\" "
        f"(product_id {conversation.last_product_id}). If this message doesn't "
        f"name a different product, assume it refers to this one.</context>\n\n"
        f"{question}"
    )


class SellerAssistantView(APIView):
    permission_classes = [IsSeller]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai_seller_assistant"

    def post(self, request):
        question = str(request.data.get("question", "")).strip()
        conversation_id = request.data.get("conversation_id")

        if not question:
            return Response(
                {"detail": "question is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if conversation_id:
            try:
                conversation = Conversation.objects.get(
                    id=conversation_id,
                    user=request.user,  # ownership enforced here
                )
            except Conversation.DoesNotExist:
                return Response(
                    {"detail": "Conversation not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            conversation = Conversation.objects.create(user=request.user)

        # Built from messages already in the DB, before this turn's user
        # message is persisted below - see BuyerOrderAssistantView for why
        # the ordering matters (avoids sending the current message twice).
        history = build_message_history(conversation)

        Message.objects.create(
            conversation=conversation,
            role=Message.ROLE_USER,
            content=question,
        )

        reply = run_with_tools(
            prompt=_with_last_product_hint(question, conversation),
            system=SELLER_ASSISTANT_SYSTEM_PROMPT,
            tool_definitions=SELLER_ASSISTANT_TOOL_DEFINITIONS,
            tool_executors=SELLER_ASSISTANT_TOOL_EXECUTORS,
            actor=request.user,
            max_tool_calls=5,
            proposal_tool_names=PROPOSAL_TOOL_NAMES,
            prior_messages=history,
        )

        Message.objects.create(
            conversation=conversation,
            role=Message.ROLE_ASSISTANT,
            content=reply.text,
        )

        if reply.last_product_id and reply.last_product_id != conversation.last_product_id:
            conversation.last_product_id = reply.last_product_id
            conversation.save(update_fields=["last_product_id"])

        return Response({
            "response": reply.text,
            "pending_actions": reply.pending_actions,
            "conversation_id": conversation.id,
        })


class ConfirmSellerActionView(APIView):
    permission_classes = [IsSeller]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai_seller_action"

    def post(self, request):
        action = request.data.get("action")
        executor = SELLER_ACTION_CONFIRM_EXECUTORS.get(action)
        if executor is None:
            return Response({"detail": "Unknown action."}, status=status.HTTP_400_BAD_REQUEST)

        # create_product has no product_id yet and needs several fields rather
        # than a single new_value, so it takes its own request shape.
        if action == "create_product":
            kwargs = {
                "name": request.data.get("name"),
                "category_id": request.data.get("category_id"),
                "price": request.data.get("price"),
                "stock": request.data.get("stock"),
            }
            if any(v in (None, "") for v in kwargs.values()):
                return Response(
                    {"detail": "name, category_id, price, and stock are required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            product_id = request.data.get("product_id")
            new_value = request.data.get("new_value")
            if product_id in (None, "") or new_value in (None, ""):
                return Response(
                    {"detail": "product_id and new_value are required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            kwargs = {"product_id": product_id, "new_value": new_value}

        try:
            result = executor(seller=request.user, **kwargs)
        except Product.DoesNotExist:
            return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)
        except (ValueError, TypeError):
            return Response({"detail": "Invalid value for this action."}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"success": True, **result})


class BuyerOrderAssistantView(APIView):
    permission_classes = [IsBuyer]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai_order_assistant"

    def post(self, request):
        message_text = str(request.data.get("message", "")).strip()
        conversation_id = request.data.get("conversation_id")

        if not message_text:
            return Response(
                {"detail": "message is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if conversation_id:
            try:
                conversation = Conversation.objects.get(
                    id=conversation_id,
                    user=request.user,  # ownership enforced here
                )
            except Conversation.DoesNotExist:
                return Response(
                    {"detail": "Conversation not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            conversation = Conversation.objects.create(user=request.user)

        # Built from messages already in the DB, before this turn's user
        # message is persisted below - otherwise that message would show up
        # twice in a row (once as the last history entry, once again as the
        # prompt run_with_tools appends), which the Anthropic API rejects.
        history = build_message_history(conversation)

        Message.objects.create(
            conversation=conversation,
            role=Message.ROLE_USER,
            content=message_text,
        )

        reply = run_with_tools(
            prompt=message_text,
            system=ORDER_ASSISTANT_SYSTEM_PROMPT,
            tool_definitions=BUYER_TOOL_DEFINITIONS,
            tool_executors=BUYER_TOOL_EXECUTORS,
            actor=request.user,
            actor_kwarg="user",
            max_tool_calls=5,
            prior_messages=history,
        )

        Message.objects.create(
            conversation=conversation,
            role=Message.ROLE_ASSISTANT,
            content=reply.text,
        )

        return Response({
            "response": reply.text,
            "conversation_id": conversation.id,
        })
