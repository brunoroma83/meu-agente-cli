from meu_agente_cli.db import get_connection
import sys

def criar_tabela_vinhos():
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vinhos (
                    id SERIAL PRIMARY KEY,
                    nome VARCHAR(100) NOT NULL,
                    ano INT,
                    regiao VARCHAR(100),
                    tipo VARCHAR(50),
                    nota NUMERIC(2, 1),
                    valor_compra NUMERIC(12, 2),
                    foto_vinho TEXT,
                    data_compra DATE,
                    observacoes TEXT,
                    informacoes TEXT,
                    ativo BOOLEAN DEFAULT TRUE
                )
            """)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Erro ao criar tabela de vinhos: {e}", file=sys.stderr)
        return False

def salvar_vinho(**kwargs):
    if 'nome' not in kwargs:
        return "Erro: O nome do vinho é obrigatório!"
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO vinhos (nome, ano, regiao, tipo, nota, valor_compra, foto_vinho, data_compra, observacoes, informacoes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                kwargs['nome'],
                int(kwargs.get('ano', 0)) if str(kwargs.get('ano', '')).isdigit() else 0,
                kwargs.get('regiao', ' '),
                kwargs.get('tipo', ' '),
                float(kwargs.get('nota', 0.0)) if kwargs.get('nota') else 0.0,
                float(kwargs.get('valor_compra', 0.0)) if kwargs.get('valor_compra') else 0.0,
                kwargs.get('foto_vinho', ' '),
                kwargs.get('data_compra', '2026-08-12') or '2026-08-12',
                kwargs.get('observacoes', ' '),
                kwargs.get('informacoes', ' ')
            ))
            new_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return f"Vinho '{kwargs['nome']}' cadastrado com sucesso! ID: {new_id}"
    except Exception as e:
        print(f"[ERROR] Erro ao salvar vinho: {e}", file=sys.stderr)
        return f"Erro ao salvar vinho: {e}"

def atualizar_vinho(**kwargs):
    id_vinho = kwargs.get('id_vinho') or kwargs.get('id')
    if not id_vinho:
        return "Erro: ID do vinho é obrigatório para atualização!"
        
    try:
        id_vinho = int(id_vinho)
    except ValueError:
        return "Erro: ID do vinho inválido!"
        
    fields = []
    values = []
    
    mapeamento = {
        'nome': str,
        'ano': int,
        'regiao': str,
        'tipo': str,
        'nota': float,
        'valor_compra': float,
        'foto_vinho': str,
        'data_compra': str,
        'observacoes': str,
        'informacoes': str,
        'ativo': bool
    }
    
    for k, t in mapeamento.items():
        if k in kwargs:
            val = kwargs[k]
            if val is not None:
                try:
                    if t == int:
                        val = int(val) if str(val).isdigit() else 0
                    elif t == float:
                        val = float(val)
                    elif t == bool:
                        val = str(val).lower() in ['true', '1', 'yes', 't']
                except ValueError:
                    continue
                fields.append(f"{k} = %s")
                values.append(val)
                
    if not fields:
        return "Erro: Nenhuma informação enviada para atualizar."
        
    values.append(id_vinho)
    sql = f"UPDATE vinhos SET {', '.join(fields)} WHERE id = %s"
    
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(sql, tuple(values))
        conn.commit()
        conn.close()
        return f"Vinho ID #{id_vinho} atualizado com sucesso!"
    except Exception as e:
        print(f"[ERROR] Erro ao atualizar vinho: {e}", file=sys.stderr)
        return f"Erro ao atualizar vinho: {e}"

def buscar_vinhos(**kwargs):
    query = kwargs.get('query', '')
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            if not query:
                cur.execute("SELECT id, nome, ano, regiao, tipo, nota, valor_compra, observacoes FROM vinhos WHERE ativo = TRUE ORDER BY id DESC")
            else:
                cur.execute(
                    "SELECT id, nome, ano, regiao, tipo, nota, valor_compra, observacoes FROM vinhos "
                    "WHERE ativo = TRUE AND (nome ILIKE %s OR regiao ILIKE %s OR tipo ILIKE %s) ORDER BY id DESC",
                    (f"%{query}%", f"%{query}%", f"%{query}%")
                )
            registros = cur.fetchall()
            
        conn.close()
        if not registros:
            return "Nenhum vinho encontrado!"
            
        linhas = ["🍷 *Vinhos Encontrados:*"]
        for r in registros:
            # Renders values nicely
            nota_val = float(r[5]) if r[5] else 0.0
            compra_val = float(r[6]) if r[6] else 0.0
            linhas.append(f"• *#{r[0]}* - {r[1]} ({r[2]}) | Região: {r[3]} | Tipo: {r[4]} | Nota: {nota_val}/5 | Valor: R$ {compra_val:.2f}\n  _Obs: {r[7]}_")
        return "\n".join(linhas)
    except Exception as e:
        print(f"[ERROR] Erro ao buscar vinhos: {e}", file=sys.stderr)
        return f"Erro ao buscar vinhos: {e}"

def run(action: str, **kwargs):
    # Garante a criação da tabela no banco de dados
    criar_tabela_vinhos()
    
    action = action.lower()
    if action == "criar" or action == "salvar":
        return salvar_vinho(**kwargs)
    elif action == "atualizar":
        return atualizar_vinho(**kwargs)
    elif action == "buscar":
        return buscar_vinhos(**kwargs)
    else:
        return f"Erro: Ação '{action}' inválida para o vinho_tool. Use 'criar', 'atualizar' ou 'buscar'."