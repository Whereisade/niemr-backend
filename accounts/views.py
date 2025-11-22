from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.authentication import JWTAuthentication

from .serializers import RegisterSerializer, LoginSerializer, GoogleAuthSerializer
from .models import User
from .services.email import send_email
from .services.google import verify_google_id_token

def _jwt_pair_for(user: User) -> dict:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}

@api_view(["GET"])
@authentication_classes([JWTAuthentication])  
@permission_classes([permissions.IsAuthenticated])
def me(request):
    """
    Return basic info about the currently authenticated user.
    Used by the frontend dashboards for greetings, etc.
    """
    user = request.user
    facility = getattr(user, "facility", None)

    data = {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "role": user.role,
        "facility": None,
    }

    if facility is not None:
        data["facility"] = {
            "id": facility.id,
            "name": getattr(facility, "name", ""),
        }

    return Response(data)

@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def register(request):
    s = RegisterSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    user = s.save()
    send_email(
        subject="Welcome to Niemr",
        to=user.email,
        html="<p>Your account was created successfully.</p>",
        tags=["auth.register"]
    )
    return Response({"user": {"email": user.email, "role": user.role}}, status=status.HTTP_201_CREATED)

@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def login_password(request):
    s = LoginSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    user = s.validated_data["user"]
    return Response({"tokens": _jwt_pair_for(user), "user": {"email": user.email, "role": user.role}})

@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def login_google(request):
    s = GoogleAuthSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    payload = verify_google_id_token(s.validated_data["id_token"])
    email = payload.get("email")
    if not email:
        return Response({"detail": "Email missing in Google token"}, status=400)
    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "username": email.split("@")[0],
            "first_name": (payload.get("given_name") or "")[:150],
            "last_name": (payload.get("family_name") or "")[:150],
            "email_verified": payload.get("email_verified", False),
        },
    )
    if created:
        send_email("Welcome to Niemr (Google)", email, "<p>Signed up via Google.</p>", tags=["auth.register"])
    return Response({"tokens": _jwt_pair_for(user), "user": {"email": user.email, "role": user.role}})
