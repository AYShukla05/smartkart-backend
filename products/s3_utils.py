import boto3
from django.conf import settings


def get_s3_client():
    return boto3.client(
        "s3",
        region_name=settings.AWS_S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


def generate_presigned_put_url(key, content_type="image/webp", expires_in=300):
    """Generate a presigned PUT URL for uploading to S3."""
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


def get_public_url(key):
    """Construct the public URL for an S3 object."""
    return f"https://{settings.AWS_S3_BUCKET_NAME}.s3.{settings.AWS_S3_REGION}.amazonaws.com/{key}"


def delete_s3_object(url):
    """Delete an S3 object given its full public URL."""
    prefix = f"https://{settings.AWS_S3_BUCKET_NAME}.s3.{settings.AWS_S3_REGION}.amazonaws.com/"
    if url.startswith(prefix):
        key = url[len(prefix):]
        client = get_s3_client()
        client.delete_object(Bucket=settings.AWS_S3_BUCKET_NAME, Key=key)
