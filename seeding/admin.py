from django.contrib import admin
from django.utils.html import format_html

from .models import SeedImagePoolItem


@admin.register(SeedImagePoolItem)
class SeedImagePoolItemAdmin(admin.ModelAdmin):
    """
    Purpose-built for the one-time visual QA pass described in
    SEEDING_PLAN.md: browse by category, eyeball thumbnails, catch a
    miscategorized photo (e.g. a couch under Electronics) before it's
    reused across hundreds of seeded products.
    """

    list_display = ("thumbnail", "category_slug", "s3_key", "created_at")
    list_filter = ("category_slug",)
    search_fields = ("s3_key", "category_slug", "source_url")
    readonly_fields = ("preview",)
    fields = ("category_slug", "s3_key", "image_url", "source_url", "preview", "created_at")

    @admin.display(description="Preview")
    def thumbnail(self, obj):
        return format_html(
            '<img src="{}" style="height:70px;width:auto;border-radius:4px;" />',
            obj.image_url,
        )

    @admin.display(description="Preview")
    def preview(self, obj):
        return format_html(
            '<img src="{}" style="max-height:400px;width:auto;" />',
            obj.image_url,
        )
