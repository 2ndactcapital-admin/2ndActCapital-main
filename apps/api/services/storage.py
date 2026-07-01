"""Object storage layer.

Provides a boto3 S3-compatible client pointed at Cloudflare R2 and a helper to
upload deal documents. Downloads / signed URLs are a later sprint.
"""

import os

import boto3

DEFAULT_BUCKET = os.environ.get("R2_BUCKET_NAME", "2ndactcapital-docs")


def get_s3_client():
    """Construct a boto3 S3 client configured for Cloudflare R2."""
    account_id = os.environ.get("R2_ACCOUNT_ID")
    if not account_id:
        raise RuntimeError("R2_ACCOUNT_ID environment variable is not set")

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def upload_bytes(
    key: str,
    data: bytes,
    content_type: str | None = None,
    bucket: str | None = None,
) -> str:
    """Upload bytes to R2 under ``key`` and return the object key.

    Synchronous (boto3) — call via ``run_in_threadpool`` from async handlers.
    """
    client = get_s3_client()
    extra = {"ContentType": content_type} if content_type else {}
    client.put_object(
        Bucket=bucket or DEFAULT_BUCKET,
        Key=key,
        Body=data,
        **extra,
    )
    return key


def get_signed_url(key: str, expires: int = 3600, bucket: str | None = None) -> str:
    """Return a presigned GET URL for ``key``.

    Synchronous (boto3) — call via ``run_in_threadpool`` from async handlers.
    ``expires`` is the lifetime in seconds (default 1 hour).
    """
    client = get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket or DEFAULT_BUCKET, "Key": key},
        ExpiresIn=expires,
    )

