import hashlib
import hmac
import secrets


def hash_password(password: str, salt: str | None = None) -> str:
    resolved_salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(resolved_salt),
        n=16384,
        r=8,
        p=1,
    ).hex()
    return f"{resolved_salt}:{digest}"


def check_password(password: str, stored: str) -> bool:
    return hmac.compare_digest(hash_password(password, stored.split(":", 1)[0]), stored)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
