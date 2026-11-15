"""The ways the application can fail on purpose, each with the HTTP status and the message the user sees.

Raise one of these where a failure happens; do not catch it just to return an empty result. The handlers
in helpers/error_handlers.py turn any that reach the edge into a clean JSON error, and the agent graph
turns the ones it can explain (`LLMUnavailable`, `GoogleAuthRequiredException`) into an error reply.
Internal details stay in the exception's text for the logs; `public_message` is what is shown.
"""


class DomainError(Exception):
    status_code = 500
    code = "internal_error"
    default_message = "Something went wrong. Please try again."

    @property
    def public_message(self) -> str:
        """Safe to show the user."""
        return self.default_message


class GoogleAuthRequiredException(DomainError):
    """Google needs the user to sign in again (no token, a rejected token, or a failed refresh)."""

    status_code = 409  # not 401: the app session is fine, only the Google connection needs renewing
    code = "google_auth_required"
    default_message = "Your Google connection has expired. Please reconnect your Google account."

    @property
    def public_message(self) -> str:
        return str(self) or self.default_message  # raised with user-facing wording


class LLMUnavailable(DomainError):
    """The language model could not be reached or refused the request."""

    status_code = 503
    code = "llm_unavailable"
    default_message = "The AI model is not available right now. Please try again in a moment."

    def __init__(self, detail: str = "", provider: str = "", reason: str = "error"):
        super().__init__(detail or self.default_message)
        self.provider, self.reason = provider, reason

    @property
    def public_message(self) -> str:
        if self.reason == "rate_limited":
            return "The AI service is busy right now. Please try again in a moment."
        if self.reason == "timeout":
            return "The AI model took too long to answer. Please try again."
        if self.reason == "unusable_output":
            return ("I couldn't turn that into something I can do. Please say it a little differently, for example "
                    "\"schedule a meeting with Sara tomorrow at 3pm\".")
        return self.default_message

    @classmethod
    def from_exception(cls, exc: Exception, provider: str = "") -> "LLMUnavailable":
        """Classify a provider SDK error (OpenAI, Groq, Cohere, httpx) without importing any of them."""
        if isinstance(exc, cls):
            return exc
        status = getattr(exc, "status_code", None)
        name = type(exc).__name__.lower()
        if status == 429 or "ratelimit" in name:
            reason = "rate_limited"
        elif status in (401, 403) or "authentication" in name or "permission" in name:
            reason = "auth"
        elif status == 404 or "notfound" in name:
            reason = "model_not_found"
        elif status == 400 and "json_validate_failed" in str(exc):
            reason = "unusable_output"   # the model could not produce what was asked (it refused or went off track)
        elif "timeout" in name:
            reason = "timeout"
        elif "connection" in name or isinstance(exc, (ConnectionError, OSError)):
            reason = "unreachable"
        else:
            reason = "error"
        error = cls(f"{provider or 'LLM'} request failed ({reason}): {exc}", provider=provider, reason=reason)
        error.__cause__ = exc
        return error


class LLMOutputError(DomainError):
    """The model replied, but not with usable JSON. `raw` is its last reply, for callers that can fall back."""

    status_code = 502
    code = "llm_bad_output"
    default_message = "The AI model gave an answer that could not be understood. Please try again."

    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw


class UsageLimitReached(LLMUnavailable):
    """The user's daily AI allowance (a safety limit, not a plan) is used up. Only AI work pauses."""

    status_code = 429
    code = "daily_limit_reached"
    default_message = ("You've reached today's usage limit for the assistant, so AI features are paused until "
                       "midnight UTC. Your reminders, notifications and saved data are not affected.")

    @property
    def public_message(self) -> str:
        return self.default_message


class RateLimitExceeded(DomainError):
    """The user is sending requests faster than the safety limit allows."""

    status_code = 429
    code = "rate_limited"

    def __init__(self, what: str = "requests", retry_after: int = 60):
        super().__init__(f"Too many {what}")
        self.what, self.retry_after = what, max(1, int(retry_after))

    @property
    def public_message(self) -> str:
        wait = f"{self.retry_after} seconds" if self.retry_after < 120 else f"{(self.retry_after + 59) // 60} minutes"
        return f"You're sending {self.what} too quickly. Please wait {wait} and try again."


class ExternalServiceError(DomainError):
    """A service we depend on (Google, a search API) failed."""

    status_code = 502
    code = "external_service_error"
    default_message = "A connected service is not responding. Please try again shortly."

    def __init__(self, detail: str = "", service: str = "", http_status: int = 0):
        super().__init__(detail or self.default_message)
        self.service = service
        self.http_status = http_status  # what the service answered, 0 if it never did (timeout, dropped connection)


class ValidationFailed(DomainError):
    """Input the user (or the model on their behalf) supplied cannot be used."""

    status_code = 400
    code = "invalid_input"
    default_message = "That request could not be processed."

    @property
    def public_message(self) -> str:
        return str(self) or self.default_message  # raised with user-facing wording


class EmailAlreadyRegistered(DomainError):
    status_code = 409
    code = "email_taken"
    default_message = "Email already exists"

    @property
    def public_message(self) -> str:
        return self.default_message
