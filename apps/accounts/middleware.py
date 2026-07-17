from django.utils import timezone


class LastActivityMiddleware:
    """Track ``User.last_activity_at`` with at most one UPDATE per minute."""

    STALE_SECONDS = 60

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            now = timezone.now()
            last = user.last_activity_at
            if last is None or (now - last).total_seconds() > self.STALE_SECONDS:
                # request.user is a SimpleLazyObject; resolve the real model
                # class via _meta rather than type().
                user._meta.model.objects.filter(pk=user.pk).update(last_activity_at=now)
                user.last_activity_at = now
        return self.get_response(request)
