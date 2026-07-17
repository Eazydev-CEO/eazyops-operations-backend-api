"""JWT auth endpoints with lockout integration and audit records."""
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView

from apps.accounts.services import is_locked_out
from apps.audits.services import Actions, record_audit
from apps.core.utils import client_meta

from .throttling import AuthRateThrottle, AuthUserRateThrottle


class LockoutAwareTokenObtainSerializer(TokenObtainPairSerializer):
    """Applies the same failed-login lockout policy as the web login."""

    def validate(self, attrs):
        request = self.context.get("request")
        email = (attrs.get(self.username_field) or "").lower()
        ip, _ = client_meta(request)
        if is_locked_out(email=email, ip_address=ip):
            raise AuthenticationFailed(
                "Too many failed attempts. Try again in a few minutes.", code="locked_out"
            )
        data = super().validate(attrs)
        record_audit(
            actor=self.user, action=Actions.API_TOKEN_ISSUED, instance=self.user,
            metadata={"grant": "password"}, request=request,
        )
        return data


class ObtainTokenView(TokenObtainPairView):
    serializer_class = LockoutAwareTokenObtainSerializer
    throttle_classes = [AuthRateThrottle]


class RefreshTokenView(TokenRefreshView):
    throttle_classes = [AuthUserRateThrottle]


class VerifyTokenView(TokenVerifyView):
    throttle_classes = [AuthUserRateThrottle]


class TokenPairResponseSerializer(serializers.Serializer):
    """Schema helper for API docs."""

    access = serializers.CharField()
    refresh = serializers.CharField()
