# Part A - Seeding Realistic Data (Simple Walkthrough)

## What we're doing and why

Right now the database has a handful of real products and orders - enough to prove the app works, not enough to make an AI feature look meaningful. "Search for wireless headphones" over 5 products isn't a real demo. Over 8,000 products, it is.

So before touching any AI code, we're writing a script that fills the database with realistic, fake-but-believable data: thousands of buyers and sellers, thousands of products across real categories, and thousands of orders spread out over months - including orders that mix products from multiple sellers, since that's a real feature of this app.

This is a one-time (or re-runnable) command, not a permanent part of the app. It never runs automatically - you run it by hand when you want to (re-)populate data.

## The big picture

Four kinds of data, and they have to be created **in this order**, because each one depends on the one before it:

```
1. Users (buyers + sellers)
        ↓
2. Categories (Electronics, Books, etc.)
        ↓
3. Products (each needs a seller AND a category to already exist)
        ↓
4. Orders (each needs real products and real buyers to already exist)
```

You can't create a product before you have a seller to own it, and you can't create an order before you have products to put in it. So the script does these four things as four separate steps, in this order, each one fully finished before the next starts.

## What each step actually does, in plain terms

**Step 1 - Users.** We create ~5,000 fake buyer accounts and ~250 fake seller accounts (that ratio - way more buyers than sellers - matches how real marketplaces actually look). All the fake accounts share one email pattern (`buyer0001@seed.smartkart.dev`, `seller0001@seed.smartkart.dev`, and so on) and one shared demo password. That email pattern isn't just for looks - it's how we mark "this data was created by the seed script," which matters in step 5.

**Step 2 - Categories.** We hand-write a list of ~20 realistic categories (Electronics, Fashion, Books, Home & Kitchen, etc.) rather than generating them randomly - a random tool can't invent a sensible category list, so this one's just typed out directly in the code.

**Step 3 - Products.** For each of the 250 sellers, we generate a handful to a few hundred products (some sellers have way more listings than others, which is realistic), each assigned to a real category with a realistic price for that category (a "Jewelry" item and a "Grocery" item shouldn't be priced the same way). We deliberately leave the product **descriptions** mostly blank or minimal at this stage - that's not laziness, that's on purpose, because filling those in with real AI-generated text is literally Phase 1 of your roadmap. Seeding blank descriptions now means Phase 1 has a real, large catalog to demonstrate itself on.

For product images, we upload **real files to S3**, the same as a real seller would end up with - just via a different path to get there in bulk. Here's the reasoning and the mechanics:

- Uploading a genuinely unique real photo for every one of 8,000+ products isn't practical (free image sources rate-limit you, and it'd make the script slow) - but reusing one small pool of stock photos per category is both realistic (real catalogs reuse stock photography constantly) and fast. So the script downloads a pool of ~30-40 real photos *per category* once (about 700-800 images total across ~20 categories), uploads each one to S3 exactly once, and then randomly assigns 1-3 of the category-appropriate images to each product.
- **The image source matters, and it changed from the earlier draft of this plan.** A purely random photo service (like `picsum.photos`) has no idea what "Electronics" means - it just hands you an arbitrary photo, so nothing would stop a couch photo from landing in the Electronics pool. Instead, we use an image API that supports **keyword search** (recommended default: [Pexels](https://www.pexels.com/api/), free tier - 200 requests/hour, 20,000/month, more than enough for a one-time pool build), and search each category using a hand-written keyword mapped to it in `constants.py` (e.g. Electronics → `"electronics gadgets"`, Furniture → `"furniture couch chair"`, Jewelry → `"jewelry rings necklace"`). This needs a free Pexels API key, stored as a new env var (`PEXELS_API_KEY`), the same pattern as the existing AWS credentials.
- The upload itself skips the normal browser-facing "get a presigned URL, then PUT from JavaScript" flow - that exists specifically for a real seller's browser to talk to S3 without your backend touching the file bytes. Here, the script runs on the backend directly, so it uses the same underlying S3 client (`get_s3_client()` from `products/s3_utils.py`) to upload straight to the bucket, no presigning needed.
- These pooled images live under their own S3 prefix (e.g. `seed-images/{category-slug}/`), kept separate from the `products/{product_id}/...` prefix real seller uploads use - so there's no ambiguity later about which images came from a real seller action versus the seed script.
- New dependency needed: `requests` (for downloading the source images) - not currently in `requirements.txt`. `Pillow`/image conversion is **not** needed for this - we upload the downloaded JPEGs as-is rather than converting to WebP; that keeps the script simple, and if format consistency with real uploads ever matters later, that's a cheap thing to add.
- This does mean real (very small) S3 storage costs instead of zero - a few hundred images at typical stock-photo sizes is negligible, but it's not free the way hotlinking was, worth knowing going in.

### Verifying the images are actually correct

A keyword search gets you *most* of the way there, but "trust the search API's relevance" isn't a verification step, it's a hope. So every image the pool downloads gets recorded in a small new tracking table - a `SeedImagePoolItem` model in the `seeding` app (category, the S3 key it was uploaded to, and the original source URL it came from). This table does three jobs at once:

1. **It's the thing you actually look at to verify correctness.** Registered in Django admin with a thumbnail preview, filterable by category - since the whole pool is only ~700-800 images (not thousands), skimming through it once, organized by category, takes a few minutes and is enough to catch an obvious couch-in-Electronics outlier before it gets reused across hundreds of products. This is a **one-time manual pass over the pool**, not something you re-check per product - because every product just references pool images, catching a mistake once at the pool level fixes it everywhere downstream.
2. It's how the product-seeding step actually picks images - "give me images for this category" becomes a real, precise database query against this table instead of a vague notion of "the pool."
3. It's how cleanup knows exactly what it's allowed to delete later (see the `--reset` fix below).

We're deliberately *not* reaching for an automated image-classification step to verify content (running every downloaded photo through a vision model to confirm "yes, this is furniture") - for a one-time, ~700-image pool, that's disproportionate cost and complexity next to just looking at a thumbnail grid once.

### What happens when a download or upload fails

This step talks to two external things (the Pexels API, then S3) roughly 700-800 times each, over a network - something *will* fail partway through eventually (a timeout, a transient 5xx, a rate limit), and the original plan didn't say what happens when it does. Here's the strategy:

- **A single image failing** (download or upload) gets a few retries with a short backoff, and if it still fails, it's logged and skipped - the script moves on to the next image rather than aborting the whole pool build over one bad file.
- **A whole category's search failing** (the initial "give me candidate photos for Furniture" call) is retried the same way, but if it's still failing after retries, that category is logged clearly as having no pool images *this run*, and its products simply get seeded with zero images for now - it does **not** silently fall back to reusing another category's images, because that would quietly reintroduce the exact couch-in-Electronics problem the search-based approach exists to prevent. A partial pool is an honest, visible gap you can re-run later; a mismatched fallback image is a silent one.
- **Rate-limit pacing, precisely** - checked directly against Pexels' own documentation rather than assumed: the 200/hour, 20,000/month quota applies **only to calls against `api.pexels.com`** (the `/search` endpoint) - it does not apply to downloading the actual photo files afterward, which come from a separate CDN host (`images.pexels.com`) and aren't metered against that quota at all. That changes the shape of the problem: this step makes ~20 quota-counted requests (one search per category), not ~700 - trivially fine against a 200/hour ceiling even with no pacing at all. The ~700 image downloads that follow aren't subject to that limit.
  That doesn't mean "no pacing anywhere" - it's still good practice not to fire hundreds of downloads at a third-party CDN in a tight burst, and a named, tunable constant beats a magic number buried in a loop either way. So: a `PEXELS_SEARCH_DELAY_SECONDS` constant (e.g. `1.0`, applied between the 20 search calls - pure safety margin, not mathematically required) and a much lighter `PEXELS_DOWNLOAD_DELAY_SECONDS` (e.g. `0.1`, applied between individual image downloads - courtesy pacing to the CDN, not quota compliance) both live in `constants.py`. Building the full pool this way still only takes a few minutes, but for a different reason than originally stated - it's now clear that's about being a considerate client, not about dodging a rate-limit wall that was never actually there for the download step.
- **Pool building runs as its own isolated step**, outside the database transactions that create users/products/orders - so a bad afternoon for an external image API can never block or roll back the far more important data seeding.

**Step 4 - Orders.** We generate thousands of orders spread across roughly the last 9-12 months (not all dumped on today's date), each containing 1-5 products picked from *anywhere* in the catalog - not just one seller's products. That last detail is the trick that makes multi-seller orders happen automatically: since we're not restricting product choice to one seller, some orders will naturally end up containing items from two or three different sellers, exactly like a real checkout would produce.

## Known demo accounts (separate from the bulk-random data)

Alongside the thousands of randomly generated accounts, we create a small, fixed set of **memorable** accounts specifically for live-demoing the AI features later - random seed data is realistic but useless for a demo, since you can't reliably find "a seller with a coherent history" by scrolling through 250 random ones.

- **1 demo buyer** - `demo.buyer@smartkart.dev`, with a documented, non-secret password (this is demo-only data, never treated as a real credential).
- **2 demo sellers** - `demo.seller1@smartkart.dev` / `demo.seller2@smartkart.dev`. Two, not one, so a demo order can genuinely span multiple known sellers - useful later for showing the multi-seller order handling working with accounts you can actually point to by name, not ones you have to go searching for.
- These are created through the normal `create_user()` path (not the bulk method) - it's only 3 accounts, so the performance reason for bulk-inserting doesn't apply here, and using the normal path is simpler and one less thing to get subtly wrong.
- **Each demo seller gets ~20-30 hand-curated products**, spread across 2-3 categories, with **real, complete descriptions written out** (not left blank like the bulk-seeded products) - these are meant to look finished and screenshot-ready immediately, not wait on Phase 1's AI generator to fill them in.
- **The demo buyer gets ~15-20 orders** spread across several months, deliberately mixing products from both demo sellers *and* some of the random bulk sellers - so Phase 4's conversational order assistant later has real, specific, memorable order history to answer questions about ("where's my order from last month") instead of having to dig through anonymous random data to demo it convincingly.

## The one tricky technical bit (worth knowing, not just trusting)

Two of the database fields - `Product.created_at` and `Order.created_at` - are set up to *automatically* stamp themselves with "right now" the moment a row is created, and they do this no matter what value you try to give them, even through the fast bulk-insert method we're using. Left alone, every single seeded product and order would show a `created_at` of the exact moment we ran the script - which would look completely fake (nobody adds 8,000 orders in one second).

The fix is simple: right before we bulk-insert products or orders, we briefly tell Django "don't auto-stamp this field this one time," write the batch with our own realistic historical dates, then turn the auto-stamping back on immediately after. It's a few lines of code, but it's the difference between "this data looks real" and "this data obviously isn't."

## How you'll actually run it

Once built, it works like this:

```
python manage.py seed_data --dry-run
```
This *always* prints two things before anything else, every single run, not as an afterthought: **which database it's about to write to** (the host and database name, read straight from Django's own connection settings - never the password, that field is simply never touched or printed) and the planned record counts. Nothing gets written. Always run this first, and actually read the host line - that's the whole point of it existing.

```
python manage.py seed_data --confirm
```
This actually writes the data, using sensible default amounts (you can override the numbers, e.g. `--buyers=5000 --products=8000`).

```
python manage.py seed_data --confirm --reset
```
This wipes out *only* the fake seed data (identified by that `@seed.smartkart.dev` email pattern from Step 1) and starts over - it can never touch or delete any of your real accounts or real orders, even by accident.

**Important distinction, fixed from the earlier draft**: `--reset` does **not** touch the S3 image pool or the `SeedImagePoolItem` rows tracking it, on purpose. The email-namespace scoping that makes `--reset` safe only ever covered user/product/order rows - it was never going to reach S3 objects living under `seed-images/`, which aren't tied to any user at all. Rather than bolt on S3 cleanup to `--reset` and make one flag do two very different jobs, the pool is treated as a separate, persistent, reusable asset: it's slow and mildly costly to rebuild (downloading and uploading ~700 real images), it doesn't need to change just because you're regenerating fake users and orders, and reusing it across resets is strictly better - faster reseeds, and no re-verification of the pool's correctness needed each time.

```
python manage.py seed_data --rebuild-image-pool
```
A separate, explicit, rarely-used command for the one case where you *do* want fresh photos: it deletes exactly the S3 objects the `SeedImagePoolItem` table says it owns (and only those - never anything else in the bucket), deletes those tracking rows, and re-runs the download/search/upload step from scratch.

Worth stating explicitly rather than leaving implicit: if a `SeedImagePoolItem` row exists but its S3 object was somehow already gone (deleted manually, bucket lifecycle rule, whatever), this doesn't fail or need special-case handling - S3's `delete_object` is idempotent, a delete against a key that doesn't exist returns success (204), not an error. So `--rebuild-image-pool` just works in that case, with nothing extra to code for it.

You can re-run the plain `--confirm` command safely any time - it tops up to the target numbers instead of duplicating everything.

## What "done" looks like

- Running `--dry-run` shows the correct target database host/name and accurate counts.
- After a real run, the database has thousands of realistic buyers, sellers, products, and orders - including some genuinely multi-seller orders.
- Order and product dates are spread across months, not all bunched at "now."
- Product images are real S3 objects with real S3 URLs (spot-check one in a browser) - not third-party hotlinks.
- You've done the one-time visual pass over the `SeedImagePoolItem` admin list and nothing is obviously miscategorized (no couch in Electronics).
- Any category that failed to get pool images is clearly logged, not silently papered over with a mismatched fallback.
- Running `--reset` and then `--confirm` again does **not** re-download or re-upload any images - the pool persists and just gets reused, confirming the separation from `--rebuild-image-pool` actually works as intended.
- The two known demo accounts exist, log in with their documented credentials, and have their curated (not random) history.
- **The skewed distributions actually look skewed, not flat.** "Some sellers have way more products than others" is a claim, not a fact, until you check it - a bug in the pareto logic could easily produce something close to uniform and you'd never notice just by browsing. So the seed command itself prints a short distribution summary at the end of every real run, something like:
  ```
  Products per seller - min: 1, median: 12, max: 340
  Top 20% of sellers own 61% of all products
  Orders per buyer   - min: 0, median: 1, max: 22
  Top 20% of buyers account for 58% of all orders
  ```
  As a rough sanity check: if the "top 20%" numbers come back close to 20% (meaning: not skewed, everyone has roughly the same amount), the distribution logic isn't actually working, even if nothing crashed. Somewhere in the 50-70% range is what a real pareto-ish skew should look like.
- Running the existing test suite (`manage.py test`) still passes - this script only adds data, it doesn't touch any code the tests exercise.

## Recommended first move

Before running this against the real production-adjacent database, test it small first - e.g. `--buyers=200 --sellers=20 --products=300 --orders=500` - check it in Django's admin panel or shell, make sure it looks right, *then* scale up to the real numbers.
