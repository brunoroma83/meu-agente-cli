import subprocess

def run() -> str:
    """Testa a conexão realizando ping para o Google."""
    try:
        # Faz um ping de 2 pacotes
        res = subprocess.run(["ping", "-c", "2", "8.8.8.8"], capture_output=True, text=True, timeout=5.0)
        if res.returncode == 0:
            return f"[SUCCESS] Conexão de internet ativa! Resposta:\n{res.stdout}"
        else:
            return f"[ERROR] Falha no teste de ping:\n{res.stderr}"
    except Exception as e:
        return f"[ERROR] Falha ao executar subprocesso de ping: {e}"
