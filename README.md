# SmartKart Backend

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-6.0-092E20?logo=django&logoColor=white)
![DRF](https://img.shields.io/badge/DRF-3.16-A30000)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Neon-4169E1?logo=postgresql&logoColor=white)
![Claude](https://img.shields.io/badge/LLM-Claude_Haiku_4.5-D97757)
![Tests](https://img.shields.io/badge/tests-167_passing-brightgreen)
![Deployment](https://img.shields.io/badge/deployed-Render-46E3B7?logo=render&logoColor=white)

A multi-vendor e-commerce REST API where sellers list products and buyers browse, cart, and checkout - with real inventory management, multi-seller orders, S3-based image handling, and four composed AI features. Built with Django REST Framework and PostgreSQL.

**Live API:** https://smartkart-backend-p74d.onrender.com/api
**Interactive API Docs:** https://smartkart-backend-p74d.onrender.com/api/docs/
**Frontend:** [smartkart-frontend](https://github.com/AYShukla05/smartkart-frontend)

> **Note:** The API runs on Render's free tier and sleeps after inactivity. The first request may take up to 60 seconds to wake up - subsequent requests are fast.

---

### Contents

[What It Does](#what-it-does) · [AI Features](#ai-features) · [Tech Stack](#tech-stack) · [Architecture Decisions](#architecture-decisions) · [Project Structure](#project-structure) · [API Overview](#api-overview) · [Data Model](#data-model) · [Testing](#testing) · [Production Configuration](#production-configuration) · [Local Setup](#local-setup)

---

## Highlights

- **Atomic, race-condition-safe checkout** across multi-seller orders, via `select_for_update()` row locking - not just an ORM `.save()` call
- **Four AI features that build on each other**, not four disconnected demos: streamed description generation feeds the embeddings behind semantic search, which the seller and buyer assistants both call as a tool
- **AI mutations can never bypass a human**: the model only ever *proposes* a change; a separate, LLM-unreachable endpoint independently re-validates ownership and current state before writing anything
- **167 backend tests, 41 frontend tests, all passing** - plus LLM cost measured with Anthropic's `count_tokens` API rather than estimated (`docs/ai-costing.md`)

---

## What It Does

SmartKart is a marketplace backend that supports three user roles:

- **Buyers** browse products, manage a server-side cart, and place orders through an atomic checkout flow
- **Sellers** manage their own product catalog with images and view orders/revenue for their sold items
- **Admins** manage product categories

A single order can contain products from multiple sellers. Each seller only sees the portion of the order relevant to them.

---

## AI Features

**AI Product Description Generator** - turns a name, category, and price into a complete listing; no seller copywriting required.
- Title, description, bullet points, and SEO keywords generated in one pass
- Streams token-by-token from Claude Haiku 4.5 (Anthropic, Server-Sent Events) - no blocking wait
- Generated keywords persist and double as embedding input for semantic search - one AI call improves both the listing and its discoverability
- Cost bounded by design: per-seller rate limit, hard token cap, and a batch-enrichment command that prices itself out before spending a cent

**Semantic Search** - understands what a buyer means, not just what they typed.
- "protect my phone screen from cracking" → screen protectors for phones and smartwatches as top confident matches, zero shared literal words (a keyword search on the same phrase returns nothing)
- Voyage AI embeddings, ranked via pgvector's HNSW index (cosine similarity) at catalog scale, against a confidence threshold tuned empirically - not guessed
- Confident and "related" results split visually instead of one undifferentiated list; a narrow query still returns a useful floor instead of a blank page
- Zero matches in a chosen category retries catalog-wide automatically; keyword search takes over if embeddings are ever unavailable

**AI Seller Assistant** - a tool-calling agent scoped to one seller's own catalog, not a generic chatbot.
- Hand-rolled agentic loop (`run_with_tools`, no LangChain/LlamaIndex) - the model calls read tools (stats, top sellers, category breakdown, stock forecast, product lookup by name, etc.) and chains as many as it needs before answering
- Mutations (stock/price update, activate/deactivate, create listing) are never executed by the model - it can only *propose* a structured change, surfaced as a confirm card; a separate, LLM-unreachable endpoint re-validates ownership and current state before writing anything
- A single message can chain multiple proposals (e.g. "set stock to 50 and drop the price to 25") - each renders and confirms independently
- Conversation memory (last 5 messages) plus a last-referenced-product cache, so a follow-up like "what's the price now?" resolves without restating the product name or paying for a repeat lookup tool call

**Conversational Order Assistant** - a buyer-facing chat that composes the description generator's prompting, semantic search, and the seller assistant's tool-calling loop into one multi-turn feature.
- Answers "where's my order," "what did I order last time," and open-ended product questions in the same thread, backed by `get_my_orders`, `get_order_detail`, and `search_products` tools
- Genuine multi-turn memory scoped per conversation - `Conversation`/`Message` persist history server-side, capped at the last 5 messages for cost, with ownership enforced on every retrieval (`user=request.user`, never trusted from the client)
- Cost-engineered, not guessed: real per-assistant token counts pulled via Anthropic's `count_tokens` API drove the history window and prompt sizes down (written up in `docs/ai-costing.md`)

---

## Tech Stack

| Layer | Technology | Why This Choice |
|---|---|---|
| Framework | Django 6.0 + DRF 3.16 | Mature ORM, built-in admin, strong serialization layer |
| Database | PostgreSQL (Neon) | ACID transactions needed for checkout, `select_for_update` for row locking |
| Auth | JWT via SimpleJWT | Stateless auth for a decoupled SPA frontend, token rotation for security |
| Image Storage | AWS S3 (presigned URLs) | Frontend uploads directly to S3 - backend never handles file bytes, only stores URLs |
| LLM | Claude Haiku 4.5 (Anthropic) | Description generation, streaming, and both tool-calling assistants - a single `llm_client.py` is the only file that imports the `anthropic` SDK |
| Embeddings | Voyage AI + pgvector (HNSW) | Semantic search over the product catalog, cosine similarity at catalog scale |
| API Docs | drf-spectacular (OpenAPI 3.0) | Auto-generated Swagger UI from serializers and views |
| Deployment | Render | Free tier with managed PostgreSQL via Neon |

---

## Architecture Decisions

### Atomic checkout with row-level locking
The checkout endpoint wraps the entire flow in `transaction.atomic()` and uses `select_for_update()` on product rows. This prevents a race condition where two buyers could simultaneously purchase the last item - the second request blocks until the first completes, then fails stock validation cleanly.

### Price captured at purchase time
`OrderItem` stores `price_at_purchase` rather than referencing the product's current price. This ensures order history remains accurate even if a seller changes their prices later.

### S3 presigned URL pipeline
Instead of the backend receiving and forwarding image files (which consumes server memory and bandwidth), the flow is:
1. Seller requests a presigned URL from the backend
2. Backend generates a time-limited S3 PUT URL and returns it
3. Frontend uploads directly to S3
4. Frontend tells the backend the final URL to store

This keeps the backend stateless and lightweight.

### Ownership enforcement at the query level
Seller endpoints filter by `product__seller=request.user` at the queryset level, not in view logic. This means a seller literally cannot access another seller's products even if they guess the ID - the queryset returns a 404, not a 403.

### Server-side cart
Cart state lives in the database, not in browser storage. This means the cart persists across devices and sessions, and stock validation happens server-side where it can't be bypassed.

### Propose → confirm for every AI-driven mutation
Neither AI assistant gives the model a tool that writes to the database. The model can only call a `propose_*` tool that returns a structured, unexecuted change; that proposal is rendered to the user as a confirm card, and only an explicit follow-up HTTP call to a separate, LLM-unreachable endpoint performs the actual mutation - re-deriving current state and re-checking ownership itself rather than trusting anything the model or client claims. This keeps "the model decided to change something" and "the change actually happened" as two independently auditable steps.

```mermaid
sequenceDiagram
    participant Seller
    participant UI as Chat UI
    participant API as SellerAssistantView
    participant LLM as Claude (tool-calling loop)
    participant DB as Database

    Seller->>UI: "Drop the price to $25"
    UI->>API: POST /seller-assistant/
    API->>LLM: prompt + tool schemas
    LLM->>API: propose_price_update(product, 25)
    Note over API,DB: No write happens here
    API-->>UI: pending action
    UI-->>Seller: Confirm card rendered
    Seller->>UI: Confirm
    UI->>API: POST /confirm-action/
    API->>DB: re-check ownership, apply update
    DB-->>API: committed
    API-->>UI: confirmed
```

### Actor-agnostic tool-calling loop
`run_with_tools()` takes a generic `actor` plus an `actor_kwarg` (defaulting to `"seller"`), so every tool executor is called as `tool_executors[name](**{actor_kwarg: actor}, **args)`. The seller assistant and the buyer order assistant share one loop implementation without either one's tools needing to agree on a parameter name - the generalization was added only when a second role (buyer) actually needed it, not speculatively.

---

## Project Structure

```
smartkart-backend/
├── authentication/      # JWT login, register, refresh, logout
├── users/               # Custom User model (email-based), role permissions
├── categories/          # Admin-managed product categories
├── products/            # Seller product CRUD, image management, S3 utilities
├── cart/                # Server-side buyer cart
├── orders/              # Checkout (atomic), buyer/seller order views, stats
├── ai/                  # Description generation, semantic search, seller & order assistants
│   ├── services/        # llm_client (Anthropic), tool_runner (agentic loop), search, context (history)
│   ├── tools/           # seller_tools, seller_actions (propose/execute), buyer_tools
│   ├── models.py        # ProductEmbedding, Conversation, Message
│   └── prompts.py       # System prompts for description gen, seller & order assistants
└── smartkart/           # Settings, URL routing, pagination config
```

Each app is self-contained with its own models, serializers, views, and URLs.

---

## API Overview

| Domain | Key Endpoints | Access |
|---|---|---|
| **Auth** | `POST login/`, `register/`, `refresh/` | Public |
| **Users** | `GET me/` | Authenticated |
| **Categories** | `GET /` (public), `POST/PATCH/DELETE` (admin) | Mixed |
| **Products** | `GET /` (public browse with search, filter, sort), `GET/POST/PATCH/DELETE my/` (seller CRUD) | Mixed |
| **Images** | `POST my/{id}/images/presign/`, `POST my/{id}/images/`, `DELETE` | Seller |
| **Cart** | `GET /`, `POST items/`, `PATCH/DELETE items/{id}/` | Buyer |
| **Orders** | `POST checkout/`, `GET /` (buyer history), `GET seller/` (seller orders), `GET seller/stats/` | Role-based |
| **AI** | `POST ai/generate-description/` (streaming), `POST ai/seller-assistant/`, `POST ai/seller-assistant/confirm-action/`, `POST ai/seller-assistant/record-outcomes/`, `POST ai/order-assistant/` | Seller / Buyer |

Full interactive documentation at `/api/docs/`.

---

## Data Model

```
User (email, role: BUYER|SELLER, is_staff)
 ├── Product (seller FK) ──→ Category (FK, PROTECT)
 │    ├── ProductImage (image_url, is_thumbnail)
 │    └── ProductEmbedding (product 1:1, 512-dim vector, model_id)
 ├── Cart (buyer 1:1, created lazily)
 │    └── CartItem (product FK, quantity) [unique together: cart+product]
 ├── Order (buyer FK)
 │    └── OrderItem (product FK, seller FK, quantity, price_at_purchase)
 └── Conversation (user FK, last_product FK nullable, shared by seller/buyer assistants)
      └── Message (conversation FK, role: user|assistant, content)
```

---

## Testing

**167 tests, all passing** (`python manage.py test`). Coverage includes:

- Ownership scoping on every seller/buyer-facing query - cross-seller and cross-buyer access consistently returns 404, never a leaked row
- Checkout's row-locking behavior and stock-validation edge cases
- Every AI tool: ranking/aggregation math, `days`-window filtering, inactive-product/category exclusion
- The tool-calling loop itself - text-only replies, tool chaining, `max_tool_calls` enforcement, executor exceptions surfaced as tool-result errors instead of raised, multi-proposal accumulation in a single turn
- Full HTTP flows for both assistants: auth (401/403), throttling (429), conversation persistence and ownership, and all four mutation types including invalid-value rejection

Every AI tool was additionally verified against real seeded data through the Django shell and the live endpoints with natural-language questions, not just the automated suite.

---

## Production Configuration

- **Rate limiting:** 60 req/min anonymous, 120 req/min authenticated; AI endpoints scoped separately (5/min seller assistant, 20/min action confirms, 10/min order assistant, 10/min description generation)
- **Pagination:** Configurable per endpoint (12 products/page, 10 orders/page)
- **Security headers:** HSTS, SSL redirect, secure cookies (when `DEBUG=False`)
- **Query optimization:** `select_related`/`prefetch_related` on all list views, database indexes on filtered fields
- **Static files:** WhiteNoise with Brotli/gzip compression

---

## Local Setup

```bash
git clone git@github.com:AYShukla05/smartkart-backend.git
cd smartkart-backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file:

```env
SECRET_KEY=your-secret-key
DEBUG=True
DATABASE_URL=postgresql://user:password@host:port/dbname
AWS_ACCESS_KEY_ID=your-key
AWS_SECRET_ACCESS_KEY=your-secret
AWS_S3_BUCKET_NAME=smartkart-images
AWS_S3_REGION=ap-south-1
ANTHROPIC_API_KEY=your-key
VOYAGE_API_KEY=your-key
```

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

