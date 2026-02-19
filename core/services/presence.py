"""
Helpers to build admin presence payloads.
"""
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import UserActivity


def get_presence_config():
    """
    Resolve presence tuning values from Django settings.
    """
    online_window_seconds = max(
        int(getattr(settings, "ADMIN_ONLINE_WINDOW_SECONDS", 300)),
        30,
    )
    refresh_seconds = max(
        int(getattr(settings, "ADMIN_PRESENCE_REFRESH_SECONDS", 30)),
        10,
    )
    touch_interval_seconds = max(
        int(getattr(settings, "ADMIN_PRESENCE_TOUCH_INTERVAL_SECONDS", 30)),
        5,
    )
    excluded_users = tuple(
        getattr(settings, "ADMIN_PRESENCE_EXCLUDED_USERS", ("admin", "admin_tester"))
    )
    return {
        "online_window_seconds": online_window_seconds,
        "refresh_seconds": refresh_seconds,
        "touch_interval_seconds": touch_interval_seconds,
        "excluded_users": excluded_users,
    }


def build_admin_presence_payload():
    """
    Return normalized admin presence rows for templates/API.
    """
    config = get_presence_config()
    users = list(
        User.objects.filter(is_staff=True, is_active=True)
        .exclude(username__in=config["excluded_users"])
        .only("id", "username")
        .order_by("username")
    )
    if not users:
        return []

    user_ids = [user.id for user in users]
    activity_map = {
        activity.user_id: activity
        for activity in UserActivity.objects.filter(user_id__in=user_ids).only(
            "user_id", "last_activity", "is_online"
        )
    }

    now = timezone.now()
    online_window = config["online_window_seconds"]
    rows = []
    for user in users:
        activity = activity_map.get(user.id)
        is_online = False
        if activity and activity.is_online and activity.last_activity:
            delta = now - activity.last_activity
            is_online = delta.total_seconds() < online_window

        rows.append(
            {
                "user_id": user.id,
                "username": user.username,
                "initials": (user.username[:2] or "--").upper(),
                "is_online": is_online,
                "last_activity": activity.last_activity.isoformat()
                if activity and activity.last_activity
                else "",
            }
        )
    return rows
