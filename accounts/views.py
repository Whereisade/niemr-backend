from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from accounts.enums import UserRole
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated

from .serializers import (
    RegisterSerializer,
    LoginSerializer,
    GoogleAuthSerializer,
    UserProfileUpdateSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    PasswordChangeSerializer,
)
from .models import User
from .services.email import send_email
from .services.google import verify_google_id_token

def _jwt_pair_for(user: User) -> dict:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}

@api_view(["GET", "PATCH"])
@authentication_classes([JWTAuthentication])  
@permission_classes([permissions.IsAuthenticated])
def me(request):
    """
    Return or update basic info about the currently authenticated user.
    
    GET: Return user profile
    PATCH: Update user profile (first_name, last_name)
    """
    user = request.user
    
    if request.method == "PATCH":
        serializer = UserProfileUpdateSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        # After update, continue to return the updated user data below
    
    # Return user data (for both GET and PATCH)
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
@authentication_classes([JWTAuthentication])
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
@authentication_classes([JWTAuthentication])
@permission_classes([permissions.AllowAny])
def login_password(request):
    s = LoginSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    user = s.validated_data["user"]
    return Response({"tokens": _jwt_pair_for(user), "user": {"email": user.email, "role": user.role}})

@api_view(["POST"])
@authentication_classes([JWTAuthentication])
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


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([permissions.AllowAny])
def password_reset_request(request):
    """Request a password reset link.

    Security: always returns 200 (even if the email doesn't exist).
    """
    s = PasswordResetRequestSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    email = (s.validated_data.get("email") or "").strip()

    user = User.objects.filter(email__iexact=email).first()
    if user and getattr(user, "email", None):
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        base = getattr(settings, "FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")
        reset_url = f"{base}/reset-password?uid={uid}&token={token}"

        send_email(
            subject="Reset your NIEMR password",
            to=user.email,
            html=(
                "<p>We received a request to reset your password.</p>"
                f"<p><a href='{reset_url}'>Click here to reset your password</a></p>"
                "<p>If you didn't request this, you can ignore this email.</p>"
            ),
            tags=["auth.password_reset"],
        )

    return Response(
        {"detail": "If an account exists for that email, a reset link has been sent."},
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([permissions.AllowAny])
def password_reset_confirm(request):
    """Confirm password reset using uid + token and set a new password."""
    s = PasswordResetConfirmSerializer(data=request.data)
    s.is_valid(raise_exception=True)

    uid = s.validated_data.get("uid")
    token = s.validated_data.get("token")
    new_password = s.validated_data.get("new_password")

    try:
        user_id = force_str(urlsafe_base64_decode(uid))
        user = User.objects.get(pk=user_id)
    except Exception:
        return Response({"detail": "Invalid reset link."}, status=status.HTTP_400_BAD_REQUEST)

    if not default_token_generator.check_token(user, token):
        return Response({"detail": "Reset link expired or invalid."}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(new_password)
    user.save(update_fields=["password"])

    # Best-effort confirmation email.
    try:
        send_email(
            subject="Your NIEMR password was changed",
            to=user.email,
            html="<p>Your password was changed successfully. If you did not do this, contact support immediately.</p>",
            tags=["auth.password_changed"],
        )
    except Exception:
        pass

    return Response({"detail": "Password has been reset."}, status=status.HTTP_200_OK)


@api_view(["POST"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def password_change(request):
    """Change password for the currently authenticated user.

    Intended for Settings > Profile.
    Body:
      {"current_password": "...", "new_password": "...", "confirm_password": "..."}
    """

    s = PasswordChangeSerializer(data=request.data, context={"request": request})
    s.is_valid(raise_exception=True)

    user = request.user
    new_password = s.validated_data["new_password"]

    user.set_password(new_password)
    user.save(update_fields=["password"])

    # Best-effort notification email.
    try:
        send_email(
            subject="Your NIEMR password was changed",
            to=user.email,
            html=(
                "<p>Your password was changed successfully.</p>"
                "<p>If you did not do this, contact support immediately.</p>"
            ),
            tags=["auth.password_changed"],
        )
    except Exception:
        pass

    return Response({"detail": "Password updated successfully."}, status=status.HTTP_200_OK)

@api_view(["GET", "PATCH"])
@authentication_classes([JWTAuthentication])
@permission_classes([IsAuthenticated])
def visibility_settings(request):
    """
    Get or update visibility settings for the current user.
    
    GET: Returns current visibility status
    PATCH: Updates visibility status
    
    Body (for PATCH):
    {
        "is_publicly_visible": true/false
    }
    
    Only available for facility users and independent providers.
    Patients cannot access this endpoint.
    """
    user = request.user
    
    # Block patients from accessing this endpoint
    if user.role == UserRole.PATIENT:
        return Response(
            {"detail": "Patients cannot modify visibility settings."},
            status=status.HTTP_403_FORBIDDEN
        )
    
    # Determine if this is a facility user or independent provider
    is_facility_user = getattr(user, "facility_id", None) is not None
    is_independent_provider = user.role in {
        UserRole.DOCTOR, UserRole.NURSE, UserRole.LAB, UserRole.PHARMACY
    } and not is_facility_user
    
    if request.method == "GET":
        # Return current visibility status
        current_visibility = None
        entity_type = None
        
        if is_facility_user:
            # Facility user - check facility visibility
            facility = user.facility
            current_visibility = getattr(facility, "is_publicly_visible", True)
            entity_type = "facility"
            entity_name = facility.name
        elif is_independent_provider:
            # Independent provider - check provider profile visibility
            try:
                provider_profile = user.provider_profile
                current_visibility = getattr(provider_profile, "is_publicly_visible", True)
                entity_type = "provider"
                entity_name = provider_profile.get_display_name()
            except Exception:
                return Response(
                    {"detail": "Provider profile not found."},
                    status=status.HTTP_404_NOT_FOUND
                )
        else:
            return Response(
                {"detail": "User type does not support visibility settings."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        return Response({
            "is_publicly_visible": current_visibility,
            "entity_type": entity_type,
            "entity_name": entity_name,
            "role": user.role,
        })
    
    elif request.method == "PATCH":
        # Update visibility status
        is_publicly_visible = request.data.get("is_publicly_visible")
        
        if is_publicly_visible is None:
            return Response(
                {"detail": "is_publicly_visible field is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Convert to boolean
        is_visible = bool(is_publicly_visible)
        
        if is_facility_user:
            # Update facility visibility (only SUPER_ADMIN and ADMIN can do this)
            if user.role not in {UserRole.SUPER_ADMIN, UserRole.ADMIN}:
                return Response(
                    {"detail": "Only facility admins can modify facility visibility."},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            facility = user.facility
            facility.is_publicly_visible = is_visible
            facility.save(update_fields=["is_publicly_visible", "updated_at"])
            
            return Response({
                "is_publicly_visible": facility.is_publicly_visible,
                "entity_type": "facility",
                "entity_name": facility.name,
                "message": f"Facility visibility updated to {'visible' if is_visible else 'hidden'}."
            })
        
        elif is_independent_provider:
            # Update provider profile visibility
            try:
                provider_profile = user.provider_profile
                provider_profile.is_publicly_visible = is_visible
                provider_profile.save(update_fields=["is_publicly_visible", "updated_at"])
                
                return Response({
                    "is_publicly_visible": provider_profile.is_publicly_visible,
                    "entity_type": "provider",
                    "entity_name": provider_profile.get_display_name(),
                    "message": f"Provider visibility updated to {'visible' if is_visible else 'hidden'}."
                })
            except Exception as e:
                return Response(
                    {"detail": f"Failed to update provider profile: {str(e)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        return Response(
            {"detail": "User type does not support visibility settings."},
            status=status.HTTP_400_BAD_REQUEST
        )