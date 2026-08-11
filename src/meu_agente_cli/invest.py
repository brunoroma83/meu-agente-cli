from meu_agente_cli.db import get_connection
import sys

def set_invest(nome_titulo: str, nome_banco: str, tipo_investimento: str, quantidade: float, valor_investido: float, data_inicio: str, **kwargs) -> bool:
    """Insere ou atualiza um investimento."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO investimentos (nome_titulo, nome_banco, tipo_investimento, quantidade, valor_investido, data_inicio, valor_atual) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (nome_titulo, nome_banco, tipo_investimento, quantidade, valor_investido, data_inicio, valor_investido)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao salvar configuração {nome_titulo}: {e}", file=sys.stderr)
        return False

def create_table_invest():
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

def get_invest() -> list:
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM investimentos")
            return cur.fetchall()
    except Exception as e:
        print(f"[ERROR] Erro ao buscar investimentos: {e}", file=sys.stderr)
        return []

def update_invest(id: int, **kwargs) -> bool:
    """Atualiza um investimento."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            comando = "UPDATE investimentos SET "
            if 'valor_atual' in kwargs:
                comando += f"valor_atual = '{str(kwargs['valor_atual'])}', "
            if 'data_ultima_atualizacao' in kwargs:
                comando += f"data_ultima_atualizacao = '{kwargs['data_ultima_atualizacao']}', "
            if 'nome_banco' in kwargs:
                comando += f"nome_banco = '{kwargs['nome_banco']}', "
            if 'nome_titulo' in kwargs:
                comando += f"nome_titulo = '{kwargs['nome_titulo']}', "
            if 'tipo_investimento' in kwargs:
                comando += f"tipo_investimento = '{kwargs['tipo_investimento']}', "
            if 'quantidade' in kwargs:
                comando += f"quantidade = '{str(kwargs['quantidade'])}', "
            if 'valor_investido' in kwargs:
                comando += f"valor_investido = '{str(kwargs['valor_investido'])}', "
            if 'data_inicio' in kwargs:
                comando += f"data_inicio = '{kwargs['data_inicio']}', "
            if 'data_venda' in kwargs:
                comando += f"data_venda = '{kwargs['data_venda']}', "
            if 'lucro_prejuizo' in kwargs:
                comando += f"lucro_prejuizo = '{str(kwargs['lucro_prejuizo'])}', "
            if 'percentual_lucro_prejuizo' in kwargs:
                comando += f"percentual_lucro_prejuizo = '{str(kwargs['percentual_lucro_prejuizo'])}', "
            if 'status' in kwargs:
                comando += f"status = '{str(kwargs['status'])}', "
            if 'active' in kwargs:
                comando += f"active = '{str(kwargs['active'])}', "
            comando = comando[:-2] + f" WHERE id = {id}"
            print(comando)
            cur.execute(comando)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao atualizar investimento: {e}", file=sys.stderr)
        return False
if False:
    if set_invest('Tesouso Reserva', 'Banco do Brasil', 'Tesouso Direto', 1, 13391.41, '2026-08-11'):
        print("Investimento criado com sucesso!")
    else:
        print("Erro ao criar investimento!")

if False:
    if update_invest(id=1, valor_atual=13391.41, data_ultima_atualizacao='2026-08-11', status='active', active=True):
        print("Investimento atualizado com sucesso!")
    else:
        print("Erro ao atualizar investimento!")