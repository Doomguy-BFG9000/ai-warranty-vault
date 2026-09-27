"""Reading and deleting objects in Supabase Storage.

Two callers, two credentials, and the difference is the point.

`verify_readable` runs with the **caller's own access token**, at registration
time. Storage RLS then decides whether that user may read that object — which is
what makes "is this file yours?" a question answered by Postgres rather than by a
string comparison in Python. It fetches a single byte, not the file.

`download_as_service` runs with the **service-role key**, inside the indexing
worker, minutes later, when the caller's token no longer exists. The path it is
given is never taken from a request: it is read back out of the
`product_documents` row, whose `storage_path` carries a CHECK constraint pinning
it to that row's own user and product. So the privileged download can only ever
reach an object that RLS already approved for the user who owns the row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.errors import ProviderError

logger = logging.getLogger(__name__)

DOCUMENTS_BUCKET = "product-documents"

_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0)


@dataclass(frozen=True)
class StoredObject:
    data: bytes
    content_type: str | None
    byte_size: int


def _object_url(settings: Settings, bucket: str, path: str) -> str:
    return f"{settings.supabase_url.rstrip('/')}/storage/v1/object/{bucket}/{path.lstrip('/')}"


async def verify_readable(settings: Settings, path: str, access_token: str, *, bucket: str = DOCUMENTS_BUCKET) -> bool:
    """True when the caller's own token can read this object.

    Uses a one-byte ranged read. A server that ignores `Range` would send the
    whole body, so the response is streamed and closed after the first chunk
    rather than buffered.
    """
    url = _object_url(settings, bucket, path)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "apikey": access_token,
        "Range": "bytes=0-0",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code in (200, 206):
                    async for _ in response.aiter_bytes():
                        break
                    return True
                if response.status_code in (400, 401, 403, 404):
                    # Not found and not permitted are intentionally not
                    # distinguished; both mean "not something you can index".
                    return False
                logger.warning("unexpected storage status %s while verifying an object", response.status_code)
                return False
    except httpx.HTTPError as exc:
        raise ProviderError(f"storage unreachable: {type(exc).__name__}") from exc


async def download_as_service(
    settings: Settings, path: str, *, bucket: str = DOCUMENTS_BUCKET, max_bytes: int | None = None
) -> StoredObject:
    if not settings.supabase_service_role_key:
        raise ProviderError(
            "SUPABASE_SERVICE_ROLE_KEY is not configured; the indexing worker cannot read uploads.",
            code="storage_not_configured",
            retryable=False,
        )

    url = _object_url(settings, bucket, path)
    headers = {
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "apikey": settings.supabase_service_role_key,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 404:
                    raise ProviderError("the stored file is gone", code="object_missing", retryable=False)
                if response.status_code >= 400:
                    raise ProviderError(
                        f"storage returned {response.status_code}",
                        code="storage_error",
                        retryable=response.status_code >= 500,
                    )

                buffer = bytearray()
                async for chunk in response.aiter_bytes():
                    buffer.extend(chunk)
                    # Belt-and-braces against a bucket limit that was relaxed
                    # without this service being told: stop reading rather than
                    # filling memory.
                    if max_bytes is not None and len(buffer) > max_bytes:
                        raise ProviderError(
                            "the stored file is larger than this service accepts",
                            code="object_too_large",
                            retryable=False,
                        )

                return StoredObject(
                    data=bytes(buffer),
                    content_type=response.headers.get("content-type"),
                    byte_size=len(buffer),
                )
    except httpx.HTTPError as exc:
        raise ProviderError(f"storage unreachable: {type(exc).__name__}") from exc


async def delete_as_user(settings: Settings, path: str, access_token: str, *, bucket: str = DOCUMENTS_BUCKET) -> None:
    """Remove an object using the caller's token, so RLS authorises the delete.

    Failures are logged, not raised: the database row is already gone by the time
    this runs, and a user should not see "delete failed" because an orphaned
    object survived.
    """
    url = _object_url(settings, bucket, path)
    headers = {"Authorization": f"Bearer {access_token}", "apikey": access_token}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.delete(url, headers=headers)
            if response.status_code >= 400 and response.status_code != 404:
                logger.warning("storage delete returned %s for an object", response.status_code)
    except httpx.HTTPError as exc:
        logger.warning("storage delete failed: %s", type(exc).__name__)
