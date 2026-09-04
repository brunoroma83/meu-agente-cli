from meu_agente_cli.db import get_connection
import sys
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from decimal import Decimal

def create_table_invest():
    """Cria a tabela de investimentos se ela não existir."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
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
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao criar tabela de investimentos: {e}", file=sys.stderr)
        return False

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
    """Insere um novo investimento na carteira."""
    try:
        conn = get_connection()
        v_atual = valor_atual if valor_atual is not None else valor_investido
        lucro = float(v_atual) - float(valor_investido)
        pct = (lucro / float(valor_investido) * 100) if float(valor_investido) > 0 else 0.0
        hoje = datetime.now().date()
        
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO investimentos (
                    nome_titulo, nome_banco, tipo_investimento, quantidade,
                    valor_investido, data_inicio, valor_atual, data_ultima_atualizacao,
                    lucro_prejuizo, percentual_lucro_prejuizo, status, active
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', TRUE)
                """,
                (
                    nome_titulo.strip(),
                    nome_banco.strip(),
                    tipo_investimento.strip().upper(),
                    quantidade,
                    valor_investido,
                    data_inicio,
                    v_atual,
                    hoje,
                    lucro,
                    pct
                )
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao cadastrar investimento {nome_titulo}: {e}", file=sys.stderr)
        return False

def get_invest() -> list:
    """Retorna todos os investimentos cadastrados."""
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

def get_investments_filtered(
    tipo: Optional[str] = None,
    banco: Optional[str] = None,
    query: Optional[str] = None,
    active_only: bool = True
) -> list:
    """Retorna lista de investimentos filtrada por tipo, banco e busca textual."""
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

def get_investment_summary() -> Dict[str, Any]:
    """Calcula indicadores consolidados de investimentos ativos."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    COUNT(*),
                    COALESCE(SUM(valor_investido), 0),
                    COALESCE(SUM(COALESCE(valor_atual, valor_investido)), 0)
                FROM investimentos
                WHERE active = TRUE
            """)
            count, total_inv, total_atual = cur.fetchone()
        conn.close()
        
        t_inv = float(total_inv)
        t_atual = float(total_atual)
        lucro = t_atual - t_inv
        pct = (lucro / t_inv * 100) if t_inv > 0 else 0.0
        
        return {
            "total_ativos": int(count),
            "total_investido": t_inv,
            "valor_atual": t_atual,
            "lucro_total": lucro,
            "rentabilidade_pct": pct
        }
    except Exception as e:
        print(f"[ERROR] Erro ao calcular resumo de investimentos: {e}", file=sys.stderr)
        return {
            "total_ativos": 0,
            "total_investido": 0.0,
            "valor_atual": 0.0,
            "lucro_total": 0.0,
            "rentabilidade_pct": 0.0
        }

def get_allocation_by_type() -> Dict[str, float]:
    """Totaliza o valor atual agrupado por tipo de investimento."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    tipo_investimento,
                    SUM(COALESCE(valor_atual, valor_investido))
                FROM investimentos
                WHERE active = TRUE
                GROUP BY tipo_investimento
                ORDER BY SUM(COALESCE(valor_atual, valor_investido)) DESC
            """)
            rows = cur.fetchall()
        conn.close()
        return {r[0]: float(r[1]) for r in rows}
    except Exception as e:
        print(f"[ERROR] Erro ao agrupar investimentos por tipo: {e}", file=sys.stderr)
        return {}

def get_allocation_by_bank() -> Dict[str, float]:
    """Totaliza o valor atual agrupado por banco/instituição."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    nome_banco,
                    SUM(COALESCE(valor_atual, valor_investido))
                FROM investimentos
                WHERE active = TRUE
                GROUP BY nome_banco
                ORDER BY SUM(COALESCE(valor_atual, valor_investido)) DESC
            """)
            rows = cur.fetchall()
        conn.close()
        return {r[0]: float(r[1]) for r in rows}
    except Exception as e:
        print(f"[ERROR] Erro ao agrupar investimentos por banco: {e}", file=sys.stderr)
        return {}

def get_distinct_investment_types() -> List[str]:
    """Retorna lista de tipos de investimentos ativos."""
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

def update_investment_valuation(invest_id: int, novo_valor_atual: float) -> bool:
    """Atualiza a cotação/valor atual de um investimento e recalcula rentabilidade."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT valor_investido FROM investimentos WHERE id = %s", (invest_id,))
            row = cur.fetchone()
            if not row:
                conn.close()
                return False
                
            valor_investido = float(row[0])
            novo_atual = float(novo_valor_atual)
            lucro = novo_atual - valor_investido
            pct = (lucro / valor_investido * 100) if valor_investido > 0 else 0.0
            hoje = datetime.now().date()
            
            cur.execute("""
                UPDATE investimentos
                SET valor_atual = %s,
                    lucro_prejuizo = %s,
                    percentual_lucro_prejuizo = %s,
                    data_ultima_atualizacao = %s
                WHERE id = %s
            """, (novo_atual, lucro, pct, hoje, invest_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao atualizar cotação do investimento #{invest_id}: {e}", file=sys.stderr)
        return False

def delete_investment_logical(invest_id: int) -> bool:
    """Inativa (soft delete) um investimento pelo ID."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE investimentos SET active = FALSE WHERE id = %s", (invest_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao inativar investimento #{invest_id}: {e}", file=sys.stderr)
        return False

def restore_investment_logical(invest_id: int) -> bool:
    """Restaura um investimento inativado pelo ID."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE investimentos SET active = TRUE WHERE id = %s", (invest_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao restaurar investimento #{invest_id}: {e}", file=sys.stderr)
        return False

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