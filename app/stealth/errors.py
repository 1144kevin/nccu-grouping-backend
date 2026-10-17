class StealthError(Exception):
    """Application error containing a public code, never private record contents."""

    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(code)
