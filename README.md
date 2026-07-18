# SmartKart Backend

A multi-vendor e-commerce REST API where sellers list products and buyers browse, cart, and checkout - with real inventory management, multi-seller orders, and S3-based image handling. Built with Django REST Framework and PostgreSQL.

**Live API:** https://smartkart-backend-p74d.onrender.com/api
**Interactive API Docs:** https://smartkart-backend-p74d.onrender.com/api/docs/
**Frontend:** [smartkart-frontend](https://github.com/AYShukla05/smartkart-frontend)

> **Note:** The API runs on Render's free tier and sleeps after inactivity. The first request may take up to 60 seconds to wake up - subsequent requests are fast.

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

---

## Tech Stack

| Layer | Technology | Why This Choice |
|---|---|---|
| Framework | Django 6.0 + DRF 3.16 | Mature ORM, built-in admin, strong serialization layer |
| Database | PostgreSQL (Neon) | ACID transactions needed for checkout, `select_for_update` for row locking |
| Auth | JWT via SimpleJWT | Stateless auth for a decoupled SPA frontend, token rotation for security |
| Image Storage | AWS S3 (presigned URLs) | Frontend uploads directly to S3 - backend never handles file bytes, only stores URLs |
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

Full interactive documentation at `/api/docs/`.

---

## Data Model

```
User (email, role: BUYER|SELLER, is_staff)
 ├── Product (seller FK) ──→ Category (FK, PROTECT)
 │    └── ProductImage (image_url, is_thumbnail)
 ├── Cart (buyer 1:1, created lazily)
 │    └── CartItem (product FK, quantity) [unique together: cart+product]
 └── Order (buyer FK)
      └── OrderItem (product FK, seller FK, quantity, price_at_purchase)
```

---

## Production Configuration

- **Rate limiting:** 60 req/min anonymous, 120 req/min authenticated
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
```

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

