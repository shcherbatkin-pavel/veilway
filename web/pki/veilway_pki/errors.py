"""Fixed public error codes shared by PKI boundaries."""

class PkiError(Exception):
    """Only fixed codes may cross the socket or reach an operator log."""

    CODES = {"invalid_request", "conflict", "not_found", "unavailable", "expired",
             "revoked", "invalid_import", "storage_error", "operation_failed"}

    def __init__(self, code: str):
        self.code = code if code in self.CODES else "operation_failed"
        super().__init__(self.code)
