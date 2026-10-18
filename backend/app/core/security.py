"""Password hashing (bcrypt). Tokens and role checks come with auth (T3.2)."""

import bcrypt

BCRYPT_ROUNDS = 12


def hash_password(password: str, rounds: int = BCRYPT_ROUNDS) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())
