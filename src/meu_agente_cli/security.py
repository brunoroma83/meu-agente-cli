import hashlib
import os
import secrets
from typing import Optional, Tuple
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "finance-secret-key-change-in-production-12345")
_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="finance-web-session")

def hash_password(password: str) -> str:
    """Gera um hash PBKDF2-HMAC-SHA256 seguro com salt aleatório."""
    salt = secrets.token_bytes(16)
    iterations = 100_000
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${pwd_hash.hex()}"

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica se a senha em texto puro confere com o hash gerado."""
    try:
        parts = hashed_password.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            # Suporte a migração transparente de hashes legados (SHA-256 com salt fixo)
            if len(hashed_password) == 64:
                legacy_salt = "meu_agente_sal_seguro"
                legacy_hash = hashlib.sha256((plain_password + legacy_salt).encode("utf-8")).hexdigest()
                return secrets.compare_digest(legacy_hash, hashed_password)
            return False
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected_hash = bytes.fromhex(parts[3])
        actual_hash = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, iterations)
        return secrets.compare_digest(actual_hash, expected_hash)
    except Exception:
        return False

def generate_mcp_token() -> Tuple[str, str, str]:
    """
    Gera um novo token para o MCP Server.
    Retorna (raw_token, token_hash, raw_token_prefix).
    O raw_token é mostrado apenas uma vez ao usuário.
    O token_hash (SHA-256) é salvo no banco de dados.
    """
    random_part = secrets.token_urlsafe(32)
    raw_token = f"mcp_live_{random_part}"
    token_hash = hash_token(raw_token)
    prefix = raw_token[:15] + "..."
    return raw_token, token_hash, prefix

def hash_token(raw_token: str) -> str:
    """Calcula o SHA-256 do token para armazenamento seguro."""
    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()

def create_session_token(user_name: str, display_name: str, role: str) -> str:
    """Cria um token assinado para cookie de sessão web."""
    payload = {
        "user_name": user_name,
        "display_name": display_name,
        "role": role,
    }
    return _serializer.dumps(payload)

def verify_session_token(token_str: str, max_age_seconds: int = 86400 * 30) -> Optional[dict]:
    """Valida o cookie de sessão e retorna o payload do usuário."""
    try:
        return _serializer.loads(token_str, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
