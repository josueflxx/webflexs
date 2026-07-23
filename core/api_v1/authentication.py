"""Authentication aliases used by first-party integrations."""

from rest_framework.authentication import TokenAuthentication


class BearerTokenAuthentication(TokenAuthentication):
    """Accept DRF tokens with the Bearer prefix used by the React editor."""

    keyword = "Bearer"
