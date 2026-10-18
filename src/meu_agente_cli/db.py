import os
import logging
import subprocess
import time
import sys
import getpass
import json
from decimal import Decimal
from datetime import datetime, date
from typing import List, Tuple, Dict, Any, Optional
import psycopg
from psycopg import Connection
from croniter import croniter
from meu_agente_cli.config import load_bootstrap_config, clean_string

DB_NAME = os.environ.get("DB_NAME") or "meu_agente_cli"

def run_wsl_command(cmd_list: list) -> subprocess.CompletedProcess:
    """Executa um comando no WSL."""
    if os.name == 'nt':
        return subprocess.run(["wsl"] + cmd_list, capture_output=True, encoding="utf-8", errors="ignore")
    return subprocess.run(cmd_list, capture_output=True, encoding="utf-8", errors="ignore")

def is_postgresql_installed() -> bool:
    """Verifica se o PostgreSQL está instalado no WSL."""
    res = run_wsl_command(["which", "psql"])
    return res.returncode == 0

def install_postgresql() -> bool:
    """Tenta instalar o PostgreSQL no WSL via apt."""
    print("\n[INFO] PostgreSQL não está instalado no WSL.")
    print("[INFO] Tentando instalar PostgreSQL automaticamente via apt (pode ser necessária sua senha sudo)...")
    
    # Executa de forma interativa para que o usuário possa digitar a senha do sudo se necessário
    try:
        if os.name == 'nt':
            subprocess.run(["wsl", "sudo", "apt-get", "update"], check=True)
            subprocess.run(["wsl", "sudo", "apt-get", "install", "-y", "postgresql", "postgresql-contrib"], check=True)
        else:
            subprocess.run(["sudo", "apt-get", "update"], check=True)
            subprocess.run(["sudo", "apt-get", "install", "-y", "postgresql", "postgresql-contrib"], check=True)
        print("[SUCCESS] PostgreSQL instalado com sucesso!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Falha ao instalar o PostgreSQL via apt: {e}", file=sys.stderr)
        print("[SUGGESTION] Por favor, instale o PostgreSQL manualmente no WSL executando:")
        print("  sudo apt-get update && sudo apt-get install -y postgresql postgresql-contrib")
        return False

def ensure_postgresql_service() -> bool:
    """
    Garante que o PostgreSQL esteja instalado e com o serviço rodando no WSL.
    """
    if os.path.exists("/.dockerenv"):
        return True

    cfg = load_bootstrap_config()
    db_host = os.environ.get("DB_HOST") or cfg.get("db_host", "127.0.0.1")
    if db_host not in ("127.0.0.1", "localhost"):
        return True

    if not is_postgresql_installed():
        success = install_postgresql()
        if not success:
            return False

    # Verifica o status do serviço
    status_res = run_wsl_command(["service", "postgresql", "status"])
    # Se o serviço estiver parado (ou se o retorno não indicar que está ativo)
    if "online" not in status_res.stdout and "running" not in status_res.stdout and "active" not in status_res.stdout:
        print("\n[INFO] Iniciando o serviço PostgreSQL no WSL...")
        try:
            # Roda interativo caso necessite de senha sudo
            if os.name == 'nt':
                subprocess.run(["wsl", "sudo", "service", "postgresql", "start"], check=True)
            else:
                subprocess.run(["sudo", "service", "postgresql", "start"], check=True)
            # Dá um tempo para o serviço subir
            time.sleep(2)
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Não foi possível iniciar o serviço PostgreSQL: {e}", file=sys.stderr)
            return False
            
    return True

def get_connection(dbname: Optional[str] = None) -> Connection:
    """
    Retorna uma conexão com o banco de dados PostgreSQL.
    """
    if dbname is None:
        dbname = DB_NAME

    cfg = load_bootstrap_config()
    db_user = os.environ.get("DB_USER") or cfg.get("db_user", "postgres")
    db_pass = os.environ.get("DB_PASSWORD") or cfg.get("db_password", "")
    db_host = os.environ.get("DB_HOST") or cfg.get("db_host", "127.0.0.1")
    db_port_val = os.environ.get("DB_PORT") or cfg.get("db_port", 5432)

    try:
        db_port = int(db_port_val)
    except (ValueError, TypeError):
        db_port = 5432

    # 1. Tenta peer authentication local (Unix socket no WSL) se não estiver no Docker
    if db_host in ("127.0.0.1", "localhost") and not os.path.exists("/.dockerenv"):
        try:
            return psycopg.connect(dbname=dbname)
        except psycopg.Error:
            pass

    # 2. Se falhar ou se for conexão remota, tenta conexão TCP
    try:
        return psycopg.connect(
            dbname=dbname,
            user=db_user,
            password=db_pass,
            host=db_host,
            port=db_port
        )
    except psycopg.Error as e:
        logging.error("Falha ao obter conexão com o banco de dados %s: %s", dbname, e)
        raise e

def init_database() -> bool:
    """
    Inicializa o banco de dados, cria as tabelas se necessário.
    """
    if not ensure_postgresql_service():
        logging.error("Falha ao configurar serviço PostgreSQL no WSL.")
        print("[ERROR] Falha ao configurar serviço PostgreSQL no WSL.", file=sys.stderr)
        return False

    cfg = load_bootstrap_config()
    connected = False
    conn = None

    while not connected:
        try:
            conn = get_connection(dbname="postgres")
            connected = True
        except psycopg.Error as e:
            err_msg = str(e)
            # Se for erro de senha / autenticação, solicita credenciais interativamente
            if "password" in err_msg or "authentication" in err_msg or "fe_sendauth" in err_msg:
                if os.environ.get("DB_HOST") or os.path.exists("/.dockerenv"):
                    logging.error("Erro de autenticação com o banco de dados configurado via ambiente: %s", e)
                    print(f"[ERROR] Erro de autenticação com o banco de dados configurado via ambiente: {e}", file=sys.stderr)
                    return False

                print(f"\n[POSTGRES] Erro de autenticação: {err_msg.strip()}")
                print("Por favor, forneça as credenciais de acesso TCP/IP para o PostgreSQL no WSL.")
                
                db_user = input(f"Usuário PostgreSQL [{cfg.get('db_user', 'postgres')}]: ").strip() or cfg.get('db_user', 'postgres')
                db_pass = getpass.getpass("Senha PostgreSQL: ")
                db_host = input(f"Host [{cfg.get('db_host', '127.0.0.1')}]: ").strip() or cfg.get('db_host', '127.0.0.1')
                db_port_str = input(f"Porta [{cfg.get('db_port', 5432)}]: ").strip()
                db_port = int(db_port_str) if db_port_str.isdigit() else cfg.get('db_port', 5432)
                
                # Salva no arquivo de bootstrap config.json
                cfg["db_user"] = db_user
                cfg["db_password"] = db_pass
                cfg["db_host"] = db_host
                cfg["db_port"] = db_port
                
                from meu_agente_cli.config import save_bootstrap_config
                save_bootstrap_config(cfg)
            else:
                # Outro erro de conexão
                logging.error("Erro ao conectar ao banco de dados: %s", e)
                print(f"[ERROR] Erro ao conectar ao banco de dados: {e}", file=sys.stderr)
                return False

    # Conecta primeiro ao banco default 'postgres' para verificar/criar o banco 'meu_agente_cli'
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            # Verifica se o banco existe
            cur.execute(f"SELECT 1 FROM pg_database WHERE datname = '{DB_NAME}'")
            exists = cur.fetchone()
            if not exists:
                print(f"[INFO] Criando banco de dados '{DB_NAME}'...")
                cur.execute(f"CREATE DATABASE {DB_NAME}")
        conn.close()
    except Exception as e:
        logging.exception("Erro ao conectar ou criar banco de dados inicial")
        print(f"[ERROR] Erro ao conectar ou criar banco de dados inicial: {e}", file=sys.stderr)
        if conn:
            conn.close()
        return False

    # Conecta ao banco 'meu_agente_cli' e cria as tabelas
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            # 1. Tabela settings
            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key VARCHAR(50) PRIMARY KEY,
                    value TEXT
                )
            """)
            
            # 2. Tabela chat_history
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id SERIAL PRIMARY KEY,
                    sender VARCHAR(10) NOT NULL,
                    message TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 3. Tabela user_notes
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_notes (
                    id SERIAL PRIMARY KEY,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    active BOOLEAN DEFAULT TRUE
                )
            """)
            
            # 4. Tabela cron_jobs
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cron_jobs (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    cron_expression VARCHAR(50) NOT NULL,
                    next_run TIMESTAMP NOT NULL,
                    last_run TIMESTAMP,
                    task_prompt TEXT NOT NULL,
                    status VARCHAR(20) DEFAULT 'active',
                    active BOOLEAN DEFAULT TRUE
                )
            """)

            # 5. Tabela de Transcrições de Áudio
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audio_transcriptions (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    audio_path VARCHAR(512) NOT NULL,
                    transcription TEXT NOT NULL,
                    user_prompt TEXT NOT NULL,
                    llm_response TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    active BOOLEAN DEFAULT TRUE
                )
            """)

            # 8. Cadastro Usuários
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    user_name VARCHAR(100) NOT NULL UNIQUE,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 9. Tabela de Perfil do Usuário 
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_profile (
                    user_id INT NOT NULL,
                    category VARCHAR(50) NOT NULL,
                    content TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT pk_user_profile
                        PRIMARY KEY (user_id, category),
                    CONSTRAINT fk_user_profile
                        FOREIGN KEY (user_id)
                        REFERENCES users(user_id)
                        ON DELETE CASCADE
                )
            """)
            
            # 11. Tabela de Agentes Especialistas (Agent Hub)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS agents (
                    id SERIAL PRIMARY KEY,
                    slug VARCHAR(50) UNIQUE NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    icon VARCHAR(10) DEFAULT '🤖',
                    description TEXT,
                    system_prompt TEXT NOT NULL,
                    allowed_tools TEXT[] DEFAULT NULL,
                    is_default BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 12. Tabela de Servidores MCP (Model Context Protocol)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mcp_servers (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) UNIQUE NOT NULL,
                    url TEXT NOT NULL,
                    api_key TEXT,
                    transport VARCHAR(20) DEFAULT 'sse',
                    headers JSONB DEFAULT '{}'::jsonb,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Migrations para bases de dados existentes
            cur.execute("ALTER TABLE user_notes ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
            cur.execute("ALTER TABLE cron_jobs ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
            cur.execute("ALTER TABLE audio_transcriptions ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
            cur.execute("ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")
            cur.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_default BOOLEAN DEFAULT FALSE")
            
        conn.commit()
        conn.close()
        
        # Garante os agentes padrão (geral e estudo)
        try:
            seed_default_agents()
        except Exception as _agent_ex:
            logging.warning("Não foi possível verificar/criar agentes padrão: %s", _agent_ex)

        logging.info("Banco de dados inicializado com sucesso!")
        print("[SUCCESS] Banco de dados inicializado com sucesso!")
        return True
    except Exception as e:
        logging.exception("Erro ao inicializar tabelas do banco de dados")
        print(f"[ERROR] Erro ao inicializar tabelas do banco de dados: {e}", file=sys.stderr)
        return False

# =====================================================================
# OPERAÇÕES CRUD E DE APOIO
# =====================================================================

# 1. Configurações (Settings)
def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Retorna um valor das configurações."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
            res = cur.fetchone()
        conn.close()
        return res[0] if res else default
    except Exception:
        return default

def set_setting(key: str, value: str) -> bool:
    """Insere ou atualiza um valor nas configurações."""
    try:
        val_clean = clean_string(value)
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, val_clean)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao salvar configuração {key}: {e}", file=sys.stderr)
        return False

def get_chat_history_limit() -> int:
    """Retorna o limite configurado de mensagens de histórico para enviar ao LLM. Padrão: 4."""
    val = get_setting("chat_history_limit", "4")
    try:
        return int(val)
    except ValueError:
        return 4

def set_chat_history_limit(limit: int) -> bool:
    """Configura o limite de mensagens de histórico para enviar ao LLM."""
    if limit < 1:
        return False
    return set_setting("chat_history_limit", str(limit))

def get_backup_llm_config() -> dict:
    """Retorna a configuração completa do LLM de backup."""
    enabled_val = get_setting("backup_llm_enabled", "false")
    return {
        "enabled": str(enabled_val).lower() in ("true", "1", "yes"),
        "provider": get_setting("backup_llm_provider", "") or "",
        "model": get_setting("backup_active_model", "") or "",
        "api_key": get_setting("backup_provider_api_key", "") or "",
        "base_url": get_setting("backup_provider_base_url", "") or ""
    }

def set_backup_llm_config(
    enabled: Optional[bool] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None
) -> bool:
    """Atualiza as configurações do LLM de backup no banco de dados."""
    success = True
    if enabled is not None:
        success = success and set_setting("backup_llm_enabled", "true" if enabled else "false")
    if provider is not None:
        success = success and set_setting("backup_llm_provider", provider.strip().lower())
    if model is not None:
        success = success and set_setting("backup_active_model", model.strip())
    if api_key is not None:
        success = success and set_setting("backup_provider_api_key", api_key.strip())
    if base_url is not None:
        success = success and set_setting("backup_provider_base_url", base_url.strip())
    return success

def get_provider_api_key(provider: str) -> str:
    """Busca a API key específica do provedor ou fallback para provider_api_key."""
    specific_key = get_setting(f"api_key_{provider}", "")
    if specific_key:
        return specific_key
    active_prov = get_setting("llm_provider", "lm_studio")
    if provider == active_prov:
        return get_setting("provider_api_key", "") or ""
    return ""

def set_provider_api_key(provider: str, api_key: str) -> bool:
    """Salva a API key específica do provedor e atualiza provider_api_key se for o ativo."""
    ok = set_setting(f"api_key_{provider}", api_key)
    active_prov = get_setting("llm_provider", "lm_studio")
    if provider == active_prov:
        set_setting("provider_api_key", api_key)
    return ok

# 2. Histórico de Conversa (Chat History)
def save_chat_message(sender: str, message: Any) -> bool:
    """Salva uma mensagem do histórico no banco de dados (suporta strings ou estruturas multimodais JSON)."""
    try:
        if isinstance(message, (list, dict)):
            msg_clean = json.dumps(message, ensure_ascii=False)
        else:
            msg_clean = clean_string(str(message))
            
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO chat_history (sender, message) VALUES (%s, %s)",
                (sender, msg_clean)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao salvar mensagem no histórico: {e}", file=sys.stderr)
        return False

def get_chat_history(limit: int = 20) -> List[Tuple[str, Any]]:
    """Retorna as últimas mensagens do histórico de chat (desfazendo serialização JSON para multimodalidade)."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sender, message FROM chat_history ORDER BY id DESC LIMIT %s",
                (limit,)
            )
            rows = cur.fetchall()
        conn.close()
        
        # Desfaz serialização JSON se o conteúdo for uma estrutura de lista ou dict de multimodalidade
        history = []
        for r in reversed(rows):
            sender, msg = r[0], r[1]
            if msg.startswith("[") or msg.startswith("{"):
                try:
                    msg = json.loads(msg)
                except Exception:
                    pass
            history.append((sender, msg))
            
        return history
    except Exception as e:
        print(f"[ERROR] Erro ao ler histórico: {e}", file=sys.stderr)
        return []

def clear_chat_history() -> bool:
    """Limpa todo o histórico de conversas."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chat_history")
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao limpar histórico: {e}", file=sys.stderr)
        return False

# 3. Notas do Usuário (User Notes)
def add_user_note(content: str) -> bool:
    """Adiciona uma nova anotação/fato à memória do agente."""
    try:
        content_clean = clean_string(content)
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("INSERT INTO user_notes (content) VALUES (%s)", (content_clean,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao salvar nota: {e}", file=sys.stderr)
        return False

def search_user_notes(query: str) -> List[Tuple[int, str, datetime]]:
    """Busca anotações contendo o termo pesquisado e que estejam ativas."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, content, created_at FROM user_notes WHERE active = TRUE AND content ILIKE %s ORDER BY id DESC",
                (f"%{query}%",)
            )
            rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"[ERROR] Erro ao pesquisar notas: {e}", file=sys.stderr)
        return []

def list_all_user_notes() -> List[Tuple[int, str, datetime]]:
    """Retorna todas as notas salvas que estejam ativas."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id, content, created_at FROM user_notes WHERE active = TRUE ORDER BY id DESC")
            rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"[ERROR] Erro ao listar notas: {e}", file=sys.stderr)
        return []

def delete_user_note(note_id: int) -> bool:
    """Inativa (soft delete) uma nota específica."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE user_notes SET active = FALSE WHERE id = %s", (note_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao desativar nota: {e}", file=sys.stderr)
        return False

# 4. Agendamentos de Tarefas (Cron Jobs)
def add_cron_job(name: str, cron_expression: str, task_prompt: str) -> bool:
    """Adiciona um novo cron job de subagente, calculando o próximo disparo."""
    try:
        name_clean = clean_string(name)
        prompt_clean = clean_string(task_prompt)
        # Valida a expressão cron e calcula o próximo disparo
        base_time = datetime.now()
        iter = croniter(cron_expression, base_time)
        next_run = iter.get_next(datetime)
        
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO cron_jobs (name, cron_expression, next_run, task_prompt) VALUES (%s, %s, %s, %s)",
                (name_clean, cron_expression, next_run, prompt_clean)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao criar cronjob: {e}", file=sys.stderr)
        return False

def get_active_cron_jobs() -> List[Dict[str, Any]]:
    """Retorna todos os cronjobs ativos (e que não estejam desativados logicamente)."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, cron_expression, next_run, last_run, task_prompt, status "
                "FROM cron_jobs WHERE active = TRUE AND status = 'active'"
            )
            rows = cur.fetchall()
        conn.close()
        jobs = []
        for r in rows:
            jobs.append({
                "id": r[0],
                "name": r[1],
                "cron_expression": r[2],
                "next_run": r[3],
                "last_run": r[4],
                "task_prompt": r[5],
                "status": r[6]
            })
        return jobs
    except Exception as e:
        print(f"[ERROR] Erro ao buscar cronjobs: {e}", file=sys.stderr)
        return []

def update_cron_job_runs(job_id: int, last_run: datetime, next_run: datetime) -> bool:
    """Atualiza o histórico de execução de um cronjob."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE cron_jobs SET last_run = %s, next_run = %s WHERE id = %s",
                (last_run, next_run, job_id)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao atualizar cronjob: {e}", file=sys.stderr)
        return False

def update_cron_job(job_id: int, name: Optional[str], cron_expression: Optional[str], task_prompt: Optional[str]) -> bool:
    """Atualiza um cronjob."""
    try:
        conn = get_connection()
        # pega as informações do cronjob a ser atualizado
        active_jobs = get_active_cron_jobs()
        job = None
        job_id_found = False
        for job in active_jobs:
            if job["id"] == job_id:
                job_id_found = True
                if name:
                    job["name"] = name
                if cron_expression:
                    job["cron_expression"] = cron_expression
                if task_prompt:
                    job["task_prompt"] = task_prompt
                break
        if not job_id_found:
            logging.error(f"Erro: Job ID {job_id} não encontrado.")
            print(f"Erro: Job ID {job_id} não encontrado.", file=sys.stderr)
            return False
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE cron_jobs SET name = %s, cron_expression = %s, task_prompt = %s WHERE id = %s",
                (job["name"], job["cron_expression"], job["task_prompt"], job_id)
            )
        conn.commit()
        conn.close()
        # salvar no log a alteração do cronjob
        logging.info(f"Cronjob #{job_id} atualizado: {job}")
        print(f"Cronjob #{job_id} atualizado: {job}")
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao atualizar cronjob: {e}", file=sys.stderr)
        logging.error(f"Erro ao atualizar cronjob: {e}")
        return False

def delete_cron_job(job_id: int) -> bool:
    """Desativa (soft delete) um cronjob."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE cron_jobs SET active = FALSE WHERE id = %s", (job_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao desativar cronjob: {e}", file=sys.stderr)
        return False

def _format_sql_value(val: Any) -> str:
    """Formata um valor Python para uma representação SQL literal segura."""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float, Decimal)):
        return str(val)
    if isinstance(val, datetime):
        return f"'{val.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(val, date):
        return f"'{val.strftime('%Y-%m-%d')}'"
    if isinstance(val, (dict, list)):
        escaped = json.dumps(val, ensure_ascii=False).replace("'", "''")
        return f"'{escaped}'"
    if isinstance(val, bytes):
        hex_str = val.hex()
        return f"decode('{hex_str}', 'hex')"
    val_str = str(val).replace("'", "''")
    return f"'{val_str}'"

def generate_sql_dump() -> str:
    """Gera um dump SQL portátil contendo todos os dados e estruturas do banco."""
    conn = get_connection()
    dump = ["BEGIN;"]
    
    # Ordem das tabelas para limpeza segura (tabelas dependentes/filhas antes das mães)
    tables_delete_order = [
        "user_profile",        # FK para users
        "users",
        "audio_transcriptions",
        "cron_jobs",
        "user_notes",
        "mcp_servers",
        "agents",
        "chat_history",
        "settings",
    ]
    
    try:
        with conn.cursor() as cur:
            # Descobre quais tabelas realmente existem no schema public
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)
            existing_tables = {row[0] for row in cur.fetchall()}
            
            # Filtra apenas tabelas existentes
            active_delete_tables = [t for t in tables_delete_order if t in existing_tables]
            
            # 1. Comandos de limpeza (ordem reversa de dependência)
            for t in active_delete_tables:
                dump.append(f"DELETE FROM {t};")
                
            # 2. Inserção de dados (ordem direta: mães antes de filhas)
            for table in reversed(active_delete_tables):
                cur.execute(f"SELECT * FROM {table}")
                rows = cur.fetchall()
                if not rows:
                    continue
                    
                col_names = [desc[0] for desc in cur.description]
                cols_str = ", ".join(col_names)
                override = " OVERRIDING SYSTEM VALUE" if table in ["users"] else ""
                
                for row in rows:
                    formatted_vals = [_format_sql_value(v) for v in row]
                    vals_str = ", ".join(formatted_vals)
                    dump.append(f"INSERT INTO {table} ({cols_str}){override} VALUES ({vals_str});")
                    
            # 3. Sincronização de sequences / auto-incrementos
            sequence_tables = [
                ("user_notes", "id"),
                ("cron_jobs", "id"),
                ("audio_transcriptions", "id"),
                ("chat_history", "id"),
                ("users", "user_id"),
                ("agents", "id"),
                ("mcp_servers", "id"),
            ]
            
            for table, pk in sequence_tables:
                if table in existing_tables:
                    dump.append(
                        f"SELECT setval(pg_get_serial_sequence('{table}', '{pk}'), "
                        f"COALESCE((SELECT MAX({pk}) FROM {table}), 1), "
                        f"(SELECT (MAX({pk}) IS NOT NULL) FROM {table}));"
                    )
                    
        dump.append("COMMIT;")
        return "\n".join(dump)
    finally:
        conn.close()

def restore_sql_dump(sql_content: str) -> bool:
    """Executa o script SQL de restauração dentro de uma transação atômica."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            # Executa todo o dump SQL de uma vez
            cur.execute(sql_content)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Falha ao restaurar dump SQL: {e}", file=sys.stderr)
        return False

# 6. Transcrições de Áudio (Audio Transcriptions / Reuniões)
def save_audio_transcription(chat_id: int, audio_path: str, transcription: str, user_prompt: str, llm_response: str) -> bool:
    """Salva uma transcrição consolidada com o áudio original, prompt e resposta da LLM no banco."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audio_transcriptions (chat_id, audio_path, transcription, user_prompt, llm_response) VALUES (%s, %s, %s, %s, %s)",
                (chat_id, audio_path, clean_string(transcription), clean_string(user_prompt), clean_string(llm_response))
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao salvar transcrição de áudio: {e}", file=sys.stderr)
        return False

def list_audio_transcriptions(chat_id: int, limit: int = 15) -> List[Tuple[int, str, str, str, datetime]]:
    """Retorna a lista de transcrições de áudio ativas registradas para o usuário."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, audio_path, transcription, user_prompt, created_at FROM audio_transcriptions "
                "WHERE chat_id = %s AND active = TRUE ORDER BY id DESC LIMIT %s",
                (chat_id, limit)
            )
            rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"[ERROR] Erro ao listar transcrições de áudio: {e}", file=sys.stderr)
        return []

def list_deleted_audio_transcriptions(chat_id: int, limit: int = 15) -> List[Tuple[int, str, str, str, datetime]]:
    """Retorna a lista de transcrições de áudio inativas (soft deleted) registradas para o usuário."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, audio_path, transcription, user_prompt, created_at FROM audio_transcriptions "
                "WHERE chat_id = %s AND active = FALSE ORDER BY id DESC LIMIT %s",
                (chat_id, limit)
            )
            rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"[ERROR] Erro ao listar transcrições de áudio inativas: {e}", file=sys.stderr)
        return []

def get_audio_transcription_by_id(chat_id: int, doc_id: int) -> Optional[Tuple[int, str, str, str, str, datetime, bool]]:
    """Retorna os detalhes completos de uma transcrição de áudio específica pelo ID e chat_id (inclui coluna active)."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, audio_path, transcription, user_prompt, llm_response, created_at, active FROM audio_transcriptions "
                "WHERE id = %s AND chat_id = %s",
                (doc_id, chat_id)
            )
            row = cur.fetchone()
        conn.close()
        return row
    except Exception as e:
        print(f"[ERROR] Erro ao obter transcrição de áudio por ID: {e}", file=sys.stderr)
        return None

def delete_audio_transcription(chat_id: int, doc_id: int) -> bool:
    """Realiza a inativação lógica (soft delete) de um registro de transcrição de áudio."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE audio_transcriptions SET active = FALSE WHERE id = %s AND chat_id = %s",
                (doc_id, chat_id)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao inativar transcrição de áudio #{doc_id}: {e}", file=sys.stderr)
        return False

def restore_audio_transcription(chat_id: int, doc_id: int) -> bool:
    """Restaura (ativa novamente) um registro de transcrição de áudio inativado logicamente."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE audio_transcriptions SET active = TRUE WHERE id = %s AND chat_id = %s",
                (doc_id, chat_id)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao reativar transcrição de áudio #{doc_id}: {e}", file=sys.stderr)
        return False

def get_or_create_user(user_name: str = "default") -> int:
    """Retorna o user_id para o nome de usuário fornecido. Se não existir, cria-o."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            # Verifica se o usuário já existe
            cur.execute("SELECT user_id FROM users WHERE user_name = %s", (user_name,))
            res = cur.fetchone()
            if res:
                user_id = res[0]
            else:
                # Insere o novo usuário
                cur.execute("INSERT INTO users (user_name) VALUES (%s) RETURNING user_id", (user_name,))
                user_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return user_id
    except Exception as e:
        print(f"[ERROR] Erro ao obter/criar usuário '{user_name}': {e}", file=sys.stderr)
        return 1  # ID fallback seguro para evitar quebras

def save_user_profile(category: str, content: str, user_name: str = "default") -> bool:
    """Insere ou atualiza o perfil do usuário para uma determinada categoria e usuário."""
    try:
        user_id = get_or_create_user(user_name)
        cat_clean = clean_string(category).strip().lower()
        content_clean = clean_string(content).strip()
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_profile (user_id, category, content, updated_at) VALUES (%s, %s, %s, CURRENT_TIMESTAMP) "
                "ON CONFLICT (user_id, category) DO UPDATE SET content = EXCLUDED.content, updated_at = CURRENT_TIMESTAMP",
                (user_id, cat_clean, content_clean)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao salvar perfil do usuário ({category} para {user_name}): {e}", file=sys.stderr)
        return False

def get_user_profile(category: Optional[str] = None, user_name: str = "default") -> Any:
    """
    Retorna o perfil do usuário especificado.
    Se category for informado, retorna a string de conteúdo (ou None se não existir).
    Se category for None, retorna um dicionário com todas as categorias e seus respectivos conteúdos.
    """
    try:
        user_id = get_or_create_user(user_name)
        conn = get_connection()
        with conn.cursor() as cur:
            if category:
                cat_clean = clean_string(category).strip().lower()
                cur.execute("SELECT content FROM user_profile WHERE user_id = %s AND category = %s", (user_id, cat_clean))
                res = cur.fetchone()
                conn.close()
                return res[0] if res else None
            else:
                cur.execute("SELECT category, content FROM user_profile WHERE user_id = %s ORDER BY category ASC", (user_id,))
                rows = cur.fetchall()
                conn.close()
                return {row[0]: row[1] for row in rows}
    except Exception as e:
        print(f"[ERROR] Erro ao buscar perfil do usuário para {user_name}: {e}", file=sys.stderr)
        return None if category else {}

def delete_user_profile(category: str, user_name: str = "default") -> bool:
    """Deleta o registro de perfil de uma categoria para o usuário especificado."""
    try:
        user_id = get_or_create_user(user_name)
        cat_clean = clean_string(category).strip().lower()
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_profile WHERE user_id = %s AND category = %s", (user_id, cat_clean))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao deletar perfil do usuário ({category} para {user_name}): {e}", file=sys.stderr)
        return False

def login_user(user_name: str) -> bool:
    """Define o usuário ativo no sistema."""
    try:
        clean_name = clean_string(user_name).strip()
        if not clean_name:
            return False
        get_or_create_user(clean_name)
        set_setting("logged_in_user", clean_name)
        set_setting("login_expires_at", "")
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao definir usuário ativo '{user_name}': {e}", file=sys.stderr)
        return False

def logout_user() -> bool:
    """Redefine o usuário ativo para o padrão ('default')."""
    try:
        set_setting("logged_in_user", "default")
        set_setting("login_expires_at", "")
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao redefinir usuário: {e}", file=sys.stderr)
        return False

def get_logged_in_user() -> str:
    """Retorna o nome do usuário ativo (env DEFAULT_USER, setting ou 'default' como fallback)."""
    try:
        import os
        env_user = os.getenv("DEFAULT_USER")
        if env_user and env_user.strip():
            user = clean_string(env_user).strip()
            get_or_create_user(user)
            return user

        user = get_setting("logged_in_user")
        if user and user.strip():
            clean_u = clean_string(user).strip()
            get_or_create_user(clean_u)
            return clean_u

        get_or_create_user("default")
        return "default"
    except Exception:
        return "default"

def get_active_username() -> str:
    """Retorna o usuário atualmente ativo no sistema."""
    return get_logged_in_user()

# =====================================================================
# GERENCIAMENTO DE AGENTES (AGENT HUB)
# =====================================================================

def get_active_agent_slug() -> str:
    """Retorna o slug do agente atualmente ativo no sistema (padrão: 'geral')."""
    return get_setting("active_agent", "geral")

def set_active_agent_slug(slug: str) -> bool:
    """Define o agente ativo no sistema após verificar sua existência."""
    clean_slug = slug.strip().lower()
    agent_data = get_agent(clean_slug)
    if not agent_data:
        return False
    return set_setting("active_agent", clean_slug)

def get_agent(slug: str) -> Optional[Dict[str, Any]]:
    """Busca um agente pelo seu identificador único (slug)."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, slug, name, icon, description, system_prompt, allowed_tools, is_default, created_at, updated_at
                FROM agents
                WHERE slug = %s
            """, (slug.strip().lower(),))
            row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": row[0],
            "slug": row[1],
            "name": row[2],
            "icon": row[3],
            "description": row[4],
            "system_prompt": row[5],
            "allowed_tools": row[6],
            "is_default": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }
    except Exception as e:
        logging.error("Erro ao buscar agente '%s': %s", slug, e)
        return None

def list_agents() -> List[Dict[str, Any]]:
    """Lista todos os agentes cadastrados, com os padrões e ativos primeiro."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, slug, name, icon, description, system_prompt, allowed_tools, is_default, created_at, updated_at
                FROM agents
                ORDER BY is_default DESC, name ASC
            """)
            rows = cur.fetchall()
        conn.close()
        agents = []
        for row in rows:
            agents.append({
                "id": row[0],
                "slug": row[1],
                "name": row[2],
                "icon": row[3],
                "description": row[4],
                "system_prompt": row[5],
                "allowed_tools": row[6],
                "is_default": row[7],
                "created_at": row[8],
                "updated_at": row[9],
            })
        return agents
    except Exception as e:
        logging.error("Erro ao listar agentes: %s", e)
        return []

def create_or_update_agent(
    slug: str,
    name: str,
    icon: str = "🤖",
    description: str = "",
    system_prompt: str = "",
    allowed_tools: Optional[List[str]] = None,
    is_default: bool = False
) -> bool:
    """Cria ou atualiza as configurações e prompt de um agente."""
    try:
        clean_slug = slug.strip().lower()
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO agents (slug, name, icon, description, system_prompt, allowed_tools, is_default, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (slug) DO UPDATE SET
                    name = EXCLUDED.name,
                    icon = EXCLUDED.icon,
                    description = EXCLUDED.description,
                    system_prompt = EXCLUDED.system_prompt,
                    allowed_tools = EXCLUDED.allowed_tools,
                    updated_at = CURRENT_TIMESTAMP
            """, (clean_slug, name.strip(), icon.strip(), description.strip(), system_prompt.strip(), allowed_tools, is_default))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logging.error("Erro ao criar/atualizar agente '%s': %s", slug, e)
        return False

def delete_agent(slug: str) -> bool:
    """Exclui um agente personalizado (impede a exclusão de agentes padrão)."""
    try:
        clean_slug = slug.strip().lower()
        agent_data = get_agent(clean_slug)
        if not agent_data:
            return False
        if agent_data.get("is_default"):
            return False  # Não permite deletar agentes do sistema
            
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM agents WHERE slug = %s", (clean_slug,))
        conn.commit()
        conn.close()
        
        # Se o agente deletado era o ativo, reseta para 'geral'
        if get_active_agent_slug() == clean_slug:
            set_active_agent_slug("geral")
        return True
    except Exception as e:
        logging.error("Erro ao deletar agente '%s': %s", slug, e)
        return False

def seed_default_agents():
    """Garante a existência dos agentes padrão 'geral' e 'estudo' no banco."""
    # 1. Agente Geral
    if not get_agent("geral"):
        create_or_update_agent(
            slug="geral",
            name="Assistente Geral & Financeiro",
            icon="🤖",
            description="Assistente pessoal multifuncional para gestão financeira, produtividade, notas e comandos de sistema.",
            system_prompt="Você é o 'Meu Agente', um assistente virtual inteligente e proativo que roda no terminal Linux (WSL).\nVocê tem acesso a várias ferramentas para ajudar o usuário com finanças pessoais, produtividade e consultas gerais.",
            allowed_tools=None,
            is_default=True
        )
        
    # 2. Agente de Estudos
    if not get_agent("estudo"):
        estudo_prompt = (
            "Você é o 'Agente de Estudos & Aulas', um especialista pedagógico, acadêmico e didático de alto nível.\n"
            "Sua missão principal é ajudar o usuário a:\n"
            "1. ESTRUTURAÇÃO DE AULAS E PLANOS DE ENSINO:\n"
            "   - Elaborar planos de aula completos e altamente estruturados (Tema, Carga Horária, Público-Alvo, Objetivos de Aprendizagem alinhados à Taxonomia de Bloom, Conteúdo Programático, Metodologias Ativas / Dinâmicas de Grupo, Recursos Didáticos, Atividades Práticas e Avaliações de Fixação).\n"
            "   - Sugerir roteiros detalhados de slides, tópicos de apresentação e estudos de caso práticos (especialmente nas áreas de Engenharia Clínica, Tecnologia, Saúde, Programação e Gestão).\n"
            "2. LEITURA E SÍNTESE ACADÊMICA DE ARTIGOS / LIVROS (PDF):\n"
            "   - Ao receber ou analisar arquivos PDF com a ferramenta `pdf_tool`, extraia os pontos cruciais: Hipótese/Objetivo, Metodologia empregada, Resultados e Descobertas centrais, Limitações e Aplicações Práticas para sala de aula ou prática profissional.\n"
            "3. CONVERSÃO PARA ÁUDIO (TEXT-TO-SPEECH):\n"
            "   - Quando o usuário desejar ouvir resumos, aulas ou artigos, elabore um roteiro narrativo fluido e didático e use a ferramenta `tts_tool` para gerar o arquivo de áudio narrado com voz neural natural.\n"
            "   - Após a execução da ferramenta `tts_tool`, NÃO chame a ferramenta novamente. Apresente o texto ao usuário em formato claro e amigável confirmando a síntese do áudio.\n"
            "4. METODOLOGIA E DIDÁTICA:\n"
            "   - Sempre incentive o aprendizado ativo, raciocínio crítico, conexões interdisciplinares e aplicação prática dos conceitos.\n"
            "   - Utilize formatação Markdown rica, com tabelas, tópicos claros e destaques para facilitar a leitura e memorização."
        )
        create_or_update_agent(
            slug="estudo",
            name="Especialista em Estudos & Aulas",
            icon="🎓",
            description="Especialista pedagógico para estruturação de aulas, leitura analítica de artigos/PDFs, planos didáticos e roteiros de estudo.",
            system_prompt=estudo_prompt,
            allowed_tools=None,
            is_default=True
        )

    # 3. Agente CRM & Vendas (MCP)
    if not get_agent("crm"):
        crm_prompt = (
            "Você é o 'Agente Especialista em CRM & Vendas', focado em gerenciar relacionamentos, clientes, oportunidades, leads, reuniões e tarefas.\n"
            "Sua missão principal é:\n"
            "1. CONEXÃO COM O CRM VIA MCP:\n"
            "   - Utilizar as ferramentas MCP de CRM disponíveis para consultar histórico, cadastrar clientes, atualizar leads, agendar reuniões e gerenciar tarefas.\n"
            "   - Sempre que o usuário pedir para listar, ver ou consultar tarefas ou clientes, você DEVE EXCLUSIVAMENTE emitir o bloco JSON da ferramenta correspondente (ex: 'mcp_crm_list_tasks', 'mcp_crm_list_clients', 'mcp_crm_get_dashboard').\n"
            "   - É TERMINANTEMENTE PROIBIDO inventar tarefas ou clientes fictícios em texto sem consultar o CRM via ferramenta MCP!\n"
            "2. PRECISÃO E REQUISITOS OBRIGATÓRIOS (HUMAN-IN-THE-LOOP):\n"
            "   - Para qualquer operação de criação ou alteração (ex: cadastrar lead, fechar negócio, alterar status ou valores), certifique-se de que possui todos os parâmetros obrigatórios.\n"
            "   - Se faltar qualquer dado essencial (como nome do cliente, e-mail, telefone, ID ou valor da proposta), NUNCA invente nem presuma informações fictícias.\n"
            "   - Pare a execução da ferramenta, formule uma pergunta clara e objetiva ao usuário solicitando o dado faltante e aguarde a resposta.\n"
            "3. COMUNICAÇÃO OBJETIVA E ESTRUTURADA:\n"
            "   - Apresente resumos claros e organizados das consultas do CRM utilizando listas limpas ou tópicos formatados em Markdown."
        )
        create_or_update_agent(
            slug="crm",
            name="Especialista em CRM & Clientes",
            icon="💼",
            description="Agente especialista em gestão de CRM, clientes, leads e pipeline de vendas integrado via ferramentas MCP.",
            system_prompt=crm_prompt,
            allowed_tools=None,
            is_default=True
        )

# =====================================================================
# GERENCIAMENTO DE SERVIDORES MCP (MODEL CONTEXT PROTOCOL)
# =====================================================================

def save_mcp_server(name: str, url: str, api_key: Optional[str] = None, transport: str = "sse", headers: Optional[Dict[str, Any]] = None) -> bool:
    """Insere ou atualiza as configurações de um servidor MCP no banco de dados."""
    try:
        clean_name = clean_string(name).strip().lower()
        clean_u = clean_string(url).strip()
        clean_key = clean_string(api_key).strip() if api_key else None
        clean_trans = clean_string(transport).strip().lower() or "sse"
        headers_json = json.dumps(headers or {})
        
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO mcp_servers (name, url, api_key, transport, headers, is_active, updated_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, TRUE, CURRENT_TIMESTAMP)
                ON CONFLICT (name) DO UPDATE SET
                    url = EXCLUDED.url,
                    api_key = EXCLUDED.api_key,
                    transport = EXCLUDED.transport,
                    headers = EXCLUDED.headers,
                    is_active = TRUE,
                    updated_at = CURRENT_TIMESTAMP
            """, (clean_name, clean_u, clean_key, clean_trans, headers_json))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logging.error("Erro ao salvar servidor MCP '%s': %s", name, e)
        return False

def get_mcp_server(name: str) -> Optional[Dict[str, Any]]:
    """Busca os dados de um servidor MCP pelo nome ou alias flexível."""
    if not name:
        return None
    try:
        clean_name = name.strip().lower()
        conn = get_connection()
        with conn.cursor() as cur:
            # 1. Busca exata por nome
            cur.execute("""
                SELECT id, name, url, api_key, transport, headers, is_active, created_at, updated_at
                FROM mcp_servers
                WHERE LOWER(name) = %s
            """, (clean_name,))
            row = cur.fetchone()
            
            # 2. Busca flexível: se não encontrou, busca por variações (ex: flowcrm, flow_crm, crm)
            if not row:
                simplified = clean_name.replace("_", "").replace("-", "")
                cur.execute("""
                    SELECT id, name, url, api_key, transport, headers, is_active, created_at, updated_at
                    FROM mcp_servers
                    WHERE REPLACE(REPLACE(LOWER(name), '_', ''), '-', '') = %s
                       OR LOWER(url) LIKE %s
                """, (simplified, f"%{clean_name}%"))
                row = cur.fetchone()
                
            # 3. Fallback especial para CRM: se for flowcrm ou crm
            if not row and ("crm" in clean_name or "flow" in clean_name):
                cur.execute("""
                    SELECT id, name, url, api_key, transport, headers, is_active, created_at, updated_at
                    FROM mcp_servers
                    WHERE LOWER(name) LIKE '%crm%' OR LOWER(url) LIKE '%crm%'
                    ORDER BY id ASC LIMIT 1
                """)
                row = cur.fetchone()
                
        conn.close()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "url": row[2],
            "api_key": row[3],
            "transport": row[4],
            "headers": row[5] if isinstance(row[5], dict) else json.loads(row[5] or "{}"),
            "is_active": row[6],
            "created_at": row[7],
            "updated_at": row[8],
        }
    except Exception as e:
        logging.error("Erro ao buscar servidor MCP '%s': %s", name, e)
        return None

def list_mcp_servers(only_active: bool = False) -> List[Dict[str, Any]]:
    """Lista todos os servidores MCP cadastrados."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            query = """
                SELECT id, name, url, api_key, transport, headers, is_active, created_at, updated_at
                FROM mcp_servers
            """
            if only_active:
                query += " WHERE is_active = TRUE"
            query += " ORDER BY name ASC"
            cur.execute(query)
            rows = cur.fetchall()
        conn.close()
        
        servers = []
        for row in rows:
            servers.append({
                "id": row[0],
                "name": row[1],
                "url": row[2],
                "api_key": row[3],
                "transport": row[4],
                "headers": row[5] if isinstance(row[5], dict) else json.loads(row[5] or "{}"),
                "is_active": row[6],
                "created_at": row[7],
                "updated_at": row[8],
            })
        return servers
    except Exception as e:
        logging.error("Erro ao listar servidores MCP: %s", e)
        return []

def delete_mcp_server(name: str) -> bool:
    """Remove um servidor MCP cadastrado."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mcp_servers WHERE LOWER(name) = LOWER(%s)", (name.strip(),))
            deleted = cur.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
    except Exception as e:
        logging.error("Erro ao excluir servidor MCP '%s': %s", name, e)
        return False

def toggle_mcp_server(name: str, is_active: Optional[bool] = None) -> bool:
    """Ativa ou desativa um servidor MCP."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            if is_active is None:
                cur.execute("""
                    UPDATE mcp_servers
                    SET is_active = NOT is_active, updated_at = CURRENT_TIMESTAMP
                    WHERE LOWER(name) = LOWER(%s)
                """, (name.strip(),))
            else:
                cur.execute("""
                    UPDATE mcp_servers
                    SET is_active = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE LOWER(name) = LOWER(%s)
                """, (is_active, name.strip()))
            updated = cur.rowcount > 0
        conn.commit()
        conn.close()
        return updated
    except Exception as e:
        logging.error("Erro ao alternar status do servidor MCP '%s': %s", name, e)
        return False

