from typing import Any


class PayPalError(Exception):
    """A non-success response (or unrecoverable transport failure) from PayPal.

    `debug_id` is what PayPal support asks for; always log it.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        name: str | None = None,
        debug_id: str | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.name = name
        self.debug_id = debug_id
        self.details = details or []

    @property
    def issues(self) -> list[str]:
        """Machine-readable issue codes, e.g. ["AUTHORIZATION_EXPIRED"]."""
        return [d["issue"] for d in self.details if "issue" in d]

    def has_issue(self, issue: str) -> bool:
        return issue in self.issues or self.name == issue

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code:
            parts.append(f"status={self.status_code}")
        if self.name:
            parts.append(f"name={self.name}")
        if self.issues:
            parts.append(f"issues={','.join(self.issues)}")
        if self.debug_id:
            parts.append(f"debug_id={self.debug_id}")
        return " ".join(parts)


class PayPalAuthError(PayPalError):
    """OAuth token request failed (bad client id/secret, wrong environment)."""


class PayPalNotConfiguredError(PayPalError):
    """PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are missing."""
