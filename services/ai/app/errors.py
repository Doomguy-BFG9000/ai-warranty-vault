"""Error types and the HTTP shapes they map to.

Two principles:

  * A resource that is not yours and a resource that does not exist get the same
    404. Distinguishing them tells a caller whether some id exists in another
    account, which is a real leak even though no data comes with it.
  * Provider failures are reported as provider failures. This service never
    degrades to fabricated embeddings or an ungrounded answer when a model call
    fails, because a wrong answer that looks fine is worse than an error.
"""

from __future__ import annotations

from fastapi import HTTPException, status


class NotFoundOrForbidden(HTTPException):
    """Used for every "does not exist / is not yours" case, deliberately fused."""

    def __init__(self, what: str = "resource") -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No such {what}.",
        )


class Conflict(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class BadRequest(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


class UnprocessableDocument(HTTPException):
    """The file was accepted but could not be turned into text.

    Distinct from a 500: there is nothing to retry until the user supplies a
    better scan, and the app says so instead of showing a spinner forever.
    """

    # 422 spelled numerically: Starlette renamed its constant for this code, and a
    # literal keeps this working across both spellings.
    STATUS = 422

    def __init__(self, detail: str, code: str = "unreadable_document") -> None:
        super().__init__(
            status_code=self.STATUS,
            detail={"code": code, "message": detail},
        )


class ProviderError(RuntimeError):
    """A model or embedding provider failed, or is not configured.

    Never caught and turned into a mock result. The indexing worker retries it a
    bounded number of times and then marks the document failed; a request-time
    call surfaces as 503 so the client can offer "try again" rather than showing
    a confident answer built on nothing.
    """

    def __init__(self, message: str, *, code: str = "provider_error", retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ProviderNotConfigured(ProviderError):
    def __init__(self, what: str) -> None:
        super().__init__(
            f"{what} is not configured for this service.",
            code="provider_not_configured",
            retryable=False,
        )


def provider_unavailable(exc: ProviderError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": exc.code, "message": "The AI provider is unavailable right now."},
    )
