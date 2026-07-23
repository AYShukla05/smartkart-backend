# What one message to the assistant actually costs

Plain-language walkthrough of how the buyer and seller AI assistants get billed — using **measured token counts from Anthropic's `count_tokens` API**, not estimates. An earlier version of this document guessed at the numbers from character counts and was wrong in two ways: it used one blended "~500 token" overhead figure for both assistants, and that figure itself was roughly half the buyer assistant's real cost and a fifth of the seller assistant's. Both are corrected below, and several real cost cuts have already been applied to the code.

Pricing used throughout: **Claude Haiku 4.5** at $1.00 per million input tokens, $5.00 per million output tokens (current as of this writing — check [platform.claude.com/docs/en/about-claude/pricing](https://platform.claude.com/docs/en/about-claude/pricing) for the latest).

**On cost framing:** this is a portfolio project with no revenue — every dollar spent is pure outflow, converted at whatever your card's forex rate is (typically a 1–3.5% markup, plus 18% GST on *that markup fee* specifically, not the whole charge). There's no "it's cheap in absolute terms" free pass here; if a lever can be pulled without hurting reliability, it should be.

## The words, defined plainly

**Turn** — One round trip you can see: you send a message, the assistant sends back a reply. One turn can involve more than one call to the model behind the scenes.

**Tool call** — Mid-turn, the assistant sometimes needs real data (your orders, a product's stock) that it doesn't already know. It pauses, asks the database a question, reads the answer, then writes its reply. Each pause is a *separate*, full call to Claude: one to decide "I need to look this up," one to write the answer once it has the data. *Like a waiter ducking into the kitchen to check if a dish is available, then coming back to tell you.*

**Tokens (input / output)** — Tokens are the chunks the model reads and writes, roughly ¾ of a word each. **Input** tokens are everything sent *to* the model: your message, the instructions it always gets, the tool schemas, the conversation history, any data a tool looked up. **Output** tokens are what it writes back. Output costs 5× more per token than input on this model.

**Prompt caching** — If the exact same block of text sits at the start of two requests in a row, Anthropic can skip re-reading it and charge a fraction of the price. It only kicks in once that repeated block crosses a minimum size — **4,096 tokens on Haiku 4.5** (an earlier version of this doc said 2,048, which was wrong). Below that, caching quietly does nothing.

## What's actually inside every single call — measured, not guessed

Every call to the model carries a fixed passenger load: the system instructions plus every tool's schema (name, description, parameters) — resent in full on **every** call, including a second time if a tool call happens mid-turn. Measured directly via `client.messages.count_tokens(...)` against the real prompts and tool definitions in this repo:

| Assistant | Tools | Fixed overhead (system + tools) |
|---|---:|---:|
| Buyer order assistant | 3 | **~950 tokens** |
| Seller assistant | 15 (11 lookups + 4 propose) | **~2,520 tokens** |

The seller assistant costs **~2.7× more per call** than the buyer assistant, purely from carrying five times as many tools. This wasn't visible in the earlier version of this doc because it used one guessed number for both.

> Prompt caching is already switched on in the code (`cache_control` on the system block), but neither assistant crosses Haiku's real 4,096-token minimum yet — even the seller assistant's ~2,520 tokens falls short. Confirmed by making two real back-to-back calls and checking `usage.cache_read_input_tokens`: it came back `0` both times. Caching is not currently saving anything for either assistant — this was verified, not assumed.

## Changes already made

These are live in the code, not proposals:

1. **Conversation memory cut further: 20 messages → 5** (`ai/services/context.py`, `HISTORY_WINDOW`). Still covers the "tell me more about that" / "update it" follow-ups the feature is built around, but memory is now genuinely short — a note in both chat UIs tells the user this directly, so a forgotten detail after a long gap isn't a silent surprise.
2. **Both system prompts trimmed** (`ai/prompts.py`) — same behavioral guardrails, fewer words.
3. **Seller tool descriptions trimmed** (`ai/tools/seller_tools.py`, `ai/tools/seller_actions.py`) — cut the seller assistant's tool-schema weight from ~2,723 to ~2,522 tokens (~7%), while keeping every disambiguation phrase intact (e.g. `get_low_stock_products` vs. `get_lowest_stock_products`) and every mutation-safety phrase intact ("this does NOT apply until confirmed," "never say it's already updated"). Verified live afterward: fired the four ambiguous/ordinary queries this risked breaking, and every one still triggered the correct tool.
4. **Output ceiling tightened defensively: 1000 → 700** (`ai/services/llm_client.py`, `call_with_tools`). This bounds worst-case spend on a runaway/degenerate response — it does **not** reduce typical-case cost, since you're billed for tokens actually produced, not the ceiling. Normal replies (60–150 tokens) are unaffected.

## A worked example, per assistant

Rounded for readability; treat as "about this much."

**Seller assistant — 3 turns** (product lookup → detail lookup → price-change proposal, all needing a tool call):

| Turn | Input tokens | Output tokens | Cost |
|---|---:|---:|---:|
| "Which of my products is most popular?" | ~5,350 | ~125 | $0.0060 |
| "What's the price and stock of it?" | ~5,450 | ~105 | $0.0060 |
| "Update its price to $X" | ~5,550 | ~105 | $0.0061 |
| **Total** | | | **~$0.018** |

**Buyer assistant — 3 turns** (order lookup → order detail → product search, all needing a tool call):

| Turn | Input tokens | Output tokens | Cost |
|---|---:|---:|---:|
| "What did I order last month?" | ~2,240 | ~120 | $0.0028 |
| "Tell me more — who was the seller?" | ~2,350 | ~105 | $0.0029 |
| "Find me something waterproof for hiking" | ~2,800 | ~170 | $0.0037 |
| **Total** | | | **~$0.0094** |

The seller assistant runs almost **2× the buyer assistant's cost** for a similar-length conversation — entirely due to its larger tool set, not conversation length or tool-call frequency.

## Scaling it up

Assuming a 50/50 mix of buyer and seller conversations, ~3 turns each, most turns needing a tool call (a reasonably pessimistic assumption — plenty of turns are answered directly with no lookup and cost less):

| Conversations (split evenly) | Est. total cost |
|---|---:|
| 1,000 | ~$13.75 |
| 10,000 (stress-test) | ~$137.50 |

At a rough ₹84/USD, that's **~₹1,155/month** at 1,000 conversations and **~₹11,550/month** at the stress-test row — real money for a project with no revenue behind it, which is why the cuts above were worth making rather than waving off.

Both endpoints are also rate-limited per user (10/minute buyer, 5/minute seller), which caps how fast any single account can run up spend regardless of the above.

## What's left on the table

Ranked by impact. The first two are already done above; these are what's left if further cuts are wanted.

**Consolidate the seller's tool set** — *not yet done, biggest remaining lever.* The seller assistant's 15 tools are the dominant cost (94% of its fixed overhead is tool schemas, not the system prompt). Several pairs could plausibly merge into one tool with a parameter — e.g. `get_low_stock_products` (threshold-based) and `get_lowest_stock_products` (ranked, no threshold) could become one tool with an optional `threshold` argument. This is a real design change to the tool surface, not a wording trim, so it's flagged here rather than done silently — say the word if you want this pursued, along with an estimate of which pairs are safe to merge without losing the disambiguation Haiku currently relies on.

**Shrink the history window even further** — already cut from 20 → 5. Diminishing returns from here; much shorter and the "tell me more about that" follow-ups this feature is built around stop working reliably.

**Switch models** — not applicable. Haiku 4.5 is already Anthropic's cheapest current-generation model.

**Batch API** — not applicable. It's priced for workloads that can wait up to 24 hours; both assistants answer a user waiting in a live chat.

**Force prompt caching by padding the prompt** — not worth it. Padding to cross the 4,096-token minimum costs as much as the content it would "save," and neither assistant is remotely close to that threshold even after the seller tool-schema trim.

## Bottom line

Real cuts landed: history halved, both system prompts trimmed, seller tool descriptions cut ~7% with tool-selection reliability verified afterward, and a defensive ceiling on worst-case output. Estimated savings from these alone are roughly 15–20% off what the two conversations above would have cost pre-changes. The one lever left with real remaining impact is consolidating the seller assistant's tool set — that's a design change to what tools exist, not a free trim, so it's a decision to make rather than something already applied.
