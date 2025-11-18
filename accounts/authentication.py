# Backend/accounts/authentication.py
from rest_framework_simplejwt.authentication import JWTAuthentication


class HeaderJWTAuthentication(JWTAuthentication):
    """
    Custom JWT auth that explicitly reads the Authorization header
    via request.headers instead of request.META.
    """

    def get_header(self, request):
        # DRF Request implements .headers which is case-insensitive
        auth = request.headers.get("Authorization")

        if isinstance(auth, str):
            auth = auth.encode("iso-8859-1")

        return auth
