# 5 messages (2-3 exchange pairs) - this history gets resent on every single
# model call, so a smaller window shrinks the growing-history portion of the
# bill on every turn. Still covers the "tell me more about that" / "update
# it" follow-ups this feature is built around, but memory is now genuinely
# short - the frontend surfaces a note about this so it isn't a silent
# surprise. Bump it back up if this is too short in practice.
HISTORY_WINDOW = 5


def build_message_history(conversation):
    """
    Return the conversation's prior turns formatted for the Anthropic
    messages API - naive last-N strategy, no summarisation. Only ever plain
    text turns: tool call/result pairs are never persisted to `Message`, so
    there's nothing else to assemble.
    """
    messages = conversation.get_recent_messages(n=HISTORY_WINDOW)
    return [
        {"role": m.role, "content": m.content}
        for m in messages
    ]
