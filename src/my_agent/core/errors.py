class ModelError(Exception):
    """Base error reported by a provider."""


class RetryableModelError(ModelError):
    """The provider reported a transient failure."""


class PromptTooLongError(ModelError):
    """The model rejected the request's context size."""


class ModelOutputTruncatedError(ModelError):
    """The response reached its output token limit."""


class NonRetryableModelError(ModelError):
    """The provider reported a permanent failure."""


class Cancelled(Exception):
    """The run's cooperative cancellation token was set."""
