import logging
from typing import NamedTuple, Optional

from django.contrib.auth import get_user_model

from ai.services.llm_client import LLMGenerationError, call_with_tools

logger = logging.getLogger(__name__)

User = get_user_model()

MAX_TOOL_CALLS_REACHED_MESSAGE = "I wasn't able to complete this fully — too many steps required."
GENERATION_FAILED_MESSAGE = "I'm having trouble answering right now. Please try again in a moment."


class AssistantReply(NamedTuple):
    text: str
    pending_action: Optional[dict]


def _default_proposal_text(pending_action):
    return (
        f"I'd like to update {pending_action['product_name']}'s {pending_action['field']} "
        f"from {pending_action['current_value']} to {pending_action['new_value']}. "
        "Please confirm to proceed."
    )


def run_with_tools(
    prompt,
    system,
    tool_definitions,
    tool_executors,
    seller,
    max_tool_calls=5,
    proposal_tool_names=frozenset(),
):
    """Run the tool-calling loop for a single turn. Returns an AssistantReply.

    Ownership is enforced structurally: every executor is called with
    `seller=seller` as a keyword argument the model never supplies or sees,
    so a tool cannot be tricked into returning another seller's data no
    matter what arguments the model passes.

    Tools named in `proposal_tool_names` never mutate anything themselves -
    they return a structured proposal. As soon as one succeeds, the loop
    captures it, makes one final call so the model can phrase a confirmation
    sentence, and returns - it never lets the model chain further tool calls
    once a mutation has been proposed. The proposal is only ever carried out
    by a separate, explicit confirmation step outside this loop entirely.
    """
    messages = [{"role": "user", "content": prompt}]
    tool_calls_made = 0

    while True:
        try:
            response = call_with_tools(messages, system, tool_definitions)
        except LLMGenerationError:
            logger.error("Seller assistant call_with_tools() failed for seller_id=%s", seller.id, exc_info=True)
            return AssistantReply(GENERATION_FAILED_MESSAGE, None)

        tool_use_blocks = [block for block in response.content if block.type == "tool_use"]
        text_blocks = [block for block in response.content if block.type == "text"]

        if not tool_use_blocks:
            return AssistantReply("".join(block.text for block in text_blocks), None)

        if tool_calls_made + len(tool_use_blocks) > max_tool_calls:
            logger.warning("Seller assistant hit max_tool_calls=%s for seller_id=%s", max_tool_calls, seller.id)
            return AssistantReply(MAX_TOOL_CALLS_REACHED_MESSAGE, None)

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        pending_action = None
        for block in tool_use_blocks:
            try:
                result = tool_executors[block.name](seller=seller, **block.input)
                if block.name in proposal_tool_names and pending_action is None:
                    pending_action = result
            except Exception as e:
                logger.warning("Tool %s failed for seller_id=%s: %s", block.name, seller.id, e, exc_info=True)
                result = f"Error calling {block.name}: {str(e)}"
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(result),
            })
            tool_calls_made += 1

        messages.append({"role": "user", "content": tool_results})

        if pending_action is not None:
            try:
                closing_response = call_with_tools(messages, system, tool_definitions)
                closing_text = "".join(
                    block.text for block in closing_response.content if block.type == "text"
                )
            except LLMGenerationError:
                logger.error(
                    "Seller assistant closing call_with_tools() failed for seller_id=%s", seller.id, exc_info=True
                )
                closing_text = ""
            return AssistantReply(closing_text or _default_proposal_text(pending_action), pending_action)
