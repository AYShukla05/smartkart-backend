import logging
from typing import NamedTuple

from django.contrib.auth import get_user_model

from ai.services.llm_client import LLMGenerationError, call_with_tools

logger = logging.getLogger(__name__)

User = get_user_model()

MAX_TOOL_CALLS_REACHED_MESSAGE = "I wasn't able to complete this fully — too many steps required."
GENERATION_FAILED_MESSAGE = "I'm having trouble answering right now. Please try again in a moment."


class AssistantReply(NamedTuple):
    text: str
    pending_actions: list


def _describe_proposal(action):
    if action.get("summary"):
        return f"create a new listing: {action['product_name']} — {action['summary']}"
    return (
        f"update {action['product_name']}'s {action['field']} "
        f"from {action['current_value']} to {action['new_value']}"
    )


def _default_proposal_text(pending_actions):
    if len(pending_actions) == 1:
        return f"I'd like to {_describe_proposal(pending_actions[0])}. Please confirm to proceed."
    lines = "\n".join(f"- {_describe_proposal(action)}" for action in pending_actions)
    return f"I'd like to make the following changes:\n{lines}\nPlease confirm to proceed."


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
    they return a structured proposal. Every proposal made during the turn is
    collected into `pending_actions`; the model is free to keep chaining
    further tool calls afterwards (more proposals, or lookups) up to
    max_tool_calls, so asking for two changes in one message can surface two
    confirm cards. None of them are ever carried out here - each is only
    ever executed by a separate, explicit confirmation step outside this
    loop entirely.
    """
    messages = [{"role": "user", "content": prompt}]
    tool_calls_made = 0
    pending_actions = []

    while True:
        try:
            response = call_with_tools(messages, system, tool_definitions)
        except LLMGenerationError:
            logger.error("Seller assistant call_with_tools() failed for seller_id=%s", seller.id, exc_info=True)
            return AssistantReply(GENERATION_FAILED_MESSAGE, pending_actions)

        tool_use_blocks = [block for block in response.content if block.type == "tool_use"]
        text_blocks = [block for block in response.content if block.type == "text"]

        if not tool_use_blocks:
            text = "".join(block.text for block in text_blocks)
            if not text and pending_actions:
                text = _default_proposal_text(pending_actions)
            return AssistantReply(text, pending_actions)

        if tool_calls_made + len(tool_use_blocks) > max_tool_calls:
            logger.warning("Seller assistant hit max_tool_calls=%s for seller_id=%s", max_tool_calls, seller.id)
            return AssistantReply(MAX_TOOL_CALLS_REACHED_MESSAGE, pending_actions)

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in tool_use_blocks:
            try:
                result = tool_executors[block.name](seller=seller, **block.input)
                if block.name in proposal_tool_names:
                    pending_actions.append(result)
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
