"""Tests for llm_teams.auth.pkce — PKCE crypto utilities."""
import base64
import hashlib
import re

from llm_teams.auth.pkce import generate_code_challenge, generate_code_verifier, generate_state

_B64URL_RE = re.compile(r"^[A-Za-z0-9\-_]+$")


class TestCodeVerifier:
    def test_is_string(self):
        assert isinstance(generate_code_verifier(), str)

    def test_base64url_alphabet(self):
        assert _B64URL_RE.match(generate_code_verifier())

    def test_no_padding(self):
        assert "=" not in generate_code_verifier()

    def test_length_at_least_43_chars(self):
        # RFC 7636 requires 43-128 characters
        assert len(generate_code_verifier()) >= 43

    def test_uniqueness(self):
        assert generate_code_verifier() != generate_code_verifier()


class TestCodeChallenge:
    def test_s256_derivation(self):
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        assert generate_code_challenge(verifier) == expected

    def test_is_base64url(self):
        v = generate_code_verifier()
        assert _B64URL_RE.match(generate_code_challenge(v))

    def test_no_padding(self):
        v = generate_code_verifier()
        assert "=" not in generate_code_challenge(v)

    def test_deterministic(self):
        v = generate_code_verifier()
        assert generate_code_challenge(v) == generate_code_challenge(v)

    def test_different_verifiers_produce_different_challenges(self):
        assert generate_code_challenge("aaa") != generate_code_challenge("bbb")


class TestState:
    def test_is_string(self):
        assert isinstance(generate_state(), str)

    def test_not_empty(self):
        assert generate_state()

    def test_uniqueness(self):
        assert generate_state() != generate_state()

    def test_url_safe(self):
        # no characters that need URL encoding
        for _ in range(10):
            s = generate_state()
            assert _B64URL_RE.match(s) or all(c not in s for c in "+/= ")
