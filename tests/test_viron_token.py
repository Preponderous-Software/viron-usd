import jwt
import pytest

from viron_token import DEFAULT_ISSUER, DEFAULT_SECRET, mint_token


def test_minted_token_verifies_with_secret_and_issuer():
    token = mint_token(now=1_000_000, ttl_seconds=60)
    claims = jwt.decode(
        token, DEFAULT_SECRET.encode(), algorithms=["HS256"], issuer=DEFAULT_ISSUER,
        options={"verify_exp": False},
    )
    assert claims["iss"] == DEFAULT_ISSUER and claims["exp"] - claims["iat"] == 60
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_wrong_secret_fails_verification():
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(mint_token(), b"x" * 40, algorithms=["HS256"], issuer=DEFAULT_ISSUER)


def test_short_secret_rejected():
    with pytest.raises(ValueError):
        mint_token("short")
