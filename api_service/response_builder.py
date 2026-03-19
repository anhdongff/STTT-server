from http import HTTPStatus
from typing import Any, Optional, Dict

from starlette import status as http_status


class ResponseBuilder:
    class ACTION:
        TOAST = "toast"
        DIALOG = "dialog"
        LOGOUT = "logout"
        VERIFY = "verify"

    @staticmethod
    def _status_message(code: int) -> str:
        try:
            return HTTPStatus(code).phrase
        except Exception:
            return "Unknown status"

    @classmethod
    def build(cls, data: Any, status_code: int, message: Optional[str] = None, action: Optional[str] = None) -> Dict[str, Any]:
        msg = message or cls._status_message(status_code)
        return {
            "data": data,
            "meta": {
                "status_code": status_code,
                "message": msg,
                "success": cls._is_success(status_code),
                "action": action or cls._get_action(status_code, message),
            },
        }

    @classmethod
    def success(cls, data: Any = None, message: Optional[str] = None, action: Optional[str] = None) -> Dict[str, Any]:
        return cls.build(data, http_status.HTTP_200_OK, message, action)

    @classmethod
    def error(cls, message: str, status_code: int = http_status.HTTP_500_INTERNAL_SERVER_ERROR, action: Optional[str] = None) -> Dict[str, Any]:
        return cls.build(None, status_code, message, action)

    @classmethod
    def created(cls, data: Any, message: Optional[str] = None, action: Optional[str] = None) -> Dict[str, Any]:
        return cls.build(data, http_status.HTTP_201_CREATED, message, action)

    @classmethod
    def paginated(cls, data: Any, pagination: Dict[str, Any], message: Optional[str] = None, action: Optional[str] = None) -> Dict[str, Any]:
        resp = cls.success(data, message, action)
        resp["meta"]["pagination"] = pagination
        return resp

    @classmethod
    def _get_action(cls, status_code: int, message: Optional[str]) -> Optional[str]:
        # mirror JS logic
        if status_code == 401:
            return cls.ACTION.LOGOUT
        if status_code in (403, 429):
            return cls.ACTION.DIALOG
        if status_code in (400, 404, 422):
            return cls.ACTION.TOAST
        if status_code in (500, 502, 503):
            return cls.ACTION.TOAST
        if status_code in (200, 201):
            default_msg = cls._status_message(status_code)
            return cls.ACTION.TOAST if (message and message != default_msg) else None
        return cls.ACTION.TOAST if status_code >= 400 else None

    @staticmethod
    def _is_success(status_code: int) -> bool:
        return 200 <= status_code < 300
