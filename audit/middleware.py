from .local import set_request, clear_request

class AuditRequestMiddleware:
    """
    Stores the current request in thread-local storage so signals/services can
    read user/ip/ua.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_request(request)
        try:
            return self.get_response(request)
        finally:
            clear_request()
