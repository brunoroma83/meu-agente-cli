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
from dotenv import load_dotenv

load_dotenv()

from meu_agente_cli.config import load_bootstrap_config, clean_string
from meu_agente_cli.security import hash_password, verify_password, generate_mcp_token, hash_token

DB_NAME = os.environ.get("FINANCEIRO_DB_NAME") or os.environ.get("DB_NAME") or "financeiro_db"

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

    # Se a porta já estiver aberta e respondendo (ex: Docker), não precisa mexer no WSL
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        db_port = int(os.environ.get("DB_PORT") or cfg.get("db_port", 5432))
        if s.connect_ex((db_host, db_port)) == 0:
            s.close()
            return True
        s.close()
    except Exception:
        pass

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
    db_user = os.environ.get("FINANCEIRO_DB_USER") or os.environ.get("DB_USER") or cfg.get("db_user", "postgres")
    db_pass = os.environ.get("FINANCEIRO_DB_PASSWORD") or os.environ.get("DB_PASSWORD") or cfg.get("db_password", "")
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

    # 1. Tenta conectar diretamente ao banco alvo DB_NAME primeiro
    try:
        conn = get_connection(dbname=DB_NAME)
        connected = True
        conn.close()
    except Exception:
        pass

    if not connected:
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

        # Conecta primeiro ao banco default 'postgres' para verificar/criar o banco
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
            # 6. Tabela Investimentos
            cur.execute("""
                CREATE TABLE IF NOT EXISTS investimentos (
                    id SERIAL PRIMARY KEY,
                    nome_titulo VARCHAR(100) NOT NULL,
                    nome_banco VARCHAR(100) NOT NULL,
                    tipo_investimento VARCHAR(50) NOT NULL,
                    quantidade NUMERIC(12, 2),
                    valor_investido NUMERIC(12, 2) NOT NULL,
                    data_inicio DATE NOT NULL,
                    valor_atual NUMERIC(12, 2),
                    data_ultima_atualizacao DATE,
                    data_venda DATE,
                    lucro_prejuizo NUMERIC(12, 2),
                    percentual_lucro_prejuizo NUMERIC(12, 2),
                    status VARCHAR(20) DEFAULT 'active',
                    active BOOLEAN DEFAULT TRUE
                )
            """)

            # 6.1 Tabela movimentação de Investimentos
            cur.execute("""
                CREATE TABLE IF NOT EXISTS movimentacao_renda_fixa (
                    id SERIAL PRIMARY KEY,
                    id_investimento INT REFERENCES investimentos (id),
                    tipo_movimentacao VARCHAR(50) NOT NULL CHECK (tipo_movimentacao IN ('APORTE', 'RESGATE','JUROS_RECEBIDOS', 'IMPOSTO')),
                    valor NUMERIC(12, 2) NOT NULL,
                    data_movimentacao DATE NOT NULL
                );
            """)
            # 6.2 Tabela movimentação de ações
            cur.execute("""
                CREATE TABLE IF NOT EXISTS movimentacao_acoes (
                    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    codigo_acao VARCHAR(10) NOT NULL,
                    preco_unitario NUMERIC(15, 2) NOT NULL CHECK (preco_unitario > 0),
                    quantidade INTEGER NOT NULL CHECK (quantidade > 0),
                    taxas NUMERIC(15, 2) NOT NULL DEFAULT 0.00 CHECK (taxas >= 0),
                    valor_total NUMERIC(15, 2) NOT NULL CHECK (valor_total > 0),
                    data_operacao DATE NOT NULL DEFAULT CURRENT_DATE,
                    operacao VARCHAR(20) NOT NULL CHECK (operacao IN ('COMPRA', 'VENDA', 'DESDOBRAMENTO')),
                    relacao_id BIGINT REFERENCES movimentacao_acoes (id)
                );
            """)

            # 6.3 Views Consolidadas de Investimentos
            cur.execute("""
                CREATE OR REPLACE VIEW vw_consolidado_renda_fixa AS
                SELECT 
                    i.id,
                    i.nome_titulo,
                    i.nome_banco,
                    i.tipo_investimento,
                    i.data_inicio,
                    COALESCE(SUM(CASE WHEN m.tipo_movimentacao = 'APORTE' THEN m.valor ELSE 0 END), 0) AS total_aportado,
                    COALESCE(SUM(CASE WHEN m.tipo_movimentacao = 'RESGATE' THEN m.valor ELSE 0 END), 0) AS total_resgatado,
                    COALESCE(SUM(CASE WHEN m.tipo_movimentacao = 'APORTE' THEN m.valor 
                                      WHEN m.tipo_movimentacao = 'RESGATE' THEN -m.valor ELSE 0 END), 0) AS saldo_investido,
                    COALESCE(SUM(CASE WHEN m.tipo_movimentacao = 'JUROS_RECEBIDOS' THEN m.valor ELSE 0 END), 0) AS juros_recebidos,
                    COALESCE(SUM(CASE WHEN m.tipo_movimentacao = 'IMPOSTO' THEN m.valor ELSE 0 END), 0) AS impostos,
                    COALESCE(SUM(CASE WHEN m.tipo_movimentacao = 'JUROS_RECEBIDOS' THEN m.valor 
                                      WHEN m.tipo_movimentacao = 'IMPOSTO' THEN -m.valor ELSE 0 END), 0) AS juros_liquidos,
                    COALESCE(NULLIF(i.valor_atual, 0), 
                             COALESCE(SUM(CASE WHEN m.tipo_movimentacao = 'APORTE' THEN m.valor 
                                               WHEN m.tipo_movimentacao = 'RESGATE' THEN -m.valor 
                                               WHEN m.tipo_movimentacao = 'JUROS_RECEBIDOS' THEN m.valor 
                                               WHEN m.tipo_movimentacao = 'IMPOSTO' THEN -m.valor END), 0)) AS valor_atual,
                    (COALESCE(NULLIF(i.valor_atual, 0), 
                             COALESCE(SUM(CASE WHEN m.tipo_movimentacao = 'APORTE' THEN m.valor 
                                               WHEN m.tipo_movimentacao = 'RESGATE' THEN -m.valor 
                                               WHEN m.tipo_movimentacao = 'JUROS_RECEBIDOS' THEN m.valor 
                                               WHEN m.tipo_movimentacao = 'IMPOSTO' THEN -m.valor END), 0)) -
                     COALESCE(SUM(CASE WHEN m.tipo_movimentacao = 'APORTE' THEN m.valor 
                                       WHEN m.tipo_movimentacao = 'RESGATE' THEN -m.valor ELSE 0 END), 0)) AS lucro_rendimento,
                    CASE 
                        WHEN COALESCE(SUM(CASE WHEN m.tipo_movimentacao = 'APORTE' THEN m.valor 
                                               WHEN m.tipo_movimentacao = 'RESGATE' THEN -m.valor ELSE 0 END), 0) > 0 
                        THEN ((COALESCE(NULLIF(i.valor_atual, 0), 
                                        COALESCE(SUM(CASE WHEN m.tipo_movimentacao = 'APORTE' THEN m.valor 
                                                          WHEN m.tipo_movimentacao = 'RESGATE' THEN -m.valor 
                                                          WHEN m.tipo_movimentacao = 'JUROS_RECEBIDOS' THEN m.valor 
                                                          WHEN m.tipo_movimentacao = 'IMPOSTO' THEN -m.valor END), 0)) -
                               COALESCE(SUM(CASE WHEN m.tipo_movimentacao = 'APORTE' THEN m.valor 
                                                 WHEN m.tipo_movimentacao = 'RESGATE' THEN -m.valor ELSE 0 END), 0)) / 
                              COALESCE(SUM(CASE WHEN m.tipo_movimentacao = 'APORTE' THEN m.valor 
                                                WHEN m.tipo_movimentacao = 'RESGATE' THEN -m.valor ELSE 0 END), 1) * 100)
                        ELSE 0.0 
                    END AS rentabilidade_pct,
                    i.data_ultima_atualizacao,
                    i.active
                FROM investimentos i
                LEFT JOIN movimentacao_renda_fixa m ON i.id = m.id_investimento
                WHERE UPPER(i.tipo_investimento) NOT IN ('AÇÃO', 'AÇÕES', 'ACAO', 'ACOES')
                GROUP BY i.id, i.nome_titulo, i.nome_banco, i.tipo_investimento, i.data_inicio, i.valor_atual, i.data_ultima_atualizacao, i.active;
            """)

            cur.execute("""
                CREATE OR REPLACE VIEW vw_consolidado_acoes AS
                SELECT
                    codigo_acao,
                    SUM(CASE 
                        WHEN operacao = 'COMPRA' THEN quantidade 
                        WHEN operacao = 'VENDA' THEN -quantidade 
                        WHEN operacao = 'DESDOBRAMENTO' THEN quantidade
                        ELSE 0 
                    END) AS quantidade_custodia,
                    SUM(CASE WHEN operacao = 'COMPRA' THEN valor_total ELSE 0 END) AS total_comprado,
                    SUM(CASE WHEN operacao = 'VENDA' THEN valor_total ELSE 0 END) AS total_vendido,
                    SUM(taxas) AS total_taxas,
                    SUM(CASE WHEN operacao = 'COMPRA' THEN quantidade ELSE 0 END) AS qtd_total_comprada,
                    SUM(CASE WHEN operacao = 'VENDA' THEN quantidade ELSE 0 END) AS qtd_total_vendida,
                    CASE 
                        WHEN SUM(CASE WHEN operacao = 'COMPRA' THEN quantidade ELSE 0 END) > 0 
                        THEN (SUM(CASE WHEN operacao = 'COMPRA' THEN valor_total ELSE 0 END) / 
                              SUM(CASE WHEN operacao = 'COMPRA' THEN quantidade ELSE 0 END))
                        ELSE 0.0 
                    END AS preco_medio_estimado,
                    MAX(data_operacao) AS data_ultima_operacao
                FROM movimentacao_acoes
                GROUP BY codigo_acao;
            """)
            # 7. Tabela de Transcrições de Áudio
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
            
            # 10. Tabela de Modelos de Contas Recorrentes
            cur.execute("""
                CREATE TABLE IF NOT EXISTS recurring_bills (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    category VARCHAR(50) NOT NULL,
                    default_amount NUMERIC(12, 2) NOT NULL,
                    due_day INT NOT NULL,
                    active BOOLEAN DEFAULT TRUE
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

            # 13. Tabela de Tokens MCP (Para comunicação do Hermes)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mcp_tokens (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    token_hash VARCHAR(64) UNIQUE NOT NULL,
                    raw_token_prefix VARCHAR(20) NOT NULL,
                    created_by VARCHAR(50) DEFAULT 'bruno',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    is_active BOOLEAN DEFAULT TRUE,
                    last_used_at TIMESTAMP
                )
            """)

            # 14. Tabela de Auditoria e Logs de Acesso do MCP
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mcp_access_logs (
                    id SERIAL PRIMARY KEY,
                    token_id INTEGER REFERENCES mcp_tokens(id) ON DELETE SET NULL,
                    token_name VARCHAR(100),
                    client_ip VARCHAR(50),
                    tool_name VARCHAR(100) NOT NULL,
                    request_params TEXT,
                    response_summary TEXT,
                    status VARCHAR(20) DEFAULT 'success',
                    error_message TEXT,
                    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Migrations para bases de dados existentes
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name VARCHAR(100)")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(50) DEFAULT 'user'")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            cur.execute("ALTER TABLE user_notes ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
            cur.execute("ALTER TABLE financial_records ADD COLUMN IF NOT EXISTS due_date DATE")
            cur.execute("ALTER TABLE financial_records ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
            cur.execute("ALTER TABLE financial_records ADD COLUMN IF NOT EXISTS nature VARCHAR(20) DEFAULT 'daily'")
            cur.execute("ALTER TABLE financial_records ADD COLUMN IF NOT EXISTS card_name VARCHAR(100)")
            cur.execute("ALTER TABLE financial_records ADD COLUMN IF NOT EXISTS is_paid BOOLEAN DEFAULT FALSE")
            cur.execute("ALTER TABLE financial_records ADD COLUMN IF NOT EXISTS payment_date DATE")
            cur.execute("ALTER TABLE financial_records ADD COLUMN IF NOT EXISTS user_name VARCHAR(50) DEFAULT 'bruno'")
            cur.execute("ALTER TABLE recurring_bills ADD COLUMN IF NOT EXISTS user_name VARCHAR(50) DEFAULT 'bruno'")
            cur.execute("ALTER TABLE cron_jobs ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
            cur.execute("ALTER TABLE audio_transcriptions ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
            
            # Garante que registros sem usuário fiquem vinculados ao titular 'bruno'
            cur.execute("UPDATE financial_records SET user_name = 'bruno' WHERE user_name IS NULL")
            cur.execute("UPDATE recurring_bills SET user_name = 'bruno' WHERE user_name IS NULL")
            
            # Backfill inteligente de dados existentes (se ainda não categorizados)
            cur.execute("""
                UPDATE financial_records 
                SET nature = 'card_purchase',
                    card_name = CASE 
                        WHEN category ILIKE '%Cartão de Crédito Itaú%' OR description ILIKE '%[Cartão de Crédito Itaú%' THEN 'Cartão Itaú'
                        WHEN category ILIKE '%Cartão de Crédito BB%' OR description ILIKE '%[Cartão de Crédito BB%' THEN 'Cartão BB'
                        WHEN category ILIKE '%Cartão de Crédito Porto%' OR description ILIKE '%[Cartão de Crédito Porto%' THEN 'Cartão Porto'
                        ELSE 'Cartão de Crédito'
                    END
                WHERE (category ILIKE '%Cartão de Crédito%' OR description ILIKE '%[Cartão%')
                  AND (card_name IS NULL OR nature = 'daily');

                UPDATE financial_records
                SET nature = 'card_purchase',
                    card_name = substring(description from '^\\[([A-Za-z0-9\\-_]+)\\s+\\d+/\\d+\\]')
                WHERE description ~ '^\\[([A-Za-z0-9\\-_]+)\\s+\\d+/\\d+\\]'
                  AND (card_name IS NULL OR nature = 'daily');

                UPDATE financial_records
                SET nature = 'monthly'
                WHERE category IN ('Casa', 'Seguro', 'Condominio', 'Condomínio', 'Energia', 'Internet', 'Telefonia', 'Curso', 'Impostos')
                  AND nature = 'daily'
                  AND card_name IS NULL;

                -- 15. Tabela de Cartões de Crédito Cadastrados
                CREATE TABLE IF NOT EXISTS credit_cards (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    bank VARCHAR(100) NOT NULL,
                    due_day INT NOT NULL CHECK (due_day BETWEEN 1 AND 31),
                    closing_day INT CHECK (closing_day BETWEEN 1 AND 31),
                    user_name VARCHAR(50) NOT NULL DEFAULT 'bruno',
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                ALTER TABLE financial_records ADD COLUMN IF NOT EXISTS card_id INTEGER REFERENCES credit_cards(id) ON DELETE RESTRICT;
            """)

            # Seed inicial de cartões conhecidos/legados se a tabela estiver vazia
            cur.execute("SELECT COUNT(*) FROM credit_cards")
            cards_count = cur.fetchone()[0]
            if cards_count == 0:
                cur.execute("""
                    INSERT INTO credit_cards (name, bank, due_day, closing_day, user_name, active) VALUES
                    ('Porto Seguro', 'Porto Bank', 1, 18, 'bruno', TRUE),
                    ('C6', 'C6 Bank', 20, 14, 'bruno', TRUE),
                    ('Itau', 'Itaú', 10, 2, 'bruno', TRUE),
                    ('BB', 'Banco do Brasil', 10, 29, 'bruno', TRUE)
                    ON CONFLICT (name) DO NOTHING;
                """)

            # Backfill para associar card_id em registros históricos existentes
            cur.execute("""
                UPDATE financial_records f
                SET card_id = c.id
                FROM credit_cards c
                WHERE f.card_id IS NULL AND f.card_name IS NOT NULL
                  AND (
                    LOWER(TRIM(f.card_name)) = LOWER(TRIM(c.name))
                    OR (c.name = 'Porto Seguro' AND LOWER(TRIM(f.card_name)) IN ('porto', 'porto seguro', 'porto-seguro', 'cartão porto', 'cartao porto'))
                    OR (c.name = 'BB' AND LOWER(TRIM(f.card_name)) IN ('bb', 'cartão bb', 'cartao bb', 'ourocard'))
                    OR (c.name = 'Itau' AND LOWER(TRIM(f.card_name)) IN ('itau', 'itaú', 'cartão itau', 'cartao itau', 'cartão itaú'))
                    OR (c.name = 'C6' AND LOWER(TRIM(f.card_name)) IN ('c6', 'c6 bank', 'cartão c6', 'cartao c6'))
                  );
            """)
            
        conn.commit()
        conn.close()
        
        # Garante os usuários padrão (Bruno e Fabiana)
        try:
            seed_default_users()
        except Exception as _user_ex:
            logging.warning("Não foi possível semear usuários padrão: %s", _user_ex)

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

# 4. Registros Financeiros (Financial Records)
def add_financial_record(record_type: str, category: str, amount: float, description: str, due_date: Optional[str] = None, user_name: str = "bruno") -> bool:
    """Registra uma receita ou despesa com data de vencimento opcional e vínculo com usuário."""
    try:
        cat_clean = clean_string(category)
        desc_clean = clean_string(description)
        u_name = clean_string(user_name).strip().lower() if user_name else "bruno"
        if not due_date:
            due_date = datetime.now().strftime("%Y-%m-%d")
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO financial_records (type, category, amount, description, due_date, user_name) VALUES (%s, %s, %s, %s, %s, %s)",
                (record_type, cat_clean, amount, desc_clean, due_date, u_name)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao registrar finanças: {e}", file=sys.stderr)
        return False

def add_financial_records_bulk(items: List[Dict[str, Any]], default_user_name: str = "bruno") -> bool:
    """Registra múltiplas transações financeiras de uma vez no banco com data de vencimento opcional e usuário."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            for item in items:
                r_type = item.get("type", "despesa")
                category = clean_string(item.get("category", "Geral"))
                amount = float(item.get("amount", 0.0))
                description = clean_string(item.get("description", ""))
                due_date = item.get("due_date", None)
                u_name = clean_string(item.get("user_name", default_user_name)).strip().lower() or "bruno"
                
                cur.execute(
                    "INSERT INTO financial_records (type, category, amount, description, due_date, user_name) VALUES (%s, %s, %s, %s, %s, %s)",
                    (r_type, category, amount, description, due_date, u_name)
                )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao registrar finanças em lote: {e}", file=sys.stderr)
        return False

def get_financial_records(limit: int = 50, user_name: Optional[str] = None) -> List[Tuple[int, str, str, float, str, datetime, Optional[datetime], str]]:
    """Retorna os registros financeiros recentes que estejam ativos, incluindo data de vencimento e usuário."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            sql = "SELECT id, type, category, amount, description, date, due_date, user_name FROM financial_records WHERE active = TRUE"
            params = []
            if user_name and user_name.strip().lower() not in ("todos", "todos/compartilhado", "familiar", "geral", "all", ""):
                sql += " AND LOWER(user_name) = %s"
                params.append(user_name.strip().lower())
            sql += " ORDER BY id DESC LIMIT %s"
            params.append(limit)
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        conn.close()
        return [(r[0], r[1], r[2], float(r[3]), r[4], r[5], r[6], r[7] or "bruno") for r in rows]
    except Exception as e:
        print(f"[ERROR] Erro ao buscar registros financeiros: {e}", file=sys.stderr)
        return []

def parse_date_str(date_str: Optional[str]) -> Optional[str]:
    """Normaliza strings de datas (YYYY-MM-DD, DD/MM/YYYY, etc.) para o formato ISO YYYY-MM-DD."""
    if not date_str:
        return None
    date_str = str(date_str).strip()
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return date_str

def search_financial_records(
    limit: Optional[int] = None, 
    month_year: Optional[str] = None, 
    query: Optional[str] = None,
    due_date: Optional[str] = None,
    start_due_date: Optional[str] = None,
    end_due_date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    record_type: Optional[str] = None,
    category: Optional[str] = None,
    order_asc: Optional[bool] = None,
    user_name: Optional[str] = None
) -> List[Tuple[int, str, str, float, str, datetime, Optional[datetime], str]]:
    """
    Busca registros financeiros ativos aplicando filtros opcionais por período, termo, tipo e usuário.
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            sql = "SELECT id, type, category, amount, description, date, due_date, user_name FROM financial_records"
            conditions = ["active = TRUE"]
            params = []
            
            # Normalização de datas de vencimento
            start_val = parse_date_str(start_due_date or start_date)
            end_val = parse_date_str(end_due_date or end_date)
            exact_due_val = parse_date_str(due_date)

            if exact_due_val:
                conditions.append("COALESCE(due_date, date) = %s")
                params.append(exact_due_val)
            else:
                if start_val and end_val and start_val == end_val:
                    conditions.append("COALESCE(due_date, date) = %s")
                    params.append(start_val)
                else:
                    if start_val:
                        conditions.append("COALESCE(due_date, date) >= %s")
                        params.append(start_val)
                    if end_val:
                        conditions.append("COALESCE(due_date, date) <= %s")
                        params.append(end_val)
                    
            if month_year:
                parts = month_year.split('-')
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    month = int(parts[0])
                    year = int(parts[1])
                    conditions.append("EXTRACT(MONTH FROM COALESCE(due_date, date)) = %s AND EXTRACT(YEAR FROM COALESCE(due_date, date)) = %s")
                    params.extend([month, year])
                    
            if record_type:
                conditions.append("lower(type) = %s")
                params.append(record_type.strip().lower())

            if user_name and user_name.strip().lower() not in ("todos", "todos/compartilhado", "familiar", "geral", "all", ""):
                conditions.append("LOWER(user_name) = %s")
                params.append(user_name.strip().lower())

            if category:
                cat_clean = f"%{clean_string(category)}%"
                translate_cat_sql = (
                    "translate(lower(category), 'áàâãäéèêëíìîïóòôõöúùûüç', 'aaaaaeeeeiiiiooooouuuuc') ILIKE "
                    "translate(lower(%s), 'áàâãäéèêëíìîïóòôõöúùûüç', 'aaaaaeeeeiiiiooooouuuuc')"
                )
                conditions.append(translate_cat_sql)
                params.append(cat_clean)

            if query:
                q_clean = f"%{clean_string(query)}%"
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
                
            if order_asc is True or (order_asc is None and (exact_due_val or start_val or end_val or month_year)):
                sql += " ORDER BY COALESCE(due_date, date) ASC NULLS LAST, id ASC"
            else:
                sql += " ORDER BY id DESC"
            
            if limit is not None:
                sql += " LIMIT %s"
                params.append(limit)
                
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            
        conn.close()
        return [(r[0], r[1], r[2], float(r[3]), r[4], r[5], r[6], r[7] or "bruno") for r in rows]
    except Exception as e:
        print(f"[ERROR] Erro ao buscar registros financeiros filtrados: {e}", file=sys.stderr)
        return []

def get_financial_summary(month_year: Optional[str] = None, user_name: Optional[str] = None) -> Dict[str, float]:
    """Retorna a soma de receitas, despesas e o saldo atual de registros ativos com filtro opcional por mês e usuário."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            rec_sql = "SELECT SUM(amount) FROM financial_records WHERE active = TRUE AND type = 'receita'"
            desp_sql = "SELECT SUM(amount) FROM financial_records WHERE active = TRUE AND type = 'despesa'"
            params = []
            where_extra = ""
            if month_year and "-" in month_year:
                parts = month_year.split("-")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    where_extra += " AND EXTRACT(MONTH FROM COALESCE(due_date, date)) = %s AND EXTRACT(YEAR FROM COALESCE(due_date, date)) = %s"
                    params.extend([int(parts[0]), int(parts[1])])
            if user_name and user_name.strip().lower() not in ("todos", "todos/compartilhado", "familiar", "geral", "all", ""):
                where_extra += " AND LOWER(user_name) = %s"
                params.append(user_name.strip().lower())
                
            cur.execute(rec_sql + where_extra, tuple(params))
            receitas = cur.fetchone()[0] or 0.0
            
            cur.execute(desp_sql + where_extra, tuple(params))
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

def get_financial_record_by_id(record_id: int) -> Optional[Dict[str, Any]]:
    """Busca um registro financeiro ativo pelo ID."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, type, category, amount, description, date, due_date, nature, card_name, is_paid, active, user_name
                FROM financial_records 
                WHERE id = %s AND active = TRUE
                """,
                (record_id,)
            )
            row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": row[0],
            "type": row[1],
            "category": row[2],
            "amount": float(row[3]),
            "description": row[4],
            "date": row[5],
            "due_date": row[6],
            "nature": row[7],
            "card_name": row[8],
            "is_paid": bool(row[9]),
            "active": bool(row[10]),
            "user_name": row[11] if len(row) > 11 and row[11] else "bruno"
        }
    except Exception as e:
        print(f"[ERROR] Erro ao buscar registro financeiro #{record_id}: {e}", file=sys.stderr)
        return None

def update_financial_record(
    record_id: int,
    description: Optional[str] = None,
    category: Optional[str] = None,
    amount: Optional[float] = None,
    due_date: Optional[str] = None,
    date: Optional[str] = None,
    record_type: Optional[str] = None,
    user_name: Optional[str] = None,
    is_paid: Optional[bool] = None,
    payment_date: Optional[str] = None
) -> bool:
    """Atualiza os dados de um registro financeiro ativo existente."""
    try:
        fields = []
        params = []
        if description is not None:
            fields.append("description = %s")
            params.append(clean_string(description))
        if category is not None:
            fields.append("category = %s")
            params.append(clean_string(category))
        if amount is not None:
            fields.append("amount = %s")
            params.append(float(amount))
        if due_date is not None:
            dt_parsed = parse_date_str(due_date)
            fields.append("due_date = %s")
            params.append(dt_parsed)
        if date is not None:
            dt_date = parse_date_str(date)
            fields.append("date = %s")
            params.append(dt_date)
        if record_type is not None:
            clean_type = record_type.strip().lower()
            if clean_type in ("despesa", "receita"):
                fields.append("type = %s")
                params.append(clean_type)
        if user_name is not None:
            u_clean = user_name.strip().lower()
            if u_clean:
                fields.append("user_name = %s")
                params.append(u_clean)
        if is_paid is not None:
            fields.append("is_paid = %s")
            params.append(bool(is_paid))
            if is_paid and not payment_date:
                fields.append("payment_date = CURRENT_DATE")
            elif not is_paid:
                fields.append("payment_date = NULL")
        if payment_date is not None:
            fields.append("payment_date = %s")
            params.append(parse_date_str(payment_date))
            
        if not fields:
            return False
            
        params.append(int(record_id))
        query = f"UPDATE financial_records SET {', '.join(fields)} WHERE id = %s AND active = TRUE"
        
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(query, tuple(params))
            updated = cur.rowcount > 0
        conn.commit()
        conn.close()
        return updated
    except Exception as e:
        print(f"[ERROR] Erro ao atualizar registro financeiro #{record_id}: {e}", file=sys.stderr)
        return False

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

def get_financial_categories() -> List[str]:
    """Retorna todas as categorias distintas com registros ativos."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT category FROM financial_records WHERE active = TRUE ORDER BY category ASC"
            )
            rows = cur.fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        print(f"[ERROR] Erro ao buscar categorias financeiras: {e}", file=sys.stderr)
        return []

def get_expenses_by_category(month_year: Optional[str] = None, user_name: Optional[str] = None) -> Dict[str, float]:
    """Retorna os totais de despesas ativas agrupados por categoria com filtro opcional de usuário."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            sql = (
                "SELECT category, SUM(amount) FROM financial_records "
                "WHERE active = TRUE AND lower(type) = 'despesa' "
            )
            params = []
            if month_year:
                parts = month_year.split('-')
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    m, y = int(parts[0]), int(parts[1])
                    sql += "AND EXTRACT(MONTH FROM COALESCE(due_date, date)) = %s AND EXTRACT(YEAR FROM COALESCE(due_date, date)) = %s "
                    params.extend([m, y])
            if user_name and user_name.strip().lower() not in ("todos", "todos/compartilhado", "familiar", "geral", "all", ""):
                sql += "AND LOWER(user_name) = %s "
                params.append(user_name.strip().lower())
            sql += "GROUP BY category ORDER BY SUM(amount) DESC"
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        conn.close()
        return {r[0]: float(r[1]) for r in rows}
    except Exception as e:
        print(f"[ERROR] Erro ao agrupar despesas por categoria: {e}", file=sys.stderr)
        return {}

def get_monthly_overview(year: Optional[int] = None, user_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retorna o consolidado mensal de receitas, despesas e saldo do ano com filtro opcional de usuário."""
    if year is None:
        year = datetime.now().year
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            sql = """
                SELECT 
                    EXTRACT(MONTH FROM COALESCE(due_date, date))::INTEGER as mes,
                    SUM(CASE WHEN lower(type) = 'receita' THEN amount ELSE 0 END) as receitas,
                    SUM(CASE WHEN lower(type) = 'despesa' THEN amount ELSE 0 END) as despesas
                FROM financial_records
                WHERE active = TRUE AND EXTRACT(YEAR FROM COALESCE(due_date, date)) = %s
            """
            params = [year]
            if user_name and user_name.strip().lower() not in ("todos", "todos/compartilhado", "familiar", "geral", "all", ""):
                sql += " AND LOWER(user_name) = %s"
                params.append(user_name.strip().lower())
            sql += " GROUP BY EXTRACT(MONTH FROM COALESCE(due_date, date)) ORDER BY mes ASC"
            
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        conn.close()
        
        meses_nomes = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
        dados_por_mes = {r[0]: {"receitas": float(r[1]), "despesas": float(r[2])} for r in rows if r[0] is not None}
        
        resultado = []
        for m in range(1, 13):
            rec = dados_por_mes.get(m, {}).get("receitas", 0.0)
            desp = dados_por_mes.get(m, {}).get("despesas", 0.0)
            resultado.append({
                "mes_num": m,
                "mes_label": f"{meses_nomes[m-1]}/{year}",
                "receitas": rec,
                "despesas": desp,
                "saldo": rec - desp
            })
        return resultado
    except Exception as e:
        print(f"[ERROR] Erro ao buscar resumo anual: {e}", file=sys.stderr)
        return []

# 4.1 Contas Recorrentes, Cartões de Crédito e Orçamento Diário
def add_recurring_bill(name: str, category: str, default_amount: float, due_day: int, user_name: str = "bruno") -> bool:
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO recurring_bills (name, category, default_amount, due_day, user_name) VALUES (%s, %s, %s, %s, %s)",
                (clean_string(name), clean_string(category), float(default_amount), int(due_day), clean_string(user_name).strip().lower() or "bruno")
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao adicionar modelo de conta fixa: {e}", file=sys.stderr)
        return False

def get_recurring_bills() -> List[Dict[str, Any]]:
    """Retorna os modelos cadastrados de contas recorrentes."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, category, default_amount, due_day, active FROM recurring_bills WHERE active = TRUE ORDER BY due_day ASC")
            rows = cur.fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "name": r[1],
                "category": r[2],
                "default_amount": float(r[3]),
                "due_day": r[4],
                "active": r[5]
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[ERROR] Erro ao buscar contas recorrentes: {e}", file=sys.stderr)
        return []

def delete_recurring_bill(bill_id: int) -> bool:
    """Inativa um modelo de conta recorrente."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE recurring_bills SET active = FALSE WHERE id = %s", (bill_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao inativar conta recorrente: {e}", file=sys.stderr)
        return False

def normalize_bill_name(name: str) -> str:
    """Normaliza o nome da conta removendo acentuação e pontuações para deduplicação robusta."""
    import unicodedata, re
    if not name:
        return ""
    s = unicodedata.normalize('NFKD', str(name)).encode('ASCII', 'ignore').decode('ASCII')
    s = re.sub(r'[^a-zA-Z0-9\s]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip().lower()

def get_latest_fixed_bills_catalog() -> List[Dict[str, Any]]:
    """
    Retorna o catálogo consolidado das contas fixas recorrentes,
    unificando os modelos cadastrados em recurring_bills e os últimos lançamentos
    praticados em financial_records (nature = 'monthly').
    """
    catalog = {}
    
    # 1. Varre os lançamentos históricos ordenados pelo vencimento mais recente
    # para capturar os últimos valores reais praticados no mês atual/recente
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT description, category, amount, due_date
                FROM financial_records
                WHERE active = TRUE AND lower(type) = 'despesa' AND nature = 'monthly'
                ORDER BY COALESCE(due_date, date) DESC, id DESC
            """)
            rows = cur.fetchall()
        conn.close()
        
        for desc, cat, amt, due_dt in rows:
            if not desc:
                continue
            name_clean = desc.strip()
            if name_clean.lower().startswith("fatura ") or "[" in name_clean:
                continue
                
            norm_key = normalize_bill_name(name_clean)
            if not norm_key:
                continue
                
            due_day = due_dt.day if due_dt else 10
            
            # Como a ordenação é DESC, a primeira vez que encontramos norm_key é o lançamento MAIS RECENTE!
            if norm_key not in catalog:
                catalog[norm_key] = {
                    "name": name_clean,
                    "category": cat.strip() if cat else "Contas Fixas",
                    "default_amount": float(amt),
                    "due_day": due_day
                }
    except Exception as e:
        print(f"[ERROR] Erro ao buscar histórico para catálogo: {e}", file=sys.stderr)
        
    # 2. Carrega/integra os modelos explícitos de recurring_bills
    for rb in get_recurring_bills():
        norm_key = normalize_bill_name(rb["name"])
        if norm_key not in catalog:
            catalog[norm_key] = {
                "name": rb["name"].strip(),
                "category": rb["category"].strip(),
                "default_amount": float(rb["default_amount"]),
                "due_day": int(rb["due_day"])
            }
        else:
            if rb["name"]:
                catalog[norm_key]["name"] = rb["name"].strip()
                
    return list(catalog.values())

def project_annual_fixed_expenses(year: Optional[int] = None, start_month: int = 1) -> Dict[str, Any]:
    """
    Gera as previsões de saídas de gastos fixos para todos os meses do ano especificado
    utilizando os últimos valores conhecidos de cada conta recorrente.
    Não sobrescreve despesas que já foram cadastradas ou pagas.
    """
    if year is None:
        year = datetime.now().year
        
    catalog = get_latest_fixed_bills_catalog()
    if not catalog:
        return {"gerados": 0, "total_valor": 0.0, "meses_afetados": [], "contas": []}
        
    import calendar
    records_to_insert = []
    generated_count = 0
    total_amount = 0.0
    affected_months = set()
    
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            for m in range(start_month, 13):
                # Busca as contas fixas que já existem neste mês/ano
                cur.execute("""
                    SELECT description
                    FROM financial_records
                    WHERE active = TRUE AND lower(type) = 'despesa' AND nature = 'monthly'
                      AND EXTRACT(MONTH FROM COALESCE(due_date, date)) = %s
                      AND EXTRACT(YEAR FROM COALESCE(due_date, date)) = %s
                """, (m, year))
                existing_norms = {normalize_bill_name(r[0]) for r in cur.fetchall() if r[0]}
                
                max_days = calendar.monthrange(year, m)[1]
                
                for item in catalog:
                    norm_key = normalize_bill_name(item["name"])
                    if norm_key in existing_norms:
                        # Já existe lançamento dessa conta neste mês, não duplica
                        continue
                        
                    due_d = min(item["due_day"], max_days)
                    due_date_str = f"{year:04d}-{m:02d}-{due_d:02d}"
                    reg_date_str = f"{year:04d}-{m:02d}-01"
                    amt = float(item["default_amount"])
                    
                    records_to_insert.append((
                        "despesa",
                        clean_string(item["category"]),
                        amt,
                        clean_string(item["name"]),
                        reg_date_str,
                        due_date_str,
                        "monthly",
                        False, # is_paid
                        True   # active
                    ))
                    generated_count += 1
                    total_amount += amt
                    affected_months.add(m)
                    
            if records_to_insert:
                cur.executemany("""
                    INSERT INTO financial_records 
                    (type, category, amount, description, date, due_date, nature, is_paid, active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, records_to_insert)
                conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ERROR] Erro ao projetar despesas fixas anuais: {e}", file=sys.stderr)
        
    return {
        "gerados": generated_count,
        "total_valor": round(total_amount, 2),
        "meses_afetados": sorted(list(affected_months)),
        "contas": [c["name"] for c in catalog]
    }

def update_monthly_bill(
    record_id: int, 
    new_amount: float, 
    new_due_date: Optional[str] = None, 
    propagate_future: bool = False,
    user_name: Optional[str] = None,
    category: Optional[str] = None,
    description: Optional[str] = None
) -> bool:
    """
    Atualiza o valor e/ou data de vencimento de uma conta mensal.
    Se propagate_future for True, propaga o novo valor para as mesmas contas
    dos meses subsequentes que ainda estejam em aberto (is_paid = FALSE).
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            # 1. Obtém dados do registro original
            cur.execute("""
                SELECT description, category, due_date, nature
                FROM financial_records
                WHERE id = %s AND active = TRUE
            """, (record_id,))
            row = cur.fetchone()
            if not row:
                conn.close()
                return False
                
            desc, cat, due_dt, nature = row
            
            # 2. Atualiza o registro alvo
            sql_update = "UPDATE financial_records SET amount = %s"
            params = [float(new_amount)]
            if new_due_date and new_due_date.strip():
                sql_update += ", due_date = %s"
                params.append(new_due_date.strip())
            if user_name and user_name.strip():
                sql_update += ", user_name = %s"
                params.append(user_name.strip().lower())
            if category and category.strip():
                sql_update += ", category = %s"
                params.append(clean_string(category))
            if description and description.strip():
                sql_update += ", description = %s"
                params.append(clean_string(description))
            sql_update += " WHERE id = %s"
            params.append(record_id)
            cur.execute(sql_update, tuple(params))
            
            # 3. Se propagate_future for True, propaga para meses posteriores do mesmo ano
            if propagate_future and due_dt and desc:
                cur_month = due_dt.month
                cur_year = due_dt.year
                norm_key = normalize_bill_name(desc)
                
                # Busca os IDs dos registros posteriores para atualizar
                cur.execute("""
                    SELECT id, description
                    FROM financial_records
                    WHERE active = TRUE AND lower(type) = 'despesa' AND nature = 'monthly'
                      AND is_paid = FALSE
                      AND EXTRACT(YEAR FROM COALESCE(due_date, date)) = %s
                      AND EXTRACT(MONTH FROM COALESCE(due_date, date)) > %s
                """, (cur_year, cur_month))
                future_rows = cur.fetchall()
                matching_ids = [r[0] for r in future_rows if normalize_bill_name(r[1]) == norm_key]
                
                if matching_ids:
                    cur.execute("""
                        UPDATE financial_records
                        SET amount = %s
                        WHERE id = ANY(%s)
                    """, (float(new_amount), matching_ids))
                    
                # Atualiza também o default_amount no modelo recurring_bills
                cur.execute("""
                    SELECT id, name FROM recurring_bills WHERE active = TRUE
                """)
                rb_rows = cur.fetchall()
                rb_match_ids = [r[0] for r in rb_rows if normalize_bill_name(r[1]) == norm_key]
                if rb_match_ids:
                    cur.execute("""
                        UPDATE recurring_bills
                        SET default_amount = %s
                        WHERE id = ANY(%s)
                    """, (float(new_amount), rb_match_ids))
                    
            conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao atualizar conta mensal: {e}", file=sys.stderr)
        return False

def list_credit_cards(active_only: bool = True, user_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retorna os cartões cadastrados com contagem de compras vinculadas."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            sql = """
                SELECT c.id, c.name, c.bank, c.due_day, c.closing_day, c.user_name, c.active, c.created_at,
                       COUNT(f.id) AS purchases_count
                FROM credit_cards c
                LEFT JOIN financial_records f ON (f.card_id = c.id OR LOWER(TRIM(f.card_name)) = LOWER(TRIM(c.name))) AND f.active = TRUE
                WHERE 1=1
            """
            params = []
            if active_only:
                sql += " AND c.active = TRUE"
            if user_name and user_name.strip():
                sql += " AND LOWER(c.user_name) = %s"
                params.append(user_name.strip().lower())
            sql += " GROUP BY c.id ORDER BY c.name ASC"
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "name": r[1],
                "bank": r[2],
                "due_day": r[3],
                "closing_day": r[4] or r[3],
                "user_name": r[5] or "bruno",
                "active": r[6],
                "created_at": r[7],
                "purchases_count": r[8]
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[ERROR] Erro ao listar cartões de crédito: {e}", file=sys.stderr)
        return []

def get_credit_card_by_id(card_id: int) -> Optional[Dict[str, Any]]:
    """Obtém um cartão de crédito pelo seu ID."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.id, c.name, c.bank, c.due_day, c.closing_day, c.user_name, c.active,
                       COUNT(f.id) AS purchases_count
                FROM credit_cards c
                LEFT JOIN financial_records f ON (f.card_id = c.id OR LOWER(TRIM(f.card_name)) = LOWER(TRIM(c.name))) AND f.active = TRUE
                WHERE c.id = %s
                GROUP BY c.id
            """, (card_id,))
            r = cur.fetchone()
        conn.close()
        if not r:
            return None
        return {
            "id": r[0],
            "name": r[1],
            "bank": r[2],
            "due_day": r[3],
            "closing_day": r[4] or r[3],
            "user_name": r[5] or "bruno",
            "active": r[6],
            "purchases_count": r[7]
        }
    except Exception as e:
        print(f"[ERROR] Erro ao obter cartão de crédito por id: {e}", file=sys.stderr)
        return None

def find_matching_credit_card(card_query: str, user_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Busca inteligente por cartão de crédito cadastrado e ativo.
    1. Match exato por nome (case-insensitive).
    2. Match parcial / substring (ex: 'Porto' -> 'Porto Seguro').
    Prioriza o usuário titular se informado.
    """
    if not card_query or not card_query.strip():
        return None
    q = card_query.strip().lower()
    q_clean = q.replace("cartão", "").replace("cartao", "").replace("de crédito", "").replace("de credito", "").strip()
    
    all_cards = list_credit_cards(active_only=True)
    if not all_cards:
        return None

    u_filter = user_name.strip().lower() if user_name else None
    if u_filter:
        all_cards.sort(key=lambda c: (c["user_name"].lower() != u_filter, c["name"]))

    # 1. Match exato
    for c in all_cards:
        c_name = c["name"].lower()
        if c_name == q or c_name == q_clean:
            return c

    # 2. Substring match
    if q_clean:
        for c in all_cards:
            c_name = c["name"].lower()
            if q_clean in c_name or c_name in q_clean:
                return c

    # 3. Match por banco
    if q_clean:
        for c in all_cards:
            b_name = c["bank"].lower()
            if q_clean in b_name or b_name in q_clean:
                return c

    return None

def create_credit_card(name: str, bank: str, due_day: int, closing_day: Optional[int] = None, user_name: str = "bruno") -> Tuple[bool, str]:
    """Cadastra novo cartão de crédito no banco (exclusivo Web UI)."""
    try:
        n = clean_string(name).strip()
        b = clean_string(bank).strip()
        u = clean_string(user_name).strip().lower() if user_name else "bruno"
        d_due = int(due_day)
        d_close = int(closing_day) if closing_day is not None and int(closing_day) > 0 else (d_due - 7 if d_due > 7 else d_due + 23)
        if not (1 <= d_due <= 31) or not (1 <= d_close <= 31):
            return False, "Dias de vencimento e fechamento devem estar entre 1 e 31."
        if not n:
            return False, "O nome do cartão é obrigatório."
        if not b:
            return False, "O nome do banco/emissor é obrigatório."

        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO credit_cards (name, bank, due_day, closing_day, user_name, active)
                VALUES (%s, %s, %s, %s, %s, TRUE)
            """, (n, b, d_due, d_close, u))
        conn.commit()
        conn.close()
        return True, f"Cartão '{n}' cadastrado com sucesso!"
    except Exception as e:
        msg = f"Erro ao cadastrar cartão: {e}"
        print(f"[ERROR] {msg}", file=sys.stderr)
        return False, msg

def update_credit_card(card_id: int, name: str, bank: str, due_day: int, closing_day: Optional[int] = None, user_name: str = "bruno", active: bool = True) -> Tuple[bool, str]:
    """Atualiza dados de um cartão de crédito cadastrado."""
    try:
        n = clean_string(name).strip()
        b = clean_string(bank).strip()
        u = clean_string(user_name).strip().lower() if user_name else "bruno"
        d_due = int(due_day)
        d_close = int(closing_day) if closing_day is not None and int(closing_day) > 0 else d_due
        if not (1 <= d_due <= 31) or not (1 <= d_close <= 31):
            return False, "Dias de vencimento e fechamento devem estar entre 1 e 31."

        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE credit_cards
                SET name = %s, bank = %s, due_day = %s, closing_day = %s, user_name = %s, active = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (n, b, d_due, d_close, u, active, card_id))
        conn.commit()
        conn.close()
        return True, f"Cartão '{n}' atualizado com sucesso!"
    except Exception as e:
        msg = f"Erro ao atualizar cartão: {e}"
        print(f"[ERROR] {msg}", file=sys.stderr)
        return False, msg

def toggle_credit_card_active(card_id: int) -> Tuple[bool, str]:
    """Alterna o status ativo/desativado do cartão."""
    try:
        card = get_credit_card_by_id(card_id)
        if not card:
            return False, "Cartão não encontrado."
        new_status = not card["active"]
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE credit_cards SET active = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s", (new_status, card_id))
        conn.commit()
        conn.close()
        status_txt = "ativado" if new_status else "desativado"
        return True, f"Cartão '{card['name']}' {status_txt} com sucesso!"
    except Exception as e:
        msg = f"Erro ao alternar status do cartão: {e}"
        print(f"[ERROR] {msg}", file=sys.stderr)
        return False, msg

def delete_credit_card(card_id: int) -> Tuple[bool, str]:
    """
    Exclui fisicamente um cartão apenas se NÃO houver despesas vinculadas a ele.
    Caso contrário, rejeita o DELETE e orienta a desativação.
    """
    try:
        card = get_credit_card_by_id(card_id)
        if not card:
            return False, "Cartão não encontrado."

        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM financial_records WHERE card_id = %s OR LOWER(TRIM(card_name)) = LOWER(TRIM(%s))", (card_id, card["name"]))
            count = cur.fetchone()[0]
            if count > 0:
                conn.close()
                return False, f"Não é possível excluir o cartão '{card['name']}' pois existem {count} despesas vinculadas a ele. O cartão pode apenas ser desativado para preservar o histórico."

            cur.execute("DELETE FROM credit_cards WHERE id = %s", (card_id,))
        conn.commit()
        conn.close()
        return True, f"Cartão '{card['name']}' excluído com sucesso!"
    except Exception as e:
        msg = f"Erro ao excluir cartão: {e}"
        print(f"[ERROR] {msg}", file=sys.stderr)
        return False, msg

def get_distinct_cards() -> List[str]:
    """Retorna a lista de nomes dos cartões cadastrados e ativos."""
    cards = list_credit_cards(active_only=True)
    if cards:
        return [c["name"] for c in cards]
    # Fallback caso ainda não migrado
    return ["Porto Seguro", "C6", "Itau", "BB"]

def get_monthly_bills(month_year: Optional[str] = None, user_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retorna a lista unificada de contas mensais (fixas) e faturas consolidadas de cartão para o mês especificado,
    com suporte a filtro por usuário.
    """
    now = datetime.now()
    if month_year and "-" in month_year:
        parts = month_year.split("-")
        m, y = int(parts[0]), int(parts[1])
    else:
        m, y = now.month, now.year
        
    bills = []
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            # 1. Contas mensais avulsas cadastradas
            sql1 = """
                SELECT id, category, amount, description, due_date, date, is_paid, payment_date, user_name
                FROM financial_records
                WHERE active = TRUE AND lower(type) = 'despesa' AND nature = 'monthly'
                  AND EXTRACT(MONTH FROM COALESCE(due_date, date)) = %s
                  AND EXTRACT(YEAR FROM COALESCE(due_date, date)) = %s
            """
            params1 = [m, y]
            if user_name and user_name.strip().lower() not in ("todos", "todos/compartilhado", "familiar", "geral", "all", ""):
                sql1 += " AND LOWER(user_name) = %s"
                params1.append(user_name.strip().lower())
            sql1 += " ORDER BY due_date ASC, id ASC"
            
            cur.execute(sql1, tuple(params1))
            for r in cur.fetchall():
                bills.append({
                    "id": r[0],
                    "name": r[3] or r[1],
                    "category": r[1],
                    "amount": float(r[2]),
                    "due_date": r[4] or r[5],
                    "is_paid": bool(r[6]),
                    "payment_date": r[7],
                    "is_card_invoice": False,
                    "card_name": None,
                    "user_name": r[8] or "bruno"
                })
                
            # 2. Faturas consolidadas de cartões de crédito
            sql2 = """
                SELECT 
                    card_name,
                    SUM(amount) as total_fatura,
                    MIN(due_date) as data_venc,
                    BOOL_AND(is_paid) as todos_pagos,
                    user_name
                FROM financial_records
                WHERE active = TRUE AND nature = 'card_purchase' AND card_name IS NOT NULL
                  AND EXTRACT(MONTH FROM COALESCE(due_date, date)) = %s
                  AND EXTRACT(YEAR FROM COALESCE(due_date, date)) = %s
            """
            params2 = [m, y]
            if user_name and user_name.strip().lower() not in ("todos", "todos/compartilhado", "familiar", "geral", "all", ""):
                sql2 += " AND LOWER(user_name) = %s"
                params2.append(user_name.strip().lower())
            sql2 += " GROUP BY card_name, user_name ORDER BY card_name ASC"
            
            cur.execute(sql2, tuple(params2))
            for r in cur.fetchall():
                c_name = r[0]
                tot = float(r[1]) if r[1] is not None else 0.0
                venc = r[2]
                all_paid = bool(r[3]) if r[3] is not None else False
                u_owner = r[4] or "bruno"
                bills.append({
                    "id": f"card_{c_name}",
                    "name": f"Fatura {c_name}",
                    "category": "Cartão de Crédito",
                    "amount": tot,
                    "due_date": venc,
                    "is_paid": all_paid,
                    "payment_date": None,
                    "is_card_invoice": True,
                    "card_name": c_name,
                    "user_name": u_owner
                })
        conn.close()
    except Exception as e:
        print(f"[ERROR] Erro ao buscar contas mensais consolidadas: {e}", file=sys.stderr)
        
    return bills

def toggle_bill_paid(record_id: Any, is_paid: bool, month_year: Optional[str] = None) -> bool:
    """Marca uma conta mensal ou fatura de cartão como paga ou pendente."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            rec_str = str(record_id).strip()
            if rec_str.startswith("card_"):
                # Fatura de cartão de crédito: atualiza todos os itens de compra daquele cartão no mês
                card_name = rec_str.replace("card_", "")
                now = datetime.now()
                if month_year and "-" in month_year:
                    parts = month_year.split("-")
                    m, y = int(parts[0]), int(parts[1])
                else:
                    m, y = now.month, now.year
                    
                cur.execute(
                    """
                    UPDATE financial_records
                    SET is_paid = %s,
                        payment_date = CASE WHEN %s THEN CURRENT_DATE ELSE NULL END
                    WHERE active = TRUE AND nature = 'card_purchase' AND card_name = %s
                      AND EXTRACT(MONTH FROM COALESCE(due_date, date)) = %s
                      AND EXTRACT(YEAR FROM COALESCE(due_date, date)) = %s
                    """,
                    (is_paid, is_paid, card_name, m, y)
                )
            else:
                rid = int(rec_str)
                cur.execute(
                    """
                    UPDATE financial_records
                    SET is_paid = %s,
                        payment_date = CASE WHEN %s THEN CURRENT_DATE ELSE NULL END
                    WHERE id = %s
                    """,
                    (is_paid, is_paid, rid)
                )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao alternar status de pagamento: {e}", file=sys.stderr)
def add_card_purchase(
    card_name: str,
    category: str,
    total_amount: float,
    installments: int,
    description: str,
    buy_date_str: Optional[str] = None,
    user_name: str = "bruno"
) -> Tuple[bool, str]:
    """
    Registra uma compra à vista ou parcelada no cartão de crédito, calculando parcelas e faturas futuras.
    Exige validação rigorosa: o cartão deve existir na tabela credit_cards e estar ativo.
    """
    u_owner = clean_string(user_name).strip().lower() if user_name else "bruno"
    matched_card = find_matching_credit_card(card_name, user_name=u_owner)
    
    if not matched_card:
        available = list_credit_cards(active_only=True)
        avail_str = ", ".join([f"{c['name']} ({c['bank']}, {c['user_name'].capitalize()})" for c in available]) if available else "Nenhum cartão cadastrado"
        return False, f"Cartão '{card_name}' não encontrado ou inativo. Cartões cadastrados disponíveis: [{avail_str}]."

    card_id = matched_card["id"]
    matched_name = matched_card["name"]
    closing_day = matched_card.get("closing_day") or 1
    due_day = matched_card.get("due_day") or 10
    card_owner = matched_card.get("user_name") or u_owner
    
    buy_date = datetime.now()
    if buy_date_str and buy_date_str.strip():
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                buy_date = datetime.strptime(buy_date_str.strip(), fmt)
                break
            except ValueError:
                pass
                
    if buy_date.day >= closing_day:
        if buy_date.month == 12:
            closing_month, closing_year = 1, buy_date.year + 1
        else:
            closing_month, closing_year = buy_date.month + 1, buy_date.year
    else:
        closing_month, closing_year = buy_date.month, buy_date.year
        
    if due_day <= closing_day:
        if closing_month == 12:
            first_due_month, first_due_year = 1, closing_year + 1
        else:
            first_due_month, first_due_year = closing_month + 1, closing_year
    else:
        first_due_month, first_due_year = closing_month, closing_year
        
    inst_count = max(1, int(installments))
    base_inst_val = round(total_amount / inst_count, 2)
    diff = round(total_amount - (base_inst_val * inst_count), 2)
    
    records = []
    import calendar
    for i in range(1, inst_count + 1):
        inst_amount = round(base_inst_val + diff, 2) if i == 1 else base_inst_val
        due_month = first_due_month + (i - 1)
        due_year = first_due_year
        while due_month > 12:
            due_month -= 12
            due_year += 1
            
        max_days = calendar.monthrange(due_year, due_month)[1]
        adjusted_due_day = min(due_day, max_days)
        due_date = datetime(due_year, due_month, adjusted_due_day).date()
        inst_desc = f"[{matched_name} {i}/{inst_count}] {description.strip()}"
        
        records.append((
            "despesa",
            clean_string(category),
            inst_amount,
            inst_desc,
            buy_date.date(),
            due_date,
            "card_purchase",
            matched_name,
            card_id,
            card_owner
        ))
        
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO financial_records (type, category, amount, description, date, due_date, nature, card_name, card_id, user_name, is_paid, active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, TRUE)
                """,
                records
            )
        conn.commit()
        conn.close()
        return True, f"Compra de R$ {total_amount:.2f} ({inst_count}x) registrada com sucesso no cartão '{matched_name}' para '{card_owner.capitalize()}'."
    except Exception as e:
        msg = f"Erro ao registrar compra no cartão: {e}"
        print(f"[ERROR] {msg}", file=sys.stderr)
        return False, msg

def get_card_purchases(card_name: Optional[str] = None, month_year: Optional[str] = None, user_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retorna os lançamentos e parcelas individuais de compras no cartão de crédito com filtro opcional por usuário."""
    now = datetime.now()
    if month_year and "-" in month_year:
        parts = month_year.split("-")
        m, y = int(parts[0]), int(parts[1])
    else:
        m, y = now.month, now.year
        
    items = []
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            sql = """
                SELECT id, card_name, category, amount, description, due_date, date, is_paid, user_name
                FROM financial_records
                WHERE active = TRUE AND nature = 'card_purchase'
                  AND EXTRACT(MONTH FROM COALESCE(due_date, date)) = %s
                  AND EXTRACT(YEAR FROM COALESCE(due_date, date)) = %s
            """
            params = [m, y]
            c_clean = card_name.strip() if card_name and isinstance(card_name, str) else ""
            if c_clean and c_clean not in ["Todos", "Todas", "Todos os Cartões", "todos os cartões", "Cartão de Crédito"]:
                sql += " AND UPPER(card_name) = %s"
                params.append(c_clean.upper())
            if user_name and user_name.strip().lower() not in ("todos", "todos/compartilhado", "familiar", "geral", "all", ""):
                sql += " AND LOWER(user_name) = %s"
                params.append(user_name.strip().lower())
                
            sql += " ORDER BY due_date ASC, id ASC"
            cur.execute(sql, tuple(params))
            for r in cur.fetchall():
                items.append({
                    "id": r[0],
                    "card_name": r[1],
                    "category": r[2],
                    "amount": float(r[3]),
                    "description": r[4],
                    "due_date": r[5] or r[6],
                    "buy_date": r[6],
                    "is_paid": bool(r[7]),
                    "user_name": r[8] or "bruno"
                })
        conn.close()
    except Exception as e:
        print(f"[ERROR] Erro ao buscar compras de cartão: {e}", file=sys.stderr)
    return items

def get_monthly_incomes(month_year: Optional[str] = None, category: Optional[str] = None, query: Optional[str] = None, user_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retorna as receitas ativas para o mês especificado com filtro opcional por usuário."""
    now = datetime.now()
    if month_year and "-" in month_year:
        parts = month_year.split("-")
        m, y = int(parts[0]), int(parts[1])
    else:
        m, y = now.month, now.year
        
    incomes = []
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            sql = """
                SELECT id, category, amount, description, COALESCE(due_date, date), date, user_name
                FROM financial_records
                WHERE active = TRUE AND lower(type) = 'receita'
                  AND EXTRACT(MONTH FROM COALESCE(due_date, date)) = %s
                  AND EXTRACT(YEAR FROM COALESCE(due_date, date)) = %s
            """
            params = [m, y]
            if category and category not in ["Todas", "Todos", "", None]:
                sql += " AND lower(category) = %s"
                params.append(category.strip().lower())
            if user_name and user_name.strip().lower() not in ("todos", "todos/compartilhado", "familiar", "geral", "all", ""):
                sql += " AND LOWER(user_name) = %s"
                params.append(user_name.strip().lower())
            if query and query.strip():
                sql += " AND (description ILIKE %s OR category ILIKE %s)"
                termo = f"%{query.strip()}%"
                params.extend([termo, termo])
                
            sql += " ORDER BY COALESCE(due_date, date) ASC, id ASC"
            cur.execute(sql, tuple(params))
            for r in cur.fetchall():
                incomes.append({
                    "id": r[0],
                    "category": r[1],
                    "amount": float(r[2]),
                    "description": r[3] or "",
                    "due_date": r[4],
                    "date": r[5],
                    "user_name": r[6] or "bruno"
                })
        conn.close()
    except Exception as e:
        print(f"[ERROR] Erro ao buscar receitas: {e}", file=sys.stderr)
    return incomes

def get_daily_expenses(month_year: Optional[str] = None, category: Optional[str] = None, query: Optional[str] = None, user_name: Optional[str] = None) -> List[Tuple]:
    """Retorna exclusivamente as despesas rotineiras diárias (nature = 'daily') com filtro opcional por usuário."""
    now = datetime.now()
    if month_year and "-" in month_year:
        parts = month_year.split("-")
        m, y = int(parts[0]), int(parts[1])
    else:
        m, y = now.month, now.year
        
    rows = []
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            sql = """
                SELECT id, type, category, amount, description, date, due_date, user_name
                FROM financial_records
                WHERE active = TRUE AND lower(type) = 'despesa' AND nature = 'daily'
                  AND EXTRACT(MONTH FROM COALESCE(date, due_date)) = %s
                  AND EXTRACT(YEAR FROM COALESCE(date, due_date)) = %s
            """
            params = [m, y]
            if category and category not in ["Todas", "Todos", "", None]:
                sql += " AND lower(category) = %s"
                params.append(category.strip().lower())
            if user_name and user_name.strip().lower() not in ("todos", "todos/compartilhado", "familiar", "geral", "all", ""):
                sql += " AND LOWER(user_name) = %s"
                params.append(user_name.strip().lower())
            if query and query.strip():
                sql += " AND (description ILIKE %s OR category ILIKE %s)"
                termo = f"%{query.strip()}%"
                params.extend([termo, termo])
                
            sql += " ORDER BY COALESCE(date, due_date) DESC, id DESC"
            cur.execute(sql, tuple(params))
            raw = cur.fetchall()
            rows = [(r[0], r[1], r[2], float(r[3]), r[4], r[5], r[6], r[7] or "bruno") for r in raw]
        conn.close()
    except Exception as e:
        print(f"[ERROR] Erro ao buscar despesas diárias: {e}", file=sys.stderr)
    return rows

def get_daily_budget_summary(month_year: Optional[str] = None, user_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Calcula o balanço orçamentário e a disponibilidade de gastos por dia (Teto Diário) com filtro opcional de usuário.
    """
    import calendar
    now = datetime.now()
    if month_year and "-" in month_year:
        parts = month_year.split("-")
        m, y = int(parts[0]), int(parts[1])
    else:
        m, y = now.month, now.year
        
    res = {
        "receitas_mes": 0.0,
        "custos_fixos_mes": 0.0,
        "saldo_livre_mes": 0.0,
        "gastos_diarios_mes": 0.0,
        "saldo_livre_restante": 0.0,
        "gasto_hoje": 0.0,
        "dias_totais_mes": calendar.monthrange(y, m)[1],
        "dias_restantes": 1,
        "teto_diario": 0.0,
        "status_hoje": "ok"
    }
    
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            u_cond = ""
            u_params = []
            if user_name and user_name.strip().lower() not in ("todos", "todos/compartilhado", "familiar", "geral", "all", ""):
                u_cond = " AND LOWER(user_name) = %s"
                u_params = [user_name.strip().lower()]

            # 1. Total de Receitas do Mês
            rec_sql = f"""
                SELECT COALESCE(SUM(amount), 0)
                FROM financial_records
                WHERE active = TRUE AND lower(type) = 'receita'
                  AND EXTRACT(MONTH FROM COALESCE(due_date, date)) = %s
                  AND EXTRACT(YEAR FROM COALESCE(due_date, date)) = %s
                  {u_cond}
            """
            cur.execute(rec_sql, tuple([m, y] + u_params))
            res["receitas_mes"] = float(cur.fetchone()[0])
            
            # 2. Total de Gastos Diários no Mês
            desp_sql = f"""
                SELECT COALESCE(SUM(amount), 0)
                FROM financial_records
                WHERE active = TRUE AND lower(type) = 'despesa' AND nature = 'daily'
                  AND EXTRACT(MONTH FROM COALESCE(date, due_date)) = %s
                  AND EXTRACT(YEAR FROM COALESCE(date, due_date)) = %s
                  {u_cond}
            """
            cur.execute(desp_sql, tuple([m, y] + u_params))
            res["gastos_diarios_mes"] = float(cur.fetchone()[0])
            
            # 3. Gasto Diário Realizado Hoje
            hoje_sql = f"""
                SELECT COALESCE(SUM(amount), 0)
                FROM financial_records
                WHERE active = TRUE AND lower(type) = 'despesa' AND nature = 'daily'
                  AND date = CURRENT_DATE
                  {u_cond}
            """
            cur.execute(hoje_sql, tuple(u_params))
            res["gasto_hoje"] = float(cur.fetchone()[0])
            
        conn.close()
        
        # 4. Total de Contas Mensais Fixas e Faturas
        monthly_bills = get_monthly_bills(f"{m:02d}-{y}", user_name=user_name)
        res["custos_fixos_mes"] = sum(b["amount"] for b in monthly_bills)
        
        # 5. Cálculos Orçamentários
        res["saldo_livre_mes"] = max(0.0, res["receitas_mes"] - res["custos_fixos_mes"])
        res["saldo_livre_restante"] = res["saldo_livre_mes"] - res["gastos_diarios_mes"]
        
        # Dias restantes no mês
        dias_no_mes = res["dias_totais_mes"]
        if y == now.year and m == now.month:
            dias_restantes = max(1, dias_no_mes - now.day + 1)
        elif y < now.year or (y == now.year and m < now.month):
            dias_restantes = 1
        else:
            dias_restantes = dias_no_mes
            
        res["dias_restantes"] = dias_restantes
        res["teto_diario"] = max(0.0, res["saldo_livre_restante"] / dias_restantes) if res["saldo_livre_restante"] > 0 else 0.0
        res["status_hoje"] = "ok" if res["gasto_hoje"] <= (res["teto_diario"] if res["teto_diario"] > 0 else 999999) else "warning"
        
    except Exception as e:
        print(f"[ERROR] Erro ao calcular resumo orçamentário diário: {e}", file=sys.stderr)
        
    return res

# 5. Agendamentos de Tarefas (Cron Jobs)
def add_cron_job(name: str, cron_expression: str, task_prompt: str) -> bool:
    """Adiciona um novo cron job de subagente, calculando o próximo disparo."""
    try:
        name_clean = clean_string(name)
        prompt_clean = clean_string(task_prompt)
        # Valida a expressão cron e calcula o próximo disparo
        base_time = datetime.now()
        next_run = base_time
        
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

def ensure_daily_finance_cron() -> bool:
    """Garante que o cron job diário de alerta de contas às 11:00 AM exista e esteja ativo."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, cron_expression, active FROM cron_jobs WHERE name = 'Alerta Diário de Contas a Vencer'"
            )
            row = cur.fetchone()
            if row:
                job_id, cron_expr, active = row
                if not active or cron_expr != "0 11 * * *":
                    next_run = datetime.now()
                    cur.execute(
                        "UPDATE cron_jobs SET cron_expression = '0 11 * * *', next_run = %s, active = TRUE, status = 'active' WHERE id = %s",
                        (next_run, job_id)
                    )
                    conn.commit()
                conn.close()
                return True
        conn.close()
        return add_cron_job(
            name="Alerta Diário de Contas a Vencer",
            cron_expression="0 11 * * *",
            task_prompt="Verificar contas e despesas com vencimento hoje e nos próximos 2 dias e enviar alerta para o Telegram."
        )
    except Exception as e:
        print(f"[ERROR] Erro ao garantir cron de alerta financeiro: {e}", file=sys.stderr)
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
        "tecnovigilancia",
        "movimentacao_renda_fixa", # FK para investimentos (deve ser apagada antes)
        "movimentacao_acoes",
        "investimentos",
        "audio_transcriptions",
        "cron_jobs",
        "financial_records",
        "user_notes",
        "vinhos",
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
                override = " OVERRIDING SYSTEM VALUE" if table in ["users", "tecnovigilancia", "movimentacao_acoes"] else ""
                
                for row in rows:
                    formatted_vals = [_format_sql_value(v) for v in row]
                    vals_str = ", ".join(formatted_vals)
                    dump.append(f"INSERT INTO {table} ({cols_str}){override} VALUES ({vals_str});")
                    
            # 3. Sincronização de sequences / auto-incrementos
            sequence_tables = [
                ("user_notes", "id"),
                ("financial_records", "id"),
                ("cron_jobs", "id"),
                ("investimentos", "id"),
                ("movimentacao_renda_fixa", "id"),
                ("movimentacao_acoes", "id"),
                ("audio_transcriptions", "id"),
                ("chat_history", "id"),
                ("vinhos", "id"),
                ("users", "user_id"),
                ("tecnovigilancia", "id"),
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

# 7. Transcrições de Áudio (Audio Transcriptions / Reuniões)
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

# =====================================================================
# AUTENTICAÇÃO E GESTÃO DE USUÁRIOS (BRUNO & FABIANA)
# =====================================================================

def seed_default_users():
    """Garante a existência e senha inicial dos usuários padrão (Bruno e Fabiana)."""
    try:
        users = [
            ("bruno", "Bruno", os.environ.get("BRUNO_INITIAL_PASSWORD", "bruno123"), "admin"),
            ("fabiana", "Fabiana", os.environ.get("FABIANA_INITIAL_PASSWORD", "fabiana123"), "user"),
        ]
        conn = get_connection()
        with conn.cursor() as cur:
            for u_name, d_name, pwd, role in users:
                cur.execute("SELECT user_id, password_hash FROM users WHERE LOWER(user_name) = %s", (u_name.lower(),))
                row = cur.fetchone()
                if not row:
                    p_hash = hash_password(pwd)
                    cur.execute(
                        """
                        INSERT INTO users (user_name, display_name, password_hash, role)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (u_name, d_name, p_hash, role)
                    )
                elif not row[1] or os.environ.get("RESET_DEFAULT_PASSWORDS", "").lower() in ("true", "1", "yes"):
                    p_hash = hash_password(pwd)
                    cur.execute(
                        """
                        UPDATE users 
                        SET display_name = %s, password_hash = %s, role = %s
                        WHERE user_id = %s
                        """,
                        (d_name, p_hash, role, row[0])
                    )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error("Erro ao semear usuários padrão: %s", e)

def authenticate_user(user_name: str, plain_password: str) -> Optional[Dict[str, Any]]:
    """Autentica o usuário pelo nome e senha, retornando seus dados em caso de sucesso."""
    if not user_name or not plain_password:
        return None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, user_name, display_name, password_hash, role
                FROM users
                WHERE LOWER(user_name) = %s
                """,
                (user_name.strip().lower(),)
            )
            row = cur.fetchone()
        conn.close()
        if not row:
            return None
        u_id, u_name, d_name, p_hash, role = row
        if not p_hash or not verify_password(plain_password, p_hash):
            return None
        # Se for hash legado de 64 caracteres, atualiza automaticamente para PBKDF2
        if len(p_hash) == 64:
            try:
                update_user_password(u_name, plain_password)
            except Exception:
                pass
        return {
            "user_id": u_id,
            "user_name": u_name,
            "display_name": d_name or u_name.capitalize(),
            "role": role or "user"
        }
    except Exception as e:
        logging.error("Erro na autenticação do usuário '%s': %s", user_name, e)
        return None

def get_user_by_name(user_name: str) -> Optional[Dict[str, Any]]:
    """Busca dados de um usuário pelo nome."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, user_name, display_name, role, created_at
                FROM users
                WHERE LOWER(user_name) = %s
                """,
                (user_name.strip().lower(),)
            )
            row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "user_id": row[0],
            "user_name": row[1],
            "display_name": row[2] or row[1].capitalize(),
            "role": row[3] or "user",
            "created_at": row[4]
        }
    except Exception as e:
        logging.error("Erro ao buscar usuário '%s': %s", user_name, e)
        return None

def list_users() -> List[Dict[str, Any]]:
    """Lista todos os usuários cadastrados."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, user_name, display_name, role, created_at FROM users ORDER BY user_id ASC")
            rows = cur.fetchall()
        conn.close()
        return [{
            "user_id": r[0],
            "user_name": r[1],
            "display_name": r[2] or r[1].capitalize(),
            "role": r[3] or "user",
            "created_at": r[4]
        } for r in rows]
    except Exception as e:
        logging.error("Erro ao listar usuários: %s", e)
        return []

def update_user_password(user_name: str, new_password: str) -> bool:
    """Atualiza a senha de um usuário."""
    try:
        p_hash = hash_password(new_password)
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET password_hash = %s WHERE LOWER(user_name) = %s", (p_hash, user_name.strip().lower()))
            ok = cur.rowcount > 0
        conn.commit()
        conn.close()
        return ok
    except Exception as e:
        logging.error("Erro ao atualizar senha de '%s': %s", user_name, e)
        return False

# =====================================================================
# GESTÃO DE TOKENS MCP & AUDITORIA DE ACESSOS (HERMES)
# =====================================================================

def create_mcp_token_record(name: str, created_by: str = "bruno") -> Tuple[bool, str, str]:
    """
    Gera um novo token MCP e registra o hash no banco.
    Retorna (sucesso, raw_token, mensagem).
    """
    try:
        name_clean = clean_string(name).strip()
        if not name_clean:
            return False, "", "O apelido do token não pode ser vazio."
            
        raw_token, token_hash, prefix = generate_mcp_token()
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mcp_tokens (name, token_hash, raw_token_prefix, created_by, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
                RETURNING id
                """,
                (name_clean, token_hash, prefix, clean_string(created_by).strip().lower() or "bruno")
            )
            token_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return True, raw_token, f"Token #{token_id} criado com sucesso!"
    except Exception as e:
        logging.error("Erro ao criar token MCP: %s", e)
        return False, "", f"Erro ao criar token: {e}"

def list_mcp_tokens() -> List[Dict[str, Any]]:
    """Lista todos os tokens MCP registrados para visualização no painel."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, raw_token_prefix, created_by, created_at, expires_at, is_active, last_used_at
                FROM mcp_tokens
                ORDER BY id DESC
                """
            )
            rows = cur.fetchall()
        conn.close()
        return [{
            "id": r[0],
            "name": r[1],
            "prefix": r[2],
            "created_by": r[3],
            "created_at": r[4],
            "expires_at": r[5],
            "is_active": bool(r[6]),
            "last_used_at": r[7]
        } for r in rows]
    except Exception as e:
        logging.error("Erro ao listar tokens MCP: %s", e)
        return []

def revoke_mcp_token(token_id: int) -> bool:
    """Revoga/desativa um token MCP."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE mcp_tokens SET is_active = FALSE WHERE id = %s", (int(token_id),))
            ok = cur.rowcount > 0
        conn.commit()
        conn.close()
        return ok
    except Exception as e:
        logging.error("Erro ao revogar token MCP #{token_id}: %s", e)
        return False

def delete_mcp_token(token_id: int) -> bool:
    """Exclui permanentemente um token MCP."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mcp_tokens WHERE id = %s", (int(token_id),))
            ok = cur.rowcount > 0
        conn.commit()
        conn.close()
        return ok
    except Exception as e:
        logging.error("Erro ao excluir token MCP #{token_id}: %s", e)
        return False

def validate_mcp_token(raw_token: str) -> Optional[Dict[str, Any]]:
    """
    Valida um token fornecido pelo cliente MCP (Hermes).
    Se válido, atualiza last_used_at e retorna o registro do token.
    """
    if not raw_token or not raw_token.strip():
        return None
    token_str = raw_token.strip()
    if token_str.startswith("Bearer "):
        token_str = token_str[7:].strip()
        
    t_hash = hash_token(token_str)
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, created_by, is_active, expires_at
                FROM mcp_tokens
                WHERE token_hash = %s
                """,
                (t_hash,)
            )
            row = cur.fetchone()
            if not row:
                conn.close()
                return None
            t_id, name, created_by, is_active, expires_at = row
            if not is_active:
                conn.close()
                return None
            if expires_at and expires_at < datetime.now():
                conn.close()
                return None
            cur.execute("UPDATE mcp_tokens SET last_used_at = CURRENT_TIMESTAMP WHERE id = %s", (t_id,))
        conn.commit()
        conn.close()
        return {
            "id": t_id,
            "name": name,
            "created_by": created_by
        }
    except Exception as e:
        logging.error("Erro ao validar token MCP: %s", e)
        return None

def log_mcp_access(
    token_id: Optional[int],
    token_name: Optional[str],
    client_ip: str,
    tool_name: str,
    request_params: Any,
    response_summary: Any,
    status: str = "success",
    error_message: Optional[str] = None
) -> bool:
    """Registra histórico de execuções de ferramentas no MCP para auditoria."""
    try:
        req_str = json.dumps(request_params, default=str, ensure_ascii=False) if isinstance(request_params, (dict, list)) else str(request_params)
        res_str = json.dumps(response_summary, default=str, ensure_ascii=False) if isinstance(response_summary, (dict, list)) else str(response_summary)
        
        if len(req_str) > 4000:
            req_str = req_str[:4000] + "... [truncado]"
        if len(res_str) > 4000:
            res_str = res_str[:4000] + "... [truncado]"
            
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mcp_access_logs (token_id, token_name, client_ip, tool_name, request_params, response_summary, status, error_message)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (token_id, token_name, client_ip, tool_name, req_str, res_str, status, error_message)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logging.error("Erro ao salvar log de acesso MCP: %s", e)
        return False

def get_mcp_access_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Retorna os logs de auditoria mais recentes de ferramentas executadas pelo MCP."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, token_id, token_name, client_ip, tool_name, request_params, response_summary, status, error_message, executed_at
                FROM mcp_access_logs
                ORDER BY id DESC
                LIMIT %s
                """,
                (int(limit),)
            )
            rows = cur.fetchall()
        conn.close()
        return [{
            "id": r[0],
            "token_id": r[1],
            "token_name": r[2] or "Sem Token",
            "client_ip": r[3] or "",
            "tool_name": r[4],
            "request_params": r[5],
            "response_summary": r[6],
            "status": r[7],
            "error_message": r[8],
            "executed_at": r[9]
        } for r in rows]
    except Exception as e:
        logging.error("Erro ao listar logs do MCP: %s", e)
        return []


