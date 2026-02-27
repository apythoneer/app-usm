"""
Custom application exceptions
"""

from fastapi import HTTPException, status


class USMException(Exception):
    """Base application exception"""
    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class DatabaseError(USMException):
    def __init__(self, message: str = "Database operation failed"):
        super().__init__(message, status_code=500)


class CollectorError(USMException):
    def __init__(self, message: str, vendor: str = "", array: str = ""):
        self.vendor = vendor
        self.array = array
        super().__init__(f"[{vendor}/{array}] {message}", status_code=500)


class CredentialError(USMException):
    def __init__(self, message: str = "Failed to retrieve credentials"):
        super().__init__(message, status_code=503)


class ArrayNotFoundError(USMException):
    def __init__(self, array_name: str):
        super().__init__(f"Array not found: {array_name}", status_code=404)


class NotFoundError(USMException):
    def __init__(self, resource: str):
        super().__init__(f"{resource} not found", status_code=404)


# HTTP shortcuts
def http_not_found(detail: str = "Not found") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def http_bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def http_server_error(detail: str = "Internal server error") -> HTTPException:
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)
