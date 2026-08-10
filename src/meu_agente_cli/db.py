import os
import subprocess
import time
import sys
import getpass
import json
from datetime import datetime
from typing import List, Tuple, Dict, Any, Optional
import psycopg
from psycopg import Connection
from croniter import croniter
from meu_agente_cli.config import load_bootstrap_config, clean_string

DB_NAME = os.environ.get("DB_NAME") or "meu_agente_cli"

def run_wsl_command(cmd_list: list) -> subprocess.CompletedProcess:
    """Executa um comando no WSL."""
    return subprocess.run(cmd_list, capture_output=True, text=True)

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
        # Primeiro atualiza o repositório
        subprocess.run(["sudo", "apt-get", "update"], check=True)
        # Depois instala o postgresql
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
        raise e

def init_database() -> bool:
    """
    Inicializa o banco de dados, cria as tabelas se necessário.
    """
    if not ensure_postgresql_service():
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
            
            # 4. Tabela financial_records
            cur.execute("""
                CREATE TABLE IF NOT EXISTS financial_records (
                    id SERIAL PRIMARY KEY,
                    type VARCHAR(10) NOT NULL,
                    category VARCHAR(50) NOT NULL,
                    amount NUMERIC(12, 2) NOT NULL,
                    description TEXT,
                    date DATE DEFAULT CURRENT_DATE,
                    due_date DATE,
                    active BOOLEAN DEFAULT TRUE
                )
            """)
            
            # 5. Tabela cron_jobs
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
            
            # Migrations para bases de dados existentes
            cur.execute("ALTER TABLE user_notes ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
            cur.execute("ALTER TABLE financial_records ADD COLUMN IF NOT EXISTS due_date DATE")
            cur.execute("ALTER TABLE financial_records ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
            cur.execute("ALTER TABLE cron_jobs ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
            
        conn.commit()
        conn.close()
        print("[SUCCESS] Banco de dados inicializado com sucesso!")
        return True
    except Exception as e:
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

# 4. Registros Financeiros (Financial Records)
def add_financial_record(record_type: str, category: str, amount: float, description: str, due_date: Optional[str] = None) -> bool:
    """Registra uma receita ou despesa com data de vencimento opcional."""
    try:
        cat_clean = clean_string(category)
        desc_clean = clean_string(description)
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO financial_records (type, category, amount, description, due_date) VALUES (%s, %s, %s, %s, %s)",
                (record_type, cat_clean, amount, desc_clean, due_date)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao registrar finanças: {e}", file=sys.stderr)
        return False

def add_financial_records_bulk(items: List[Dict[str, Any]]) -> bool:
    """Registra múltiplas transações financeiras de uma vez no banco com data de vencimento opcional."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            for item in items:
                r_type = item.get("type", "despesa")
                category = clean_string(item.get("category", "Geral"))
                amount = float(item.get("amount", 0.0))
                description = clean_string(item.get("description", ""))
                due_date = item.get("due_date", None)
                
                cur.execute(
                    "INSERT INTO financial_records (type, category, amount, description, due_date) VALUES (%s, %s, %s, %s, %s)",
                    (r_type, category, amount, description, due_date)
                )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao registrar finanças em lote: {e}", file=sys.stderr)
        return False

def get_financial_records(limit: int = 50) -> List[Tuple[int, str, str, float, str, datetime, Optional[datetime]]]:
    """Retorna os registros financeiros recentes que estejam ativos, incluindo data de vencimento."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, type, category, amount, description, date, due_date FROM financial_records WHERE active = TRUE ORDER BY id DESC LIMIT %s",
                (limit,)
            )
            rows = cur.fetchall()
        conn.close()
        # Retorna o valor float (convertido de decimal) e o due_date
        return [(r[0], r[1], r[2], float(r[3]), r[4], r[5], r[6]) for r in rows]
    except Exception as e:
        print(f"[ERROR] Erro ao buscar registros financeiros: {e}", file=sys.stderr)
        return []

def search_financial_records(
    limit: Optional[int] = None, 
    month_year: Optional[str] = None, 
    query: Optional[str] = None
) -> List[Tuple[int, str, str, float, str, datetime, Optional[datetime]]]:
    """
    Busca registros financeiros ativos aplicando filtros opcionais:
    - limit: quantidade máxima de linhas (None para sem limite)
    - month_year: formato 'MM-YYYY', filtra due_date naquele mês/ano
    - query: termo de busca na categoria ou descrição (busca case-insensitive e sotaque-insensitive)
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            sql = "SELECT id, type, category, amount, description, date, due_date FROM financial_records"
            conditions = ["active = TRUE"]
            params = []
            
            if month_year:
                parts = month_year.split('-')
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    month = int(parts[0])
                    year = int(parts[1])
                    conditions.append("EXTRACT(MONTH FROM due_date) = %s AND EXTRACT(YEAR FROM due_date) = %s")
                    params.extend([month, year])
                    
            if query:
                q_clean = f"%{clean_string(query)}%"
                # Usa TRANSLATE e LOWER para busca insensível a acentos e maiúsculas
                translate_sql = (
                    "translate(lower(category), 'áàâãäéèêëíìîïóòôõöúùûüç', 'aaaaaeeeeiiiiooooouuuuc') ILIKE "
                    "translate(lower(%s), 'áàâãäéèêëíìîïóòôõöúùûüç', 'aaaaaeeeeiiiiooooouuuuc') "
                    "OR "
                    "translate(lower(description), 'áàâãäéèêëíìîïóòôõöúùûüç', 'aaaaaeeeeiiiiooooouuuuc') ILIKE "
                    "translate(lower(%s), 'áàâãäéèêëíìîïóòôõöúùûüç', 'aaaaaeeeeiiiiooooouuuuc')"
                )
                conditions.append(f"({translate_sql})")
                params.extend([q_clean, q_clean])
                
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
                
            sql += " ORDER BY id DESC"
            
            if limit is not None:
                sql += " LIMIT %s"
                params.append(limit)
                
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            
        conn.close()
        return [(r[0], r[1], r[2], float(r[3]), r[4], r[5], r[6]) for r in rows]
    except Exception as e:
        print(f"[ERROR] Erro ao buscar registros financeiros filtrados: {e}", file=sys.stderr)
        return []

def get_financial_summary() -> Dict[str, float]:
    """Retorna a soma de receitas, despesas e o saldo atual de registros ativos."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT SUM(amount) FROM financial_records WHERE active = TRUE AND type = 'receita'")
            receitas = cur.fetchone()[0] or 0.0
            
            cur.execute("SELECT SUM(amount) FROM financial_records WHERE active = TRUE AND type = 'despesa'")
            despesas = cur.fetchone()[0] or 0.0
        conn.close()
        return {
            "receitas": float(receitas),
            "despesas": float(despesas),
            "saldo": float(receitas) - float(despesas)
        }
    except Exception as e:
        print(f"[ERROR] Erro ao calcular resumo financeiro: {e}", file=sys.stderr)
        return {"receitas": 0.0, "despesas": 0.0, "saldo": 0.0}

def delete_financial_record(record_id: int) -> bool:
    """Desativa (soft delete) um registro financeiro específico."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE financial_records SET active = FALSE WHERE id = %s", (record_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao desativar registro financeiro: {e}", file=sys.stderr)
        return False

def delete_financial_records_bulk(record_ids: List[int]) -> bool:
    """Desativa (soft delete) múltiplos registros financeiros em lote pelo ID."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE financial_records SET active = FALSE WHERE id = ANY(%s)", (record_ids,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao desativar registros financeiros em lote: {e}", file=sys.stderr)
        return False

def get_deleted_financial_records(limit: Optional[int] = None) -> List[Tuple[int, str, str, float, str, datetime, Optional[datetime]]]:
    """Retorna os registros financeiros que foram inativados (soft deleted)."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            sql = "SELECT id, type, category, amount, description, date, due_date FROM financial_records WHERE active = FALSE ORDER BY id DESC"
            params = []
            if limit is not None:
                sql += " LIMIT %s"
                params.append(limit)
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        conn.close()
        return [(r[0], r[1], r[2], float(r[3]), r[4], r[5], r[6]) for r in rows]
    except Exception as e:
        print(f"[ERROR] Erro ao buscar registros financeiros deletados: {e}", file=sys.stderr)
        return []

def restore_financial_record(record_id: int) -> bool:
    """Restaura (ativa novamente) um registro financeiro inativado."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE financial_records SET active = TRUE WHERE id = %s", (record_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao restaurar registro financeiro: {e}", file=sys.stderr)
        return False

# 5. Agendamentos de Tarefas (Cron Jobs)
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

def generate_sql_dump() -> str:
    """Gera um dump SQL portátil contendo todos os dados e estruturas do banco."""
    conn = get_connection()
    dump = []
    
    # Ordem das tabelas para limpeza e inserção segura
    tables = ["settings", "user_notes", "financial_records", "cron_jobs"]
    
    for t in reversed(tables):
        dump.append(f"DELETE FROM {t};")
        
    with conn.cursor() as cur:
        # Settings
        cur.execute("SELECT key, value FROM settings")
        for key, val in cur.fetchall():
            val_esc = val.replace("'", "''")
            dump.append(f"INSERT INTO settings (key, value) VALUES ('{key}', '{val_esc}');")
            
        # User Notes
        cur.execute("SELECT id, content, created_at, active FROM user_notes")
        for nid, content, created_at, active in cur.fetchall():
            content_esc = content.replace("'", "''")
            dt_str = created_at.strftime("%Y-%m-%d %H:%M:%S")
            dump.append(
                f"INSERT INTO user_notes (id, content, created_at, active) "
                f"VALUES ({nid}, '{content_esc}', '{dt_str}', {active});"
            )
            
        # Financial Records
        cur.execute("SELECT id, type, category, amount, description, date, due_date, active FROM financial_records")
        for rid, rtype, cat, amount, desc, dt, due_date, active in cur.fetchall():
            desc_esc = desc.replace("'", "''") if desc else ""
            cat_esc = cat.replace("'", "''")
            date_str = dt.strftime("%Y-%m-%d")
            due_date_str = f"'{due_date.strftime('%Y-%m-%d')}'" if due_date else "NULL"
            dump.append(
                f"INSERT INTO financial_records (id, type, category, amount, description, date, due_date, active) "
                f"VALUES ({rid}, '{rtype}', '{cat_esc}', {amount}, '{desc_esc}', '{date_str}', {due_date_str}, {active});"
            )
            
        # Cron Jobs
        cur.execute("SELECT id, name, cron_expression, next_run, last_run, task_prompt, status, active FROM cron_jobs")
        for cid, name, cron_expression, next_run, last_run, task_prompt, status, active in cur.fetchall():
            name_esc = name.replace("'", "''")
            expr_esc = cron_expression.replace("'", "''")
            prompt_esc = task_prompt.replace("'", "''")
            next_run_str = next_run.strftime("%Y-%m-%d %H:%M:%S")
            last_run_str = f"'{last_run.strftime('%Y-%m-%d %H:%M:%S')}'" if last_run else "NULL"
            dump.append(
                f"INSERT INTO cron_jobs (id, name, cron_expression, next_run, last_run, task_prompt, status, active) "
                f"VALUES ({cid}, '{name_esc}', '{expr_esc}', '{next_run_str}', {last_run_str}, '{prompt_esc}', '{status}', {active});"
            )
            
        # Sincroniza as sequências de ID seriais para evitar colisões
        for table in ["user_notes", "financial_records", "cron_jobs"]:
            dump.append(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1)) FROM {table};")
            
    conn.close()
    return "\n".join(dump)

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

def get_credit_cards() -> dict:
    """Retorna o dicionário de cartões de crédito configurados a partir de settings."""
    import json
    try:
        val = get_setting("credit_cards_config")
        if val:
            return json.loads(val)
    except Exception:
        pass
    return {"cartoes": {}}

def save_credit_card(name: str, closing_day: int, due_day: int) -> bool:
    """Cadastra ou atualiza um cartão de crédito no settings."""
    import json
    try:
        cards = get_credit_cards()
        cards["cartoes"][name] = {
            "closing_day": closing_day,
            "due_day": due_day
        }
        return set_setting("credit_cards_config", json.dumps(cards, ensure_ascii=False))
    except Exception as e:
        print(f"[ERROR] Erro ao salvar cartão de crédito no banco: {e}", file=sys.stderr)
        return False
