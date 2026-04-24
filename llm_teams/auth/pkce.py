"""PKCE (Proof Key for Code Exchange) utilities for OAuth2 CLI flows."""
import hashlib
import base64
import secrets


def generate_code_verifier() -> str:
    """Generate a cryptographically random 32-byte code verifier (base64url, no padding)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def generate_code_challenge(verifier: str) -> str:
    """Derive S256 code challenge from verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    """Random opaque value for CSRF protection."""
    return secrets.token_urlsafe(16)
