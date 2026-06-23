"""Object storage layer.

Provides a boto3 S3-compatible client pointed at Cloudflare R2. No uploads or
downloads are implemented yet — this is a stub that centralizes client
construction for later work.
"""

import os

import boto3


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
