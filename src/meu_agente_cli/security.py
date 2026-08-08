import hashlib
import os
import subprocess
import getpass
from typing import Tuple
from meu_agente_cli.db import get_connection

# Conjunto de executáveis considerados seguros por padrão
SAFE_EXECUTABLES = {"ls", "pwd", "date", "whoami", "uptime", "free", "df", "uname", "echo", "cat"}

def calculate_signature(commands: list, password_hash: str) -> str:
    """Calcula uma assinatura hash auxiliar dos comandos (usada apenas como fallback)."""
    sorted_cmds = sorted([c.strip() for c in commands if c.strip()])
    joined = ",".join(sorted_cmds)
    payload = f"{joined}:{password_hash}"
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()

def get_safe_executables() -> set:
    """
    Retorna o conjunto completo de comandos seguros.
    Lê a lista de comandos seguros adicionais do arquivo 'safe_commands.json' 
    na raiz do projeto e verifica a assinatura digital usando a chave pública do banco.
    """
    execs = SAFE_EXECUTABLES.copy()
    try:
        from pathlib import Path
        import json
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives import serialization
        
        config_file = Path(__file__).parent.parent.parent / "safe_commands.json"
        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            added = data.get("additional_safe_commands", [])
            signature_hex = data.get("signature", "")
            
            # Se não houver comandos adicionais, não precisa validar nada
            if not added:
                return execs
                
            if not signature_hex:
                print("[WARNING] O arquivo safe_commands.json contém comandos adicionais mas não está assinado. Ignorando adicionais.")
                return execs
                
            # Busca a chave pública do banco
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM settings WHERE key = 'security_public_key'")
                res = cur.fetchone()
            conn.close()
            
            if not res:
                print("[WARNING] Chave pública de segurança não encontrada. Ignorando comandos adicionais.")
                return execs
                
            public_pem = res[0].encode('utf-8')
            public_key = serialization.load_pem_public_key(public_pem)
            
            # Ordena e normaliza para assinatura
            sorted_cmds = sorted([c.strip() for c in added if c.strip()])
            payload = ",".join(sorted_cmds).encode('utf-8')
            signature = bytes.fromhex(signature_hex)
            
            # Verifica a assinatura digital usando a chave pública
            try:
                public_key.verify(
                    signature,
                    payload,
                    padding.PSS(
                        mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH
                    ),
                    hashes.SHA256()
                )
                # Assinatura válida! Adiciona os comandos
                for val in added:
                    val = val.strip()
                    if val:
                        execs.add(val)
            except Exception:
                print("[WARNING] A assinatura do arquivo safe_commands.json é INVÁLIDA! Modificação não autorizada detectada. Comandos adicionais ignorados.")
    except Exception as e:
        print(f"[WARNING] Erro ao carregar/validar comandos adicionais: {e}")
    return execs

def sign_safe_commands_file(password: str) -> bool:
    """Assina o arquivo safe_commands.json usando a chave privada correspondente e a senha do usuário."""
    try:
        from pathlib import Path
        import json
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives import serialization
        
        # Primeiro, verifica se as chaves existem
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key = 'security_private_key_encrypted'")
            res = cur.fetchone()
        conn.close()
        
        # Caso chaves não existam mas a senha de compatibilidade seja válida, gera as chaves RSA agora
        if not res:
            if verify_password(password):
                print("[INFO] Gerando par de chaves RSA inicial para assinatura...")
                if not configure_password(password):
                    return False
                conn = get_connection()
                with conn.cursor() as cur:
                    cur.execute("SELECT value FROM settings WHERE key = 'security_private_key_encrypted'")
                    res = cur.fetchone()
                conn.close()
            else:
                return False
                
        private_pem_enc = res[0].encode('utf-8')
        
        # Descriptografa a chave privada usando a senha fornecida pelo usuário
        try:
            private_key = serialization.load_pem_private_key(
                private_pem_enc,
                password=password.encode('utf-8')
            )
        except Exception:
            print("[ERROR] Senha de segurança incorreta!")
            return False
            
        # Lê os comandos do safe_commands.json
        config_file = Path(__file__).parent.parent.parent / "safe_commands.json"
        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"additional_safe_commands": []}
            
        added = data.get("additional_safe_commands", [])
        
        # Ordena e normaliza
        sorted_cmds = sorted([c.strip() for c in added if c.strip()])
        payload = ",".join(sorted_cmds).encode('utf-8')
        
        # Gera a assinatura digital
        signature = private_key.sign(
            payload,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        
        data["signature"] = signature.hex()
        
        # Grava o JSON atualizado
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao assinar safe_commands.json: {e}")
        return False

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
    - O executável principal precisa estar na lista SAFE_EXECUTABLES ou na dinâmica.
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
    
    return executable in get_safe_executables()

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
    """Salva o hash da nova senha de segurança no banco de dados e gera o par de chaves RSA correspondente."""
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        
        h = hash_password(password)
        
        # Gera o par de chaves RSA de 2048 bits
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048
        )
        
        # Criptografa a chave privada com a senha do usuário
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(password.encode('utf-8'))
        )
        
        # Chave pública em formato aberto
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (key, value) VALUES ('security_password_hash', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (h,)
            )
            cur.execute(
                "INSERT INTO settings (key, value) VALUES ('security_private_key_encrypted', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (private_pem.decode('utf-8'),)
            )
            cur.execute(
                "INSERT INTO settings (key, value) VALUES ('security_public_key', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (public_pem.decode('utf-8'),)
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
