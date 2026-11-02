"""Errors that the API maps to HTTP responses."""


class AeoError(Exception):
    """Base error for expected simulator failures."""


class ProviderConfigError(AeoError):
    """A requested provider is missing its credentials or settings."""


class UnsafeURL(AeoError):
    """The crawler refused a URL."""


class NotFoundError(AeoError):
    """The requested row does not exist."""


class ConflictError(AeoError):
    """The request does not match the current state of a resource."""


class FanoutParseError(AeoError):
    """The model did not return a usable sub-query list."""
