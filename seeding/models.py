from django.db import models


class SeedImagePoolItem(models.Model):
    """
    Tracks one image in the reusable seed-data stock-photo pool.

    Every row here corresponds to a real object already uploaded to S3
    under the `seed-images/` prefix. Seeded products are assigned images
    by querying this table (scoped by category), not by re-downloading
    anything per product. This table is what makes the pool reviewable
    (see admin.py - a one-time visual pass to catch a miscategorized
    photo before it's reused across hundreds of products) and precisely
    cleanable (`--rebuild-image-pool` deletes exactly what's tracked here,
    never anything else in the bucket).
    """

    category_slug = models.SlugField(max_length=120, db_index=True)
    s3_key = models.CharField(max_length=500, unique=True)
    image_url = models.URLField()
    source_url = models.URLField(
        help_text="Original Pexels photo page, kept for reference/attribution."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category_slug", "id"]

    def __str__(self):
        return f"{self.category_slug}: {self.s3_key}"
