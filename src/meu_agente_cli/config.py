import os
import re
import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "meu-agente-cli"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_SETTINGS = {
    "lm_studio_host": None,  # Will be auto-detected if None
    "lm_studio_port": 1234,
    "active_model": "google/gemma-4-31b-qat",
    "safe_mode": True,
    "db_user": "postgres",
    "db_password": "",
    "db_host": "127.0.0.1",
    "db_port": 5432
}

def get_wsl_host_ip() -> str:
    """
    Tenta detectar o IP do host Windows a partir do ambiente WSL.
    Verifica o arquivo /etc/resolv.conf em busca da diretiva 'nameserver'.
    Caso contrário, retorna '127.0.0.1'.
    """
    if os.path.exists('/etc/resolv.conf'):
        try:
            with open('/etc/resolv.conf', 'r') as f:
                for line in f:
                    if line.strip().startswith('nameserver'):
                        parts = line.split()
                        if len(parts) >= 2:
                            ip = parts[1].strip()
                            # Valida se é um IP válido
                            if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip):
                                return ip
        except Exception:
            pass
            
    # Tenta usar a rota default como alternativa
    try:
        import subprocess
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            match = re.search(r'via\s+([0-9.]+)', result.stdout)
            if match:
                return match.group(1)
    except Exception:
        pass

    return '127.0.0.1'

def load_bootstrap_config() -> dict:
    """
    Carrega as configurações iniciais do arquivo JSON de bootstrap.
    Cria as pastas e o arquivo com padrões caso não existam.
    """
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        
    if not CONFIG_FILE.exists():
        save_bootstrap_config(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS.copy()
        
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            # Garante que chaves padrão existam
            for k, v in DEFAULT_SETTINGS.items():
                if k not in config:
                    config[k] = v
            return config
    except Exception:
        return DEFAULT_SETTINGS.copy()

def save_bootstrap_config(config: dict) -> None:
    """
    Salva as configurações iniciais no arquivo JSON.
    """
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"Erro ao salvar configurações locais: {e}")

def get_lm_studio_url() -> str:
    """
    Retorna a URL base do LM Studio após verificar IP configurado ou detectado.
    """
    config = load_bootstrap_config()
    host = config.get("lm_studio_host")
    if not host:
        host = get_wsl_host_ip()
    port = config.get("lm_studio_port", 1234)
    return f"http://{host}:{port}"

def clean_string(s: str) -> str:
    """
    Remove ou substitui caracteres surrogate em strings do Python
    para evitar erros de codificação UTF-8 (ex: ao salvar no PostgreSQL ou enviar via HTTP).
    """
    if not isinstance(s, str):
        return s
    return s.encode('utf-8', errors='surrogateescape').decode('utf-8', errors='replace')
