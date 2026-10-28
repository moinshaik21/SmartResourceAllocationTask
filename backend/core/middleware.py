import logging
from . import services

log = logging.getLogger(__name__)


class DailyResetMiddleware:
    """Starts a fresh working day on the first request after the day changes."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method != "OPTIONS":
            try:
                services.ensure_today()
            except Exception:  # noqa: BLE001 - never block a request because of this
                log.exception("Daily reset check failed")
        return self.get_response(request)
