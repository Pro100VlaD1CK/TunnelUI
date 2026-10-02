class DomainError(Exception):
    """Carries only a constant safe message, never upstream data or credentials."""

    def __init__(self, code: str, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(code)
