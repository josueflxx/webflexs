"""
Helpers to build admin presence payloads.
"""
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import UserActivity


def _format_relative_span(seconds_delta):
    seconds = max(int(seconds_delta or 0), 0)
    if seconds < 15:
        return "hace instantes"
    if seconds < 60:
        return f"hace {seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"hace {minutes} min" if minutes != 1 else "hace 1 min"
    hours = minutes // 60
    if hours < 24:
        return f"hace {hours} h" if hours != 1 else "hace 1 h"
    days = hours // 24
    return f"hace {days} d" if days != 1 else "hace 1 d"


def _resolve_presence_status(*, activity, now, idle_window, online_window):
    if not activity or not activity.last_activity:
        return "offline", None

    delta_seconds = max((now - activity.last_activity).total_seconds(), 0)
    if not activity.is_online:
        return "offline", delta_seconds
    if delta_seconds <= idle_window:
        return "online", delta_seconds
    if delta_seconds <= online_window:
        return "idle", delta_seconds
    return "offline", delta_seconds


def get_presence_config():
    """
    Resolve presence tuning values from Django settings.
    """
    online_window_seconds = max(
        int(getattr(settings, "ADMIN_ONLINE_WINDOW_SECONDS", 300)),
        30,
    )
    idle_window_seconds = max(
        int(getattr(settings, "ADMIN_IDLE_WINDOW_SECONDS", 90)),
        15,
    )
    idle_window_seconds = min(idle_window_seconds, online_window_seconds)
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
        "idle_window_seconds": idle_window_seconds,
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
    idle_window = config["idle_window_seconds"]
    rows = []
    for user in users:
        activity = activity_map.get(user.id)
        status, delta_seconds = _resolve_presence_status(
            activity=activity,
            now=now,
            idle_window=idle_window,
            online_window=online_window,
        )
        status_label = {
            "online": "En linea",
            "idle": "Inactivo",
            "offline": "Desconectado",
        }[status]
        if status == "online":
            last_seen_label = "Activo ahora"
        elif delta_seconds is None:
            last_seen_label = "Sin actividad reciente"
        else:
            last_seen_label = f"Ultima actividad {_format_relative_span(delta_seconds)}"

        rows.append(
            {
                "user_id": user.id,
                "username": user.username,
                "initials": (user.username[:2] or "--").upper(),
                "status": status,
                "status_label": status_label,
                "is_online": status == "online",
                "is_idle": status == "idle",
                "last_activity": activity.last_activity.isoformat()
                if activity and activity.last_activity
                else "",
                "last_seen_label": last_seen_label,
            }
        )
    return rows
