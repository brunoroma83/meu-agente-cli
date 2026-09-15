from meu_agente_cli.db import get_connection
import sys
import re
import time
from datetime import datetime, date
from typing import Optional, List, Dict, Any, Tuple
from decimal import Decimal
import yfinance as yf

# Cache em memória para cotações do Yahoo Finance (TTL de 2 minutos)
_QUOTE_CACHE: Dict[str, Dict[str, Any]] = {}
_QUOTE_CACHE_TTL = 120


# =====================================================================
# INICIALIZAÇÃO DE TABELAS
# =====================================================================

def create_table_invest() -> bool:
    """Cria as tabelas de investimentos, movimentação de renda fixa e ações se não existirem."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            # 1. Tabela mestre de títulos/investimentos
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
                );
            """)
            # 2. Tabela movimentação de renda fixa
            cur.execute("""
                CREATE TABLE IF NOT EXISTS movimentacao_renda_fixa (
                    id SERIAL PRIMARY KEY,
                    id_investimento INT REFERENCES investimentos (id),
                    tipo_movimentacao VARCHAR(50) NOT NULL CHECK (tipo_movimentacao IN ('APORTE', 'RESGATE', 'JUROS_RECEBIDOS', 'IMPOSTO')),
                    valor NUMERIC(12, 2) NOT NULL,
                    data_movimentacao DATE NOT NULL
                );
            """)
            # 3. Tabela movimentação de ações
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
            # 4. Views Consolidadas no PostgreSQL
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
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao criar tabelas de investimentos: {e}", file=sys.stderr)
        return False


# =====================================================================
# COTAÇÕES (YFINANCE) COM CACHE
# =====================================================================

def get_stock_market_price(ticker: str, fallback_price: float = 0.0) -> float:
    """Busca a cotação mais recente da ação via Yahoo Finance com cache em memória."""
    t_clean = ticker.strip().upper()
    now = time.time()
    
    if t_clean in _QUOTE_CACHE:
        cached = _QUOTE_CACHE[t_clean]
        if now - cached["timestamp"] < _QUOTE_CACHE_TTL:
            return cached["price"]
            
    # Formata símbolo da B3
    symbol = t_clean if t_clean.endswith(".SA") or "^" in t_clean or "=" in t_clean else f"{t_clean}.SA"
    
    try:
        yf_ticker = yf.Ticker(symbol)
        hist = yf_ticker.history(period="5d")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
            if price > 0:
                _QUOTE_CACHE[t_clean] = {"price": price, "timestamp": now}
                return price
    except Exception as e:
        print(f"[WARN] Falha ao obter cotação para {symbol}: {e}", file=sys.stderr)
        
    return fallback_price


# =====================================================================
# RENDA FIXA: MOVIMENTAÇÕES E CONSOLIDADO
# =====================================================================

def cadastrar_titulo_renda_fixa(
    nome_titulo: str,
    nome_banco: str,
    tipo_investimento: str = "CDB",
    data_inicio: Optional[str] = None,
    valor_inicial: float = 0.0
) -> Optional[int]:
    """Cadastra um novo título de Renda Fixa na tabela mestre investimentos."""
    try:
        conn = get_connection()
        dt_ini = data_inicio if data_inicio and data_inicio.strip() else datetime.now().strftime("%Y-%m-%d")
        v_ini = max(0.0, float(valor_inicial or 0.0))
        
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO investimentos (
                    nome_titulo, nome_banco, tipo_investimento, quantidade,
                    valor_investido, data_inicio, valor_atual, data_ultima_atualizacao,
                    lucro_prejuizo, percentual_lucro_prejuizo, status, active
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', TRUE)
                RETURNING id;
            """, (
                nome_titulo.strip(),
                nome_banco.strip(),
                tipo_investimento.strip().upper(),
                1.0,
                v_ini,
                dt_ini,
                v_ini,
                dt_ini,
                0.0,
                0.0
            ))
            novo_id = cur.fetchone()[0]
            
            # Se houve aporte inicial, registra na tabela de movimentações
            if v_ini > 0:
                cur.execute("""
                    INSERT INTO movimentacao_renda_fixa (
                        id_investimento, tipo_movimentacao, valor, data_movimentacao
                    ) VALUES (%s, 'APORTE', %s, %s);
                """, (novo_id, v_ini, dt_ini))
                
        conn.commit()
        conn.close()
        return novo_id
    except Exception as e:
        print(f"[ERROR] Erro ao cadastrar título de renda fixa {nome_titulo}: {e}", file=sys.stderr)
        return None


def add_movimentacao_renda_fixa(
    id_investimento: int,
    tipo_movimentacao: str,
    valor: float,
    data_movimentacao: Optional[str] = None
) -> Tuple[bool, str]:
    """Registra uma nova movimentação de Renda Fixa (APORTE, RESGATE, JUROS_RECEBIDOS, IMPOSTO)."""
    tipo = tipo_movimentacao.strip().upper()
    tipos_validos = ("APORTE", "RESGATE", "JUROS_RECEBIDOS", "IMPOSTO")
    if tipo not in tipos_validos:
        return False, f"Tipo de movimentação inválido. Escolha entre: {', '.join(tipos_validos)}."
        
    try:
        val = float(valor)
        if val <= 0:
            return False, "O valor da movimentação deve ser maior que zero."
    except (ValueError, TypeError):
        return False, "Valor numérico inválido."
        
    dt = data_movimentacao if data_movimentacao and data_movimentacao.strip() else datetime.now().strftime("%Y-%m-%d")
    
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            # Verifica se o investimento existe
            cur.execute("SELECT id, nome_titulo, valor_atual, valor_investido FROM investimentos WHERE id = %s", (id_investimento,))
            inv = cur.fetchone()
            if not inv:
                conn.close()
                return False, f"Investimento #{id_investimento} não encontrado."
                
            cur.execute("""
                INSERT INTO movimentacao_renda_fixa (
                    id_investimento, tipo_movimentacao, valor, data_movimentacao
                ) VALUES (%s, %s, %s, %s);
            """, (id_investimento, tipo, val, dt))
            
            # Atualiza data_ultima_atualizacao no título mestre
            cur.execute("""
                UPDATE investimentos 
                SET data_ultima_atualizacao = %s 
                WHERE id = %s;
            """, (dt, id_investimento))
            
        conn.commit()
        conn.close()
        return True, f"Movimentação '{tipo}' de R$ {val:.2f} registrada com sucesso no ativo #{id_investimento}!"
    except Exception as e:
        msg = f"Erro ao registrar movimentação de renda fixa: {e}"
        print(f"[ERROR] {msg}", file=sys.stderr)
        return False, msg


def get_movimentacoes_renda_fixa(
    id_investimento: Optional[int] = None,
    tipo: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retorna extrato analítico de movimentações de Renda Fixa."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            sql = """
                SELECT 
                    m.id,
                    m.id_investimento,
                    COALESCE(i.nome_titulo, 'Título Inativo/Excluído') as nome_titulo,
                    COALESCE(i.nome_banco, '-') as nome_banco,
                    COALESCE(i.tipo_investimento, 'RENDA FIXA') as classe,
                    m.tipo_movimentacao,
                    m.valor,
                    m.data_movimentacao
                FROM movimentacao_renda_fixa m
                LEFT JOIN investimentos i ON m.id_investimento = i.id
                WHERE 1=1
            """
            params = []
            if id_investimento:
                sql += " AND m.id_investimento = %s"
                params.append(id_investimento)
            if tipo and tipo not in ("Todas", "Todos", "", None):
                sql += " AND m.tipo_movimentacao = %s"
                params.append(tipo.strip().upper())
            if data_inicio and data_inicio.strip():
                sql += " AND m.data_movimentacao >= %s"
                params.append(data_inicio.strip())
            if data_fim and data_fim.strip():
                sql += " AND m.data_movimentacao <= %s"
                params.append(data_fim.strip())
                
            sql += " ORDER BY m.data_movimentacao DESC, m.id DESC;"
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            
        conn.close()
        resultado = []
        for r in rows:
            resultado.append({
                "id": r[0],
                "id_investimento": r[1],
                "nome_titulo": r[2],
                "nome_banco": r[3],
                "classe": r[4],
                "tipo_movimentacao": r[5],
                "valor": float(r[6]),
                "data_movimentacao": r[7]
            })
        return resultado
    except Exception as e:
        print(f"[ERROR] Erro ao buscar movimentações de renda fixa: {e}", file=sys.stderr)
        return []


def get_consolidado_renda_fixa(active_only: bool = True) -> List[Dict[str, Any]]:
    """Calcula a posição consolidada de cada ativo de Renda Fixa consultando a view vw_consolidado_renda_fixa."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            sql = """
                SELECT 
                    id,
                    nome_titulo,
                    nome_banco,
                    tipo_investimento,
                    data_inicio,
                    total_aportado,
                    total_resgatado,
                    saldo_investido,
                    juros_recebidos,
                    impostos,
                    juros_liquidos,
                    valor_atual,
                    lucro_rendimento,
                    rentabilidade_pct,
                    data_ultima_atualizacao,
                    active
                FROM vw_consolidado_renda_fixa
            """
            if active_only:
                sql += " WHERE active = TRUE"
            sql += " ORDER BY id ASC;"
            cur.execute(sql)
            rows = cur.fetchall()
        conn.close()
        
        consolidado = []
        for r in rows:
            consolidado.append({
                "id": r[0],
                "nome_titulo": r[1],
                "nome_banco": r[2],
                "tipo_investimento": r[3],
                "data_inicio": r[4],
                "total_aportado": float(r[5] or 0.0),
                "total_resgatado": float(r[6] or 0.0),
                "saldo_investido": float(r[7] or 0.0),
                "juros_recebidos": float(r[8] or 0.0),
                "impostos": float(r[9] or 0.0),
                "juros_liquidos": float(r[10] or 0.0),
                "valor_atual": float(r[11] or 0.0),
                "lucro_rendimento": float(r[12] or 0.0),
                "rentabilidade_pct": float(r[13] or 0.0),
                "data_ultima_atualizacao": r[14] or r[4],
                "active": r[15]
            })
        return consolidado
    except Exception as e:
        print(f"[ERROR] Erro ao consolidar renda fixa via view: {e}", file=sys.stderr)
        return []


def update_valor_atual_renda_fixa(id_investimento: int, novo_valor: float) -> Tuple[bool, str]:
    """Atualiza a cotação/valor de mercado atual de um título de renda fixa."""
    try:
        val = float(novo_valor)
        if val < 0:
            return False, "O valor não pode ser negativo."
    except (ValueError, TypeError):
        return False, "Valor inválido."
        
    try:
        conn = get_connection()
        hoje = datetime.now().date()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE investimentos
                SET valor_atual = %s,
                    data_ultima_atualizacao = %s
                WHERE id = %s;
            """, (val, hoje, id_investimento))
        conn.commit()
        conn.close()
        return True, f"Valor atual do título #{id_investimento} atualizado para R$ {val:.2f} com sucesso!"
    except Exception as e:
        msg = f"Erro ao atualizar valor do título: {e}"
        print(f"[ERROR] {msg}", file=sys.stderr)
        return False, msg


# =====================================================================
# AÇÕES: MOVIMENTAÇÕES E CONSOLIDADO (PREÇO MÉDIO E CUSTÓDIA)
# =====================================================================

def add_movimentacao_acao(
    codigo_acao: str,
    operacao: str,
    quantidade: int,
    preco_unitario: float,
    taxas: float = 0.0,
    data_operacao: Optional[str] = None,
    relacao_id: Optional[int] = None
) -> Tuple[bool, str]:
    """Registra uma operação de Ações (COMPRA, VENDA ou DESDOBRAMENTO)."""
    cod = codigo_acao.strip().upper()
    # Remove eventual .SA informado pelo usuário para padronizar no banco
    if cod.endswith(".SA"):
        cod = cod[:-3]
        
    op = operacao.strip().upper()
    if op not in ("COMPRA", "VENDA", "DESDOBRAMENTO"):
        return False, "Operação inválida. Escolha entre: COMPRA, VENDA ou DESDOBRAMENTO."
        
    try:
        qtd = int(quantidade)
        if qtd <= 0:
            return False, "A quantidade deve ser um inteiro maior que zero."
    except (ValueError, TypeError):
        return False, "Quantidade inválida."
        
    try:
        preco = float(preco_unitario)
        if preco <= 0:
            return False, "O preço unitário deve ser maior que zero."
    except (ValueError, TypeError):
        return False, "Preço unitário inválido."
        
    tax = max(0.0, float(taxas or 0.0))
    dt = data_operacao if data_operacao and data_operacao.strip() else datetime.now().strftime("%Y-%m-%d")
    
    # Cálculo do valor financeiro total da operação
    if op == "COMPRA":
        valor_tot = (qtd * preco) + tax
    elif op == "VENDA":
        valor_tot = (qtd * preco) - tax
        if valor_tot <= 0:
            valor_tot = 0.01
    else:  # DESDOBRAMENTO
        valor_tot = (qtd * preco)
        
    # Validação de venda a descoberto (verifica custódia atual via view SQL)
    if op == "VENDA":
        view_acoes = get_view_consolidado_acoes()
        custodia_atual = 0
        for p in view_acoes:
            if p["codigo_acao"] == cod:
                custodia_atual = p["quantidade_custodia"]
                break
        if qtd > custodia_atual:
            return False, f"Saldo em custódia insuficiente para venda. Você possui {custodia_atual} ações de {cod} e tentou vender {qtd}."
            
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO movimentacao_acoes (
                    codigo_acao, preco_unitario, quantidade, taxas, valor_total,
                    data_operacao, operacao, relacao_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (cod, preco, qtd, tax, valor_tot, dt, op, relacao_id))
            novo_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return True, f"Operação #{novo_id} ({op}) de {qtd}x {cod} a R$ {preco:.2f} (Total: R$ {valor_tot:.2f}) registrada com sucesso!"
    except Exception as e:
        msg = f"Erro ao registrar operação de ações: {e}"
        print(f"[ERROR] {msg}", file=sys.stderr)
        return False, msg


def get_movimentacoes_acoes(
    codigo_acao: Optional[str] = None,
    operacao: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retorna extrato cronológico de operações de Ações."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            sql = """
                SELECT 
                    id, codigo_acao, preco_unitario, quantidade, taxas, 
                    valor_total, data_operacao, operacao, relacao_id
                FROM movimentacao_acoes
                WHERE 1=1
            """
            params = []
            if codigo_acao and codigo_acao.strip() not in ("Todas", "Todos", "", None):
                c_clean = codigo_acao.strip().upper()
                if c_clean.endswith(".SA"):
                    c_clean = c_clean[:-3]
                sql += " AND codigo_acao = %s"
                params.append(c_clean)
            if operacao and operacao.strip() not in ("Todas", "Todos", "", None):
                sql += " AND operacao = %s"
                params.append(operacao.strip().upper())
            if data_inicio and data_inicio.strip():
                sql += " AND data_operacao >= %s"
                params.append(data_inicio.strip())
            if data_fim and data_fim.strip():
                sql += " AND data_operacao <= %s"
                params.append(data_fim.strip())
                
            sql += " ORDER BY data_operacao DESC, id DESC;"
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        conn.close()
        
        resultado = []
        for r in rows:
            resultado.append({
                "id": r[0],
                "codigo_acao": r[1],
                "preco_unitario": float(r[2]),
                "quantidade": int(r[3]),
                "taxas": float(r[4]),
                "valor_total": float(r[5]),
                "data_operacao": r[6],
                "operacao": r[7],
                "relacao_id": r[8]
            })
        return resultado
    except Exception as e:
        print(f"[ERROR] Erro ao buscar movimentações de ações: {e}", file=sys.stderr)
        return []


def get_view_consolidado_acoes() -> List[Dict[str, Any]]:
    """Consulta a view consolidada de Ações diretamente do PostgreSQL (vw_consolidado_acoes)."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    codigo_acao,
                    quantidade_custodia,
                    total_comprado,
                    total_vendido,
                    total_taxas,
                    qtd_total_comprada,
                    qtd_total_vendida,
                    preco_medio_estimado,
                    data_ultima_operacao
                FROM vw_consolidado_acoes
                ORDER BY quantidade_custodia DESC, codigo_acao ASC;
            """)
            rows = cur.fetchall()
        conn.close()
        
        resultado = []
        for r in rows:
            resultado.append({
                "codigo_acao": r[0],
                "quantidade_custodia": int(r[1] or 0),
                "total_comprado": float(r[2] or 0.0),
                "total_vendido": float(r[3] or 0.0),
                "total_taxas": float(r[4] or 0.0),
                "qtd_total_comprada": int(r[5] or 0),
                "qtd_total_vendida": int(r[6] or 0),
                "preco_medio_estimado": float(r[7] or 0.0),
                "data_ultima_operacao": r[8]
            })
        return resultado
    except Exception as e:
        print(f"[ERROR] Erro ao consultar vw_consolidado_acoes: {e}", file=sys.stderr)
        return []


def get_consolidado_acoes(fetch_market_prices: bool = True) -> List[Dict[str, Any]]:
    """
    Calcula a posição consolidada em carteira por código de ação.
    Aplica o algoritmo contábil padrão de Preço Médio ponderado com taxas e apuração de lucros realizados,
    integrando com os agregados da view SQL vw_consolidado_acoes e cotações de mercado via Yahoo Finance.
    """
    try:
        # Carrega dados agregados da view SQL
        view_rows = get_view_consolidado_acoes()
        view_map = {v["codigo_acao"]: v for v in view_rows}

        conn = get_connection()
        with conn.cursor() as cur:
            # Carrega todas as movimentações ordenadas cronologicamente
            cur.execute("""
                SELECT 
                    id, codigo_acao, preco_unitario, quantidade, taxas, 
                    valor_total, data_operacao, operacao
                FROM movimentacao_acoes
                ORDER BY data_operacao ASC, id ASC;
            """)
            ops = cur.fetchall()
        conn.close()
        
        # Estrutura de cálculo por ticker
        carteira: Dict[str, Dict[str, Any]] = {}
        
        for op in ops:
            _, ticker, preco, qtd, taxas, val_tot, dt, tipo_op = op
            t = ticker.strip().upper()
            preco_f = float(preco)
            qtd_i = int(qtd)
            taxas_f = float(taxas)
            val_tot_f = float(val_tot)
            
            if t not in carteira:
                carteira[t] = {
                    "codigo_acao": t,
                    "custodia": 0,
                    "custo_total": 0.0,
                    "preco_medio": 0.0,
                    "lucro_realizado": 0.0,
                    "total_comprado_qtd": 0,
                    "total_vendido_qtd": 0,
                    "ultima_cotacao": preco_f
                }
                
            item = carteira[t]
            
            if tipo_op == "COMPRA":
                custo_compra = (qtd_i * preco_f) + taxas_f
                item["custo_total"] += custo_compra
                item["custodia"] += qtd_i
                item["total_comprado_qtd"] += qtd_i
                item["preco_medio"] = item["custo_total"] / item["custodia"] if item["custodia"] > 0 else 0.0
                item["ultima_cotacao"] = preco_f
                
            elif tipo_op == "VENDA":
                # Realização proporcional de custo baseada no Preço Médio vigente
                pm_atual = item["preco_medio"]
                custo_venda = qtd_i * pm_atual
                valor_liquido_venda = (qtd_i * preco_f) - taxas_f
                lucro_op = valor_liquido_venda - custo_venda
                
                item["lucro_realizado"] += lucro_op
                item["custodia"] -= qtd_i
                item["total_vendido_qtd"] += qtd_i
                item["custo_total"] = max(0.0, item["custodia"] * pm_atual)
                if item["custodia"] <= 0:
                    item["preco_medio"] = 0.0
                    item["custo_total"] = 0.0
                item["ultima_cotacao"] = preco_f
                
            elif tipo_op == "DESDOBRAMENTO":
                # Desdobramento ajusta a quantidade mantendo o custo total constante
                item["custodia"] += qtd_i
                if item["custodia"] > 0:
                    item["preco_medio"] = item["custo_total"] / item["custodia"]
                    
        resultado = []
        for t, data in carteira.items():
            qtd_custodia = data["custodia"]
            pm = data["preco_medio"]
            custo = data["custo_total"]
            lucro_real = data["lucro_realizado"]
            v_info = view_map.get(t, {})
            
            # Se ainda possui posição ativa em custódia
            if qtd_custodia > 0:
                if fetch_market_prices:
                    cotacao = get_stock_market_price(t, fallback_price=data["ultima_cotacao"])
                else:
                    cotacao = data["ultima_cotacao"]
                    
                v_mercado = qtd_custodia * cotacao
                lucro_nao_real = v_mercado - custo
                rent_pct = (lucro_nao_real / custo * 100) if custo > 0 else 0.0
            else:
                cotacao = 0.0
                v_mercado = 0.0
                lucro_nao_real = 0.0
                rent_pct = 0.0
                
            resultado.append({
                "codigo_acao": t,
                "quantidade_custodia": qtd_custodia,
                "preco_medio": pm,
                "custo_total": custo,
                "cotacao_atual": cotacao,
                "valor_mercado": v_mercado,
                "lucro_nao_realizado": lucro_nao_real,
                "rentabilidade_pct": rent_pct,
                "lucro_realizado": lucro_real,
                "total_comprado": v_info.get("total_comprado", 0.0),
                "total_vendido": v_info.get("total_vendido", 0.0),
                "total_taxas": v_info.get("total_taxas", 0.0),
                "data_ultima_operacao": v_info.get("data_ultima_operacao")
            })
            
        # Ordena por valor de mercado decrescente
        resultado.sort(key=lambda x: (x["quantidade_custodia"] > 0, x["valor_mercado"]), reverse=True)
        return resultado
    except Exception as e:
        print(f"[ERROR] Erro ao consolidar carteira de ações: {e}", file=sys.stderr)
        return []


# =====================================================================
# CONSOLIDADOR GERAL (CARTEIRA GLOBAL)
# =====================================================================

def get_resumo_patrimonial_geral() -> Dict[str, Any]:
    """Calcula indicadores consolidados de todo o patrimônio investido (Renda Fixa + Ações)."""
    rf_list = get_consolidado_renda_fixa(active_only=True)
    acoes_list = get_consolidado_acoes(fetch_market_prices=True)
    
    # Totais Renda Fixa
    total_inv_rf = sum(item["saldo_investido"] for item in rf_list)
    valor_atual_rf = sum(item["valor_atual"] for item in rf_list)
    lucro_rf = valor_atual_rf - total_inv_rf
    qtd_rf = len(rf_list)
    
    # Totais Ações
    acoes_ativas = [a for a in acoes_list if a["quantidade_custodia"] > 0]
    total_inv_acoes = sum(a["custo_total"] for a in acoes_ativas)
    valor_atual_acoes = sum(a["valor_mercado"] for a in acoes_ativas)
    lucro_nao_real_acoes = valor_atual_acoes - total_inv_acoes
    lucro_real_acoes = sum(a["lucro_realizado"] for a in acoes_list)
    qtd_acoes = len(acoes_ativas)
    
    # Totais Globais
    total_investido_geral = total_inv_rf + total_inv_acoes
    patrimonio_geral = valor_atual_rf + valor_atual_acoes
    lucro_geral = patrimonio_geral - total_investido_geral
    rent_global_pct = (lucro_geral / total_investido_geral * 100) if total_investido_geral > 0 else 0.0
    
    return {
        "total_investido": total_investido_geral,
        "valor_atual": patrimonio_geral,
        "lucro_total": lucro_geral,
        "rentabilidade_pct": rent_global_pct,
        "total_ativos": qtd_rf + qtd_acoes,
        # Detalhes RF
        "total_investido_rf": total_inv_rf,
        "valor_atual_rf": valor_atual_rf,
        "lucro_rf": lucro_rf,
        "qtd_ativos_rf": qtd_rf,
        # Detalhes Ações
        "total_investido_acoes": total_inv_acoes,
        "valor_atual_acoes": valor_atual_acoes,
        "lucro_nao_real_acoes": lucro_nao_real_acoes,
        "lucro_real_acoes": lucro_real_acoes,
        "qtd_ativos_acoes": qtd_acoes
    }


def get_alocacao_macro() -> Dict[str, float]:
    """Retorna a divisão patrimonial entre Renda Fixa e Ações para gráficos."""
    resumo = get_resumo_patrimonial_geral()
    aloc = {}
    if resumo["valor_atual_rf"] > 0:
        aloc["Renda Fixa"] = resumo["valor_atual_rf"]
    if resumo["valor_atual_acoes"] > 0:
        aloc["Ações"] = resumo["valor_atual_acoes"]
    return aloc


def get_alocacao_por_ativo() -> Dict[str, float]:
    """Retorna a divisão patrimonial individual por ativo/ticker para gráficos."""
    rf_list = get_consolidado_renda_fixa(active_only=True)
    acoes_list = get_consolidado_acoes(fetch_market_prices=False)
    
    aloc = {}
    for r in rf_list:
        if r["valor_atual"] > 0:
            aloc[r["nome_titulo"]] = r["valor_atual"]
            
    for a in acoes_list:
        if a["valor_mercado"] > 0:
            aloc[a["codigo_acao"]] = a["valor_mercado"]
            
    return aloc


def get_alocacao_por_instituicao() -> Dict[str, float]:
    """Retorna o patrimônio agrupado por instituição financeira."""
    rf_list = get_consolidado_renda_fixa(active_only=True)
    aloc = {}
    for r in rf_list:
        banco = r["nome_banco"] or "Outro"
        aloc[banco] = aloc.get(banco, 0.0) + r["valor_atual"]
        
    acoes_list = get_consolidado_acoes(fetch_market_prices=False)
    total_acoes = sum(a["valor_mercado"] for a in acoes_list if a["quantidade_custodia"] > 0)
    if total_acoes > 0:
        aloc["Corretora (Ações)"] = aloc.get("Corretora (Ações)", 0.0) + total_acoes
        
    return aloc


# =====================================================================
# FUNÇÕES DE COMPATIBILIDADE RETROATIVA (LEGACY)
# =====================================================================

def get_invest() -> list:
    """Retorna todos os títulos da tabela investimentos (compatibilidade)."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM investimentos ORDER BY id ASC")
            rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"[ERROR] Erro ao buscar investimentos: {e}", file=sys.stderr)
        return []


def set_invest(
    nome_titulo: str,
    nome_banco: str,
    tipo_investimento: str,
    quantidade: float,
    valor_investido: float,
    data_inicio: str,
    valor_atual: Optional[float] = None,
    **kwargs
) -> bool:
    """Insere um novo investimento na carteira mestre (compatibilidade)."""
    t_tipo = tipo_investimento.strip().upper()
    if t_tipo in ("AÇÃO", "AÇÕES", "ACAO", "ACOES"):
        # Tenta extrair ticker ex: PETR4 de 'Ações Petrobras (PETR4)'
        match = re.search(r'\b([A-Z]{4}\d{1,2})\b', nome_titulo.upper())
        ticker = match.group(1) if match else nome_titulo.strip().upper()[:10]
        qtd = max(1, int(quantidade or 1))
        pr_unit = float(valor_investido) / qtd if qtd > 0 else float(valor_investido)
        ok, _ = add_movimentacao_acao(codigo_acao=ticker, operacao="COMPRA", quantidade=qtd, preco_unitario=pr_unit, data_operacao=data_inicio)
        return ok
    else:
        new_id = cadastrar_titulo_renda_fixa(nome_titulo=nome_titulo, nome_banco=nome_banco, tipo_investimento=tipo_investimento, data_inicio=data_inicio, valor_inicial=valor_investido)
        return new_id is not None


def update_investment_valuation(invest_id: int, novo_valor_atual: float) -> bool:
    """Atualiza a cotação/valor atual de um investimento (compatibilidade)."""
    ok, _ = update_valor_atual_renda_fixa(invest_id, novo_valor_atual)
    return ok


def update_invest(id: int, **kwargs) -> bool:
    """Atualiza campos arbitrários de um investimento (compatibilidade retroativa)."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            fields = []
            values = []
            for k, v in kwargs.items():
                fields.append(f"{k} = %s")
                values.append(v)
            if not fields:
                conn.close()
                return True
            values.append(id)
            sql = f"UPDATE investimentos SET {', '.join(fields)} WHERE id = %s"
            cur.execute(sql, tuple(values))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao atualizar investimento #{id}: {e}", file=sys.stderr)
        return False


def get_investment_summary() -> Dict[str, Any]:
    """Retorna sumário consolidado de toda a carteira (compatibilidade)."""
    return get_resumo_patrimonial_geral()


def get_allocation_by_type() -> Dict[str, float]:
    """Retorna alocação por classe de ativos (compatibilidade)."""
    return get_alocacao_macro()


def get_allocation_by_bank() -> Dict[str, float]:
    """Retorna alocação por instituição (compatibilidade)."""
    return get_alocacao_por_instituicao()


def get_distinct_investment_types() -> List[str]:
    """Retorna lista de tipos de títulos ativos cadastrados."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT tipo_investimento FROM investimentos WHERE active = TRUE ORDER BY tipo_investimento ASC")
            rows = cur.fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        print(f"[ERROR] Erro ao buscar tipos de investimentos: {e}", file=sys.stderr)
        return []


def get_distinct_banks() -> List[str]:
    """Retorna lista de bancos/instituições cadastradas."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT nome_banco FROM investimentos WHERE active = TRUE ORDER BY nome_banco ASC")
            rows = cur.fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        print(f"[ERROR] Erro ao buscar bancos de investimentos: {e}", file=sys.stderr)
        return []


def get_investments_filtered(
    tipo: Optional[str] = None,
    banco: Optional[str] = None,
    query: Optional[str] = None,
    active_only: bool = True
) -> list:
    """Retorna lista de investimentos legados filtrada."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            sql = (
                "SELECT id, nome_titulo, nome_banco, tipo_investimento, quantidade, "
                "valor_investido, valor_atual, lucro_prejuizo, percentual_lucro_prejuizo, "
                "data_inicio, data_ultima_atualizacao, status, active "
                "FROM investimentos WHERE 1=1 "
            )
            params = []
            if active_only:
                sql += "AND active = TRUE "
            if tipo and tipo not in ["Todas", "Todos", "", None]:
                sql += "AND UPPER(tipo_investimento) = %s "
                params.append(tipo.strip().upper())
            if banco and banco not in ["Todos", "Todas", "", None]:
                sql += "AND UPPER(nome_banco) = %s "
                params.append(banco.strip().upper())
            if query and query.strip():
                sql += "AND (nome_titulo ILIKE %s OR nome_banco ILIKE %s OR tipo_investimento ILIKE %s) "
                termo = f"%{query.strip()}%"
                params.extend([termo, termo, termo])
            sql += "ORDER BY valor_atual DESC, id ASC"
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"[ERROR] Erro ao filtrar investimentos: {e}", file=sys.stderr)
        return []


# =====================================================================
# MIGRAÇÃO AUTOMÁTICA DE REGISTROS LEGADOS
# =====================================================================

def migrar_investimentos_legados() -> Dict[str, Any]:
    """
    Migra com segurança os 5 registros históricos da tabela investimentos:
    - ID 1 (Tesouro Direto) -> Aporte em movimentacao_renda_fixa
    - IDs 2..5 (Ações PETR4, EMBJ3/EMBR3, ITUB4, BBSE3) -> Compras em movimentacao_acoes
    """
    conn = get_connection()
    resumo_migracao = {"rf_migrados": 0, "acoes_migradas": 0, "mensagens": []}
    
    try:
        with conn.cursor() as cur:
            # 1. Verifica se movimentacao_renda_fixa está vazia
            cur.execute("SELECT COUNT(*) FROM movimentacao_renda_fixa")
            count_rf = cur.fetchone()[0]
            
            # 2. Verifica se movimentacao_acoes está vazia
            cur.execute("SELECT COUNT(*) FROM movimentacao_acoes")
            count_acoes = cur.fetchone()[0]
            
            # Busca dados de investimentos
            cur.execute("""
                SELECT id, nome_titulo, nome_banco, tipo_investimento, quantidade, 
                       valor_investido, data_inicio, valor_atual 
                FROM investimentos 
                WHERE active = TRUE
                ORDER BY id ASC;
            """)
            legados = cur.fetchall()
            
            for leg in legados:
                l_id, nome, banco, tipo, qtd, v_inv, dt_ini, v_at = leg
                t_tipo = (tipo or "").strip().upper()
                dt_str = dt_ini.strftime("%Y-%m-%d") if isinstance(dt_ini, (datetime, date)) else str(dt_ini)
                val_inv_f = float(v_inv or 0.0)
                
                # Caso Ações
                if t_tipo in ("AÇÃO", "AÇÕES", "ACAO", "ACOES"):
                    if count_acoes == 0:
                        # Extrai ticker do nome
                        match = re.search(r'\(([A-Z0-9]{4,6})\)', nome.upper())
                        ticker = match.group(1) if match else "ACAO"
                        
                        # Estimativa de quantidade e preço
                        # Se quantidade for 1.0, calculamos um preço realista ou usamos 100 cotas proporcionais
                        qtd_op = 100
                        preco_unit = val_inv_f / qtd_op if val_inv_f > 0 else 10.0
                        
                        cur.execute("""
                            INSERT INTO movimentacao_acoes (
                                codigo_acao, preco_unitario, quantidade, taxas, 
                                valor_total, data_operacao, operacao
                            ) VALUES (%s, %s, %s, 0.00, %s, %s, 'COMPRA');
                        """, (ticker, preco_unit, qtd_op, val_inv_f, dt_str))
                        resumo_migracao["acoes_migradas"] += 1
                        resumo_migracao["mensagens"].append(f"Ação {ticker} migrada: {qtd_op} ações a R$ {preco_unit:.2f} (Total R$ {val_inv_f:.2f}).")
                        
                # Caso Renda Fixa
                else:
                    if count_rf == 0:
                        cur.execute("""
                            INSERT INTO movimentacao_renda_fixa (
                                id_investimento, tipo_movimentacao, valor, data_movimentacao
                            ) VALUES (%s, 'APORTE', %s, %s);
                        """, (l_id, val_inv_f, dt_str))
                        resumo_migracao["rf_migrados"] += 1
                        resumo_migracao["mensagens"].append(f"Título #{l_id} '{nome}' migrado com aporte inicial de R$ {val_inv_f:.2f}.")
                        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ERROR] Erro na migração de legados: {e}", file=sys.stderr)
        resumo_migracao["erro"] = str(e)
        
    return resumo_migracao