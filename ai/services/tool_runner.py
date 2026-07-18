import logging

from django.contrib.auth import get_user_model

from ai.services.llm_client import LLMGenerationError, call_with_tools

logger = logging.getLogger(__name__)

User = get_user_model()

MAX_TOOL_CALLS_REACHED_MESSAGE = "I wasn't able to complete this fully — too many steps required."
GENERATION_FAILED_MESSAGE = "I'm having trouble answering right now. Please try again in a moment."


def run_with_tools(prompt, system, tool_definitions, tool_executors, seller, max_tool_calls=5):
    """Run the tool-calling loop for a single turn. Returns the model's final text response.

    Ownership is enforced structurally: every executor is called with
    `seller=seller` as a keyword argument the model never supplies or sees,
    so a tool cannot be tricked into returning another seller's data no
    matter what arguments the model passes.
    """
    messages = [{"role": "user", "content": prompt}]
    tool_calls_made = 0

    while True:
        try:
            response = call_with_tools(messages, system, tool_definitions)
        except LLMGenerationError:
            logger.error("Seller assistant call_with_tools() failed for seller_id=%s", seller.id, exc_info=True)
            return GENERATION_FAILED_MESSAGE

        tool_use_blocks = [block for block in response.content if block.type == "tool_use"]
        text_blocks = [block for block in response.content if block.type == "text"]

        if not tool_use_blocks:
            return "".join(block.text for block in text_blocks)

        if tool_calls_made + len(tool_use_blocks) > max_tool_calls:
            logger.warning("Seller assistant hit max_tool_calls=%s for seller_id=%s", max_tool_calls, seller.id)
            return MAX_TOOL_CALLS_REACHED_MESSAGE

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in tool_use_blocks:
            try:
                result = tool_executors[block.name](seller=seller, **block.input)
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
