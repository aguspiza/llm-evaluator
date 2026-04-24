"""Built-in OAuth2 / OIDC provider configurations."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Provider:
    id: str
    name: str
    auth_url: str
    token_url: str
    userinfo_url: str
    default_scopes: list[str]
    pkce: bool = True           # whether to send code_challenge
    client_id: Optional[str] = None
    # client_secret deliberately omitted — CLI flows are public clients


_PROVIDERS: dict[str, Provider] = {
    "github": Provider(
        id="github",
        name="GitHub",
        auth_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user",
        default_scopes=["read:user", "user:email"],
        pkce=False,  # GitHub OAuth does not support PKCE
    ),
    "google": Provider(
        id="google",
        name="Google",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        default_scopes=["openid", "email", "profile"],
        pkce=True,
    ),
    "microsoft": Provider(
        id="microsoft",
        name="Microsoft / Azure AD",
        auth_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        userinfo_url="https://graph.microsoft.com/oidc/userinfo",
        default_scopes=["openid", "email", "profile"],
        pkce=True,
    ),
}


def get(provider_id: str) -> Provider:
    if provider_id not in _PROVIDERS:
        raise ValueError(
            f"Unknown provider '{provider_id}'. "
            f"Available: {', '.join(_PROVIDERS)} or 'custom' (via config)."
        )
    return _PROVIDERS[provider_id]


def names() -> list[str]:
    return list(_PROVIDERS.keys()) + ["custom"]
