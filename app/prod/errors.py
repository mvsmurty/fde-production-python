class APIError(Exception):
    """An error callers should see, always in the same shape: error code, message, trace id."""

    def __init__(
        self, status_code: int, code: str, message: str, headers: dict[str, str] | None = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}
