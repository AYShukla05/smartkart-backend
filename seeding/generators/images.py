"""
Builds and manages the reusable seed-data image pool: a small, category-searched
set of real stock photos, uploaded once to S3 and reused across many products.

Isolated on purpose from user/product/order seeding - a bad afternoon for the
Pexels API or S3 should never block or roll back the far more important data
seeding. See SEEDING_PLAN.md for the full reasoning.
"""
import logging
import time

import requests
from django.conf import settings

from products.s3_utils import get_public_url, get_s3_client
from seeding.constants import (
    CATEGORIES,
    PEXELS_DOWNLOAD_DELAY_SECONDS,
    PEXELS_IMAGES_PER_CATEGORY,
    PEXELS_MAX_RETRIES,
    PEXELS_RETRY_BACKOFF_SECONDS,
    PEXELS_SEARCH_DELAY_SECONDS,
    PEXELS_SEARCH_URL,
    S3_SEED_IMAGE_PREFIX,
)
from seeding.models import SeedImagePoolItem

logger = logging.getLogger(__name__)


def _log(stdout, msg):
    if stdout:
        stdout.write(msg)
    else:
        logger.info(msg)


def _retry(fn, *, what):
    """Run fn() with a few retries and exponential backoff (1s, 2s, 4s...).
    Returns fn()'s result, or raises the last exception once retries are exhausted."""
    last_exc = None
    for attempt in range(1, PEXELS_MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - broad on purpose, this is best-effort seed tooling
            last_exc = exc
            if attempt < PEXELS_MAX_RETRIES:
                delay = PEXELS_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "%s failed (attempt %d/%d): %s - retrying in %.1fs",
                    what, attempt, PEXELS_MAX_RETRIES, exc, delay,
                )
                time.sleep(delay)
    raise last_exc


def _search_pexels(query, per_page):
    def do_search():
        response = requests.get(
            PEXELS_SEARCH_URL,
            headers={"Authorization": settings.PEXELS_API_KEY},
            params={"query": query, "per_page": per_page},
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get("photos", [])

    return _retry(do_search, what=f"Pexels search for '{query}'")


def _download_image(url):
    def do_download():
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.content

    return _retry(do_download, what=f"image download from {url}")


def _upload_to_s3(client, key, content):
    def do_upload():
        client.put_object(
            Bucket=settings.AWS_S3_BUCKET_NAME,
            Key=key,
            Body=content,
            ContentType="image/jpeg",
        )

    _retry(do_upload, what=f"S3 upload of {key}")


def build_image_pool(*, target_per_category=PEXELS_IMAGES_PER_CATEGORY, stdout=None):
    """
    Tops up every category's pool to `target_per_category` images.
    Idempotent: categories that already have enough pool items are skipped
    entirely (no re-download, no re-upload).
    """
    if not settings.PEXELS_API_KEY:
        _log(stdout, "PEXELS_API_KEY is not set - skipping image pool build. "
                      "Products will be seeded without images this run.")
        return

    client = get_s3_client()

    for slug, meta in CATEGORIES.items():
        existing = SeedImagePoolItem.objects.filter(category_slug=slug).count()
        if existing >= target_per_category:
            _log(stdout, f"  {meta['name']}: already has {existing} pool images, skipping.")
            continue

        needed = target_per_category - existing
        _log(stdout, f"  {meta['name']}: fetching {needed} images (query: \"{meta['pexels_query']}\")...")

        try:
            photos = _search_pexels(meta["pexels_query"], per_page=needed)
        except Exception as exc:
            _log(stdout, f"  {meta['name']}: search failed after retries ({exc}) - 0 images added this run.")
            continue

        if not photos:
            _log(stdout, f"  {meta['name']}: search returned no results - 0 images added this run.")
            continue

        added = 0
        for photo in photos:
            # Keyed by Pexels' own photo id, not a sequential counter: a
            # partial failure earlier (e.g. image #19 out of 35) leaves a
            # gap, and "existing count + 1" as the next index collides with
            # a key that was already taken by a *later* image that
            # succeeded on a prior run. The photo id is globally unique per
            # photo, so this can't collide, full stop - no gap-tracking needed.
            photo_id = photo["id"]
            try:
                source_image_url = photo["src"]["large"]
                content = _download_image(source_image_url)
                key = f"{S3_SEED_IMAGE_PREFIX}/{slug}/{photo_id}.jpg"
                _upload_to_s3(client, key, content)
                _, created = SeedImagePoolItem.objects.get_or_create(
                    s3_key=key,
                    defaults={
                        "category_slug": slug,
                        "image_url": get_public_url(key),
                        "source_url": photo.get("url", source_image_url),
                    },
                )
                if created:
                    added += 1
            except Exception as exc:
                _log(stdout, f"    image {photo_id} failed after retries, skipping: {exc}")
            time.sleep(PEXELS_DOWNLOAD_DELAY_SECONDS)

        _log(stdout, f"  {meta['name']}: added {added}/{needed} images.")
        time.sleep(PEXELS_SEARCH_DELAY_SECONDS)


def rebuild_image_pool(*, stdout=None):
    """Deletes every S3 object + tracking row this pool owns, then rebuilds from scratch."""
    client = get_s3_client()
    items = list(SeedImagePoolItem.objects.all())
    _log(stdout, f"Deleting {len(items)} tracked pool images from S3...")
    for item in items:
        # delete_object is idempotent - succeeds even if the key is already gone,
        # so a stale tracking row pointing at an already-deleted object is a non-issue.
        client.delete_object(Bucket=settings.AWS_S3_BUCKET_NAME, Key=item.s3_key)
    SeedImagePoolItem.objects.all().delete()
    _log(stdout, "Pool cleared. Rebuilding...")
    build_image_pool(stdout=stdout)


def get_image_pool_by_category():
    """
    Loads the whole pool in one query, grouped by category. Used by the
    product generator so assigning images to thousands of products doesn't
    mean thousands of extra queries.
    """
    pool = {slug: [] for slug in CATEGORIES}
    for item in SeedImagePoolItem.objects.all().values("category_slug", "image_url"):
        pool.setdefault(item["category_slug"], []).append(item["image_url"])
    return pool
