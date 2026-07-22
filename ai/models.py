from django.conf import settings
from django.db import models
from pgvector.django import VectorField

from products.models import Product


class ProductEmbedding(models.Model):
    """
    Design rules:
    - Separate model, not a field on Product, so re-embedding never touches
      Product's migration history
    - 512 dimensions instead of Voyage's default, this catalog doesn't need
      the extra precision, keeps storage and query cost down
    - model_id recorded on every row, so switching embedding models later is
      detectable instead of silently mixing incompatible vectors
    """
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name="embedding",
    )
    embedding = VectorField(dimensions=512)
    model_id = models.CharField(max_length=100, default="voyage-4-lite-512")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Conversation(models.Model):
    # Shared between the buyer order assistant and the seller assistant -
    # ownership is enforced identically either way via `user=request.user`,
    # the same actor-agnostic pattern run_with_tools uses for tool scoping.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    # The product a tool call most recently operated on (seller assistant
    # only, for now - see run_with_tools' last_product_id tracking). Lets a
    # follow-up like "what's the price now?" skip re-resolving a product
    # already named earlier in the conversation, instead of either paying
    # for another find_product_by_name round trip or relying on the model to
    # notice the reference itself. SET_NULL rather than CASCADE: losing this
    # cache is harmless (the assistant just asks which product next), so a
    # product going away shouldn't be able to affect the conversation row.
    last_product = models.ForeignKey(
        Product,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def get_recent_messages(self, n=5):
        """Return the last n messages, oldest first, for context window assembly."""
        return list(self.messages.order_by("-created_at")[:n][::-1])


class Message(models.Model):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_CHOICES = [(ROLE_USER, "User"), (ROLE_ASSISTANT, "Assistant")]

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
