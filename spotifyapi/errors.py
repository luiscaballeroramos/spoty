class SpotifyError(Exception):
    """Represent a Spotify failure with a safe public message, action, and optional HTTP status."""
    message = "Spotify request failed."

    def __init__(self, action: str, http_status: int | None = None):
        """Initialize the public message and retain action and HTTP status for diagnostics."""
        super().__init__(self.message)
        self.action = action
        self.http_status = http_status


class SpotifyAuthorizationError(SpotifyError):
    """Indicate missing Spotify authorization or an HTTP 401 or 403 response."""
    message = "Spotify authorization is required."


class SpotifyUnavailableError(SpotifyError):
    """Indicate a connection failure or temporary Spotify server unavailability."""
    message = "Spotify is temporarily unavailable."


class SpotifyTimeoutError(SpotifyUnavailableError):
    """Indicate a request timeout whose remote operation outcome may be unknown."""
    message = "Spotify did not respond in time; the operation outcome may be unknown."


class SpotifyInvalidResponseError(SpotifyError):
    """Indicate invalid JSON or a response that does not satisfy the expected data contract."""
    message = "Spotify returned an invalid response."


class SpotifyRequestError(SpotifyError):
    """Indicate an HTTP rejection not classified as authorization, rate limiting, or server failure."""
    message = "Spotify rejected the request."


class SpotifyRateLimitError(SpotifyError):
    """Indicate an HTTP 429 or active cooldown with a remaining retry delay in seconds."""
    message = "Spotify requests are temporarily rate limited."

    def __init__(self, action: str, retry_after: float):
        """Initialize the rate-limit failure with HTTP 429 and its retry delay."""
        super().__init__(action, http_status=429)
        self.retry_after = retry_after
