class GiggyAPIError(Exception):
    """An HTTP error returned by the Giggy API."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Giggy API returned HTTP {status_code}: {body}")
