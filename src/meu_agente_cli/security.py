import hashlib
import os
import subprocess
import getpass
from typing import Tuple
from meu_agente_cli.db import get_connection

# Conjunto de executáveis considerados seguros por padrão
SAFE_EXECUTABLES = {"ls", "pwd", "date", "whoami", "uptime", "free", "df", "uname", "echo", "cat"}

# Estado global do modo de segurança em tempo de execução
_safe_mode = True

def is_safe_mode() -> bool:
    global _safe_mode
    return _safe_mode

def set_safe_mode(enabled: bool):
    global _safe_mode
    _safe_mode = enabled

def is_command_safe(cmd_str: str) -> bool:
    """
    Verifica se um comando shell é seguro para execução direta.
    - Impede encadeadores de comando perigosos (; | & $ ` ( ) > < \n).
    - O executável principal precisa estar na lista SAFE_EXECUTABLES.
    """
    cmd_clean = cmd_str.strip()
    if not cmd_clean:
        return False
        
    # Caracteres perigosos que podem injetar outros comandos
    dangerous_chars = [";", "|", "&", "$", "`", "(", ")", ">", "<", "\n"]
    for char in dangerous_chars:
        if char in cmd_clean:
            return False
            
    # Extrai o executável principal (primeira palavra)
    parts = cmd_clean.split()
    executable = parts[0]
    
    # Se o executável for caminhos relativos ou absolutos, pega o nome base
    executable = os.path.basename(executable)
    
    return executable in SAFE_EXECUTABLES

def hash_password(password: str, salt: str = "meu_agente_sal_seguro") -> str:
    """Retorna o hash SHA-256 da senha combinada com o salt."""
    salted = password + salt
    return hashlib.sha256(salted.encode('utf-8')).hexdigest()

def has_password_configured() -> bool:
    """Verifica se já existe uma senha configurada no banco de dados settings."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key = 'security_password_hash'")
            res = cur.fetchone()
        conn.close()
        return res is not None
    except Exception:
        return False

def configure_password(password: str) -> bool:
    """Salva o hash da nova senha de segurança no banco de dados."""
    try:
        h = hash_password(password)
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (key, value) VALUES ('security_password_hash', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (h,)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao configurar a senha no banco: {e}")
        return False

def verify_password(password: str) -> bool:
    """Verifica se a senha fornecida coincide com a configurada no banco."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key = 'security_password_hash'")
            res = cur.fetchone()
        conn.close()
        if not res:
            return False
        stored_hash = res[0]
        return hash_password(password) == stored_hash
    except Exception:
        return False

def run_bash_command(cmd_str: str) -> Tuple[int, str, str]:
    """
    Executa o comando no WSL usando bash -c.
    Retorna o código de saída, stdout e stderr garantindo que as strings
    não contenham caracteres surrogate inválidos (usando decode errors='replace').
    """
    try:
        # Executa capturando bytes brutos para evitar surrogates no parser de texto padrão
        res = subprocess.run(
            ["bash", "-c", cmd_str],
            capture_output=True,
            timeout=30
        )
        out = res.stdout.decode('utf-8', errors='replace')
        err = res.stderr.decode('utf-8', errors='replace')
        return res.returncode, out, err
    except subprocess.TimeoutExpired:
        return -1, "", "Erro: O comando expirou (timeout de 30s)"
    except Exception as e:
        return -1, "", f"Erro na execução: {str(e)}"
