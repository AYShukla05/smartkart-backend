import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

logger = logging.getLogger(__name__)


class S3UploadError(Exception):
    """Raised when a presigned upload URL could not be generated."""


def get_s3_client():
    return boto3.client(
        "s3",
        region_name=settings.AWS_S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


def generate_presigned_put_url(key, content_type="image/webp", expires_in=300):
    """Generate a presigned PUT URL for uploading to S3."""
    try:
        client = get_s3_client()
        return client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.AWS_S3_BUCKET_NAME,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )
    except (ClientError, BotoCoreError):
        logger.warning(
            "Failed to generate presigned upload URL for key: %s", key, exc_info=True
        )
        raise S3UploadError("Image upload service unavailable.")


def get_public_url(key):
    """Construct the public URL for an S3 object."""
    return f"https://{settings.AWS_S3_BUCKET_NAME}.s3.{settings.AWS_S3_REGION}.amazonaws.com/{key}"


def get_s3_key_from_url(url):
    """Extract the S3 object key from one of our own public URLs, or None if it doesn't match."""
    prefix = f"https://{settings.AWS_S3_BUCKET_NAME}.s3.{settings.AWS_S3_REGION}.amazonaws.com/"
    if url.startswith(prefix):
        return url[len(prefix):]
    return None


def delete_s3_object(url):
    """Delete an S3 object given its full public URL."""
    key = get_s3_key_from_url(url)
    if key:
        client = get_s3_client()
        client.delete_object(Bucket=settings.AWS_S3_BUCKET_NAME, Key=key)
