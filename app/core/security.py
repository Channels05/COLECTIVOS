import hashlib
import hmac
import secrets


def generate_device_token() -> str:
    """Token que se entrega UNA vez al registrar el vehículo y queda guardado en la app del conductor."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    # SHA-256 basta: el token es aleatorio de alta entropía (no es una contraseña humana),
    # y es barato de verificar en un endpoint que se llama cada 5 s.
    return hashlib.sha256(token.encode()).hexdigest()


def verify_token(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), token_hash)
