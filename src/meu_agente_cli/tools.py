import httpx
import yfinance as yf
import feedparser
from typing import Dict, Any, List, Optional
from datetime import datetime
import meu_agente_cli.db as db
import meu_agente_cli.security as security
import meu_agente_cli.invest as invest

# Dicionário de Feeds RSS populares
RSS_FEEDS = {
    "economia": "https://g1.globo.com/rss/g1/economia/",
    "tecnologia": "https://g1.globo.com/rss/g1/tecnologia/",
    "geral": "https://feeds.bbci.co.uk/portuguese/rss.xml",
    "ciencia": "https://g1.globo.com/rss/g1/ciencia-e-saude/"
}

# =====================================================================
# FERRAMENTA: CLIMA (OPEN-METEO)
# =====================================================================

def get_weather(city_name: str) -> str:
    """
    Busca a previsão do tempo atual para uma cidade usando as APIs do Open-Meteo.
    Não requer chaves de API.
    """
    try:
        # 1. Geocodificação: busca latitude e longitude pelo nome da cidade
        geocode_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_name}&count=1&language=pt&format=json"
        geo_res = httpx.get(geocode_url, timeout=10)
        if geo_res.status_code != 200:
            return f"Erro ao buscar coordenadas da cidade '{city_name}': Código {geo_res.status_code}"
            
        geo_data = geo_res.json()
        if "results" not in geo_data or not geo_data["results"]:
            return f"Cidade '{city_name}' não encontrada."
            
        result = geo_data["results"][0]
        lat = result["latitude"]
        lon = result["longitude"]
        full_name = f"{result.get('name')}, {result.get('admin1', '')} - {result.get('country', '')}"
        
        # 2. Clima: Busca clima atual usando latitude e longitude
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        w_res = httpx.get(weather_url, timeout=10)
        if w_res.status_code != 200:
            return f"Erro ao carregar clima para '{full_name}': Código {w_res.status_code}"
            
        w_data = w_res.json()
        current = w_data.get("current_weather")
        if not current:
            return f"Não foi possível obter dados de clima para '{full_name}'."
            
        temp = current.get("temperature")
        windspeed = current.get("windspeed")
        weathercode = current.get("weathercode")
        
        # Mapeamento básico de weathercodes do Open-Meteo para texto
        weather_desc = {
            0: "Céu limpo",
            1: "Principalmente limpo", 2: "Parcialmente nublado", 3: "Nublado",
            45: "Nevoeiro", 48: "Nevoeiro com geada depósito",
            51: "Chuvisco leve", 53: "Chuvisco moderado", 55: "Chuvisco denso",
            61: "Chuva leve", 63: "Chuva moderada", 65: "Chuva forte",
            71: "Neve leve", 73: "Neve moderada", 75: "Neve forte",
            77: "Granizo",
            80: "Pancadas de chuva leve", 81: "Pancadas de chuva moderada", 82: "Pancadas de chuva violenta",
            95: "Trovoada", 96: "Trovoada com granizo leve", 99: "Trovoada com granizo forte"
        }
        desc = weather_desc.get(weathercode, "Condição desconhecida")
        
        return (
            f"Clima atual em {full_name}:\n"
            f"- Temperatura: {temp}°C\n"
            f"- Condição: {desc}\n"
            f"- Velocidade do vento: {windspeed} km/h"
        )
        
    except Exception as e:
        return f"Falha na consulta do clima: {str(e)}"

# =====================================================================
# FERRAMENTA: AÇÕES E CÂMBIO (YFINANCE)
# =====================================================================

def get_financial_quote(symbol: str) -> str:
    """
    Consulta o Yahoo Finance para obter a cotação mais recente de um ticker ou moeda.
    Exemplos: 'PETR4.SA' (Petrobras), 'USDBRL=X' (Dólar para Real), 'EURBRL=X'.
    """
    # Padroniza alguns símbolos conhecidos do usuário brasileiro
    symbol_upper = symbol.strip().upper()
    if symbol_upper == "DOLAR" or symbol_upper == "USD":
        symbol_upper = "USDBRL=X"
    elif symbol_upper == "EURO" or symbol_upper == "EUR":
        symbol_upper = "EURBRL=X"
    elif symbol_upper == "IBOVESPA" or symbol_upper == "IBOV":
        symbol_upper = "^BVSP"
        
    try:
        ticker = yf.Ticker(symbol_upper)
        # yfinance history de 5 dias para ter o fechamento de hoje e de ontem
        hist = ticker.history(period="5d")
        if hist.empty:
            return f"Não foi possível obter cotações para o ticker '{symbol_upper}'."
            
        # Pega a linha mais recente e a anterior
        close_prices = hist['Close'].tolist()
        last_price = close_prices[-1]
        
        if len(close_prices) >= 2:
            prev_price = close_prices[-2]
            variation = ((last_price - prev_price) / prev_price) * 100
            variation_str = f"{variation:+.2f}%"
        else:
            variation_str = "N/A"
            
        # Pega nome da moeda ou ativo
        # Tentamos fast_info ou info, se falhar usamos o símbolo
        try:
            info_name = ticker.fast_info.get("currency", "BRL")
            currency = info_name
        except Exception:
            currency = "BRL" if ".SA" in symbol_upper or "^BVSP" in symbol_upper else "USD"
            
        return (
            f"Ativo: {symbol_upper}\n"
            f"- Último Preço: {last_price:.2f} {currency}\n"
            f"- Variação Diária: {variation_str}\n"
            f"- Atualizado em: {hist.index[-1].strftime('%d/%m/%Y')}"
        )
    except Exception as e:
        return f"Falha na consulta financeira para o ativo '{symbol_upper}': {str(e)}"

# =====================================================================
# FERRAMENTA: NOTÍCIAS (RSS FEEDS)
# =====================================================================

def get_news(category: str = "geral") -> str:
    """
    Busca as últimas notícias de uma categoria de RSS feed ('economia', 'tecnologia', 'geral', 'ciencia').
    """
    cat = category.strip().lower()
    url = RSS_FEEDS.get(cat)
    if not url:
        # Se não achou, usa o feed geral
        url = RSS_FEEDS["geral"]
        cat = "geral"
        
    try:
        feed = feedparser.parse(url)
        if not feed.entries:
            return f"Não foi possível recuperar notícias na categoria '{cat}'."
            
        output = [f"Últimas notícias - Categoria: {cat.capitalize()}:"]
        # Limita a 5 notícias
        for i, entry in enumerate(feed.entries[:5], 1):
            title = entry.title
            link = entry.link
            output.append(f"{i}. {title}\n   Link: {link}")
            
        return "\n\n".join(output)
    except Exception as e:
        return f"Erro ao buscar notícias: {str(e)}"

# =====================================================================
# FERRAMENTA: FINANÇAS PESSOAIS (POSTGRESQL)
# =====================================================================

def finance_tool(action: str, category: str = "", amount: float = 0.0, description: str = "", due_date: Optional[str] = None, record_id: Optional[int] = None, record_ids: Optional[List[int]] = None, items: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    Interface para o módulo financeiro no banco de dados.
    Ações: 'add_receita', 'add_despesa', 'add_bulk', 'delete', 'extrato', 'resumo'
    """
    act = action.strip().lower()
    if act == "add_bulk":
        if not items:
            return "Erro: Parâmetro 'items' contendo a lista de lançamentos é obrigatório para a ação 'add_bulk'."
        success = db.add_financial_records_bulk(items)
        if success:
            return f"[SUCCESS] Registrados com sucesso {len(items)} lançamentos financeiros em lote."
        else:
            return "[ERROR] Falha ao registrar lançamentos em lote no banco de dados."
            
    elif act == "delete":
        if record_ids:
            success = db.delete_financial_records_bulk(record_ids)
            if success:
                return f"[SUCCESS] Registros financeiros {record_ids} removidos com sucesso."
            else:
                return f"[ERROR] Falha ao remover os registros financeiros {record_ids}."
        elif record_id is not None:
            success = db.delete_financial_record(record_id)
            if success:
                return f"[SUCCESS] Registro financeiro #{record_id} removido com sucesso."
            else:
                return f"[ERROR] Falha ao remover o registro financeiro #{record_id}."
        else:
            return "Erro: Parâmetro 'record_id' ou 'record_ids' é obrigatório para a ação 'delete'."
            
    elif act == "add_receita" or act == "add_despesa":
        record_type = "receita" if "receita" in act else "despesa"
        if not category:
            return "Erro: Categoria é obrigatória para registrar transações."
        if amount <= 0:
            return "Erro: Valor deve ser maior que zero."
            
        success = db.add_financial_record(record_type, category, amount, description, due_date)
        if success:
            due_part = f" com vencimento em {due_date}" if due_date else ""
            return f"[SUCCESS] Registro financeiro adicionado: {record_type.capitalize()} de R$ {amount:.2f} na categoria '{category}'{due_part}."
        else:
            return "[ERROR] Falha ao salvar registro financeiro no banco de dados."
            
    elif act == "extrato":
        records = db.get_financial_records(limit=20)
        if not records:
            return "Nenhum registro financeiro encontrado."
            
        output = ["Extrato das últimas 20 transações:"]
        for r in records:
            rec_id, r_type, cat, val, desc, dt, due_dt = r
            due_part = f" | Venc: {due_dt.strftime('%d/%m/%Y')}" if due_dt else ""
            desc_part = f" ({desc})" if desc else ""
            output.append(f"[{dt.strftime('%d/%m/%Y')}] #{rec_id} {r_type.upper()} | {cat}: R$ {val:.2f}{due_part}{desc_part}")
        return "\n".join(output)
        
    elif act == "resumo":
        summary = db.get_financial_summary()
        return (
            f"Resumo Financeiro:\n"
            f"- Total de Receitas: R$ {summary['receitas']:.2f}\n"
            f"- Total de Despesas: R$ {summary['despesas']:.2f}\n"
            f"- Saldo Atual: R$ {summary['saldo']:.2f}"
        )
    else:
        return "Erro: Ação financeira desconhecida. Use 'add_receita', 'add_despesa', 'add_bulk', 'delete', 'extrato' ou 'resumo'."

# =====================================================================
# FERRAMENTA: ANOTAÇÕES / MEMÓRIA (POSTGRESQL)
# =====================================================================

def notes_tool(action: str, content: str = "", query: str = "", note_id: Optional[int] = None) -> str:
    """
    Interface para o módulo de anotações e memória de longo prazo no banco de dados.
    Ações: 'add', 'search', 'list', 'delete'
    """
    act = action.strip().lower()
    if act == "add":
        if not content:
            return "Erro: Conteúdo da nota não pode ser vazio."
        success = db.add_user_note(content)
        if success:
            return "[SUCCESS] Informação salva na base de conhecimento com sucesso!"
        else:
            return "[ERROR] Falha ao salvar a informação no banco de dados."
            
    elif act == "search":
        if not query:
            return "Erro: Termo de busca não fornecido."
        notes = db.search_user_notes(query)
        if not notes:
            return f"Nenhuma nota encontrada contendo '{query}'."
            
        output = [f"Notas encontradas contendo '{query}':"]
        for n in notes:
            nid, ncontent, dt = n
            output.append(f"#{nid} [{dt.strftime('%d/%m/%Y %H:%M')}]: {ncontent}")
        return "\n".join(output)
        
    elif act == "list":
        notes = db.list_all_user_notes()
        if not notes:
            return "Nenhuma anotação salva."
        output = ["Lista de notas salvas:"]
        for n in notes:
            nid, ncontent, dt = n
            output.append(f"#{nid} [{dt.strftime('%d/%m/%Y %H:%M')}]: {ncontent}")
        return "\n".join(output)
        
    elif act == "delete":
        if note_id is None:
            return "Erro: ID da nota não fornecido para exclusão."
        success = db.delete_user_note(note_id)
        if success:
            return f"[SUCCESS] Nota #{note_id} removida com sucesso."
        else:
            return f"[ERROR] Falha ao remover nota #{note_id}."
            
    else:
        return "Erro: Ação de notas desconhecida. Use 'add', 'search', 'list' ou 'delete'."

# =====================================================================
# FERRAMENTA: COMANDO CLI LINUX (SUBPROCESS)
# =====================================================================

def execute_cli_command(command: str) -> Dict[str, Any]:
    """
    Tenta executar um comando de terminal Linux.
    - Se for seguro, executa direto.
    - Se for inseguro:
      - Se estiver em Modo Seguro, retorna um status que indica necessidade de autorização/senha.
      - Se estiver em Modo Não-Seguro, solicita confirmação e executa.
    """
    cmd = command.strip()
    if security.is_command_safe(cmd):
        code, out, err = security.run_bash_command(cmd)
        return {
            "status": "executed",
            "exit_code": code,
            "stdout": out,
            "stderr": err,
            "safe": True
        }
    else:
        # Se for inseguro
        if security.is_safe_mode():
            return {
                "status": "needs_unsafe_mode",
                "command": cmd,
                "reason": "Comando não está na lista de comandos seguros e o agente está em Modo Seguro."
            }
        else:
            # Em modo não-seguro, a execução real precisará de aprovação interativa
            # O orquestrador (agent.py) tratará essa aprovação interativa no terminal.
            return {
                "status": "needs_user_confirmation",
                "command": cmd,
                "reason": "Comando não-seguro. Necessita de confirmação do usuário."
            }

# =====================================================================
# FERRAMENTA: CALCULADORA MATEMÁTICA SEGURA
# =====================================================================

def calculator_tool(expression: Optional[str] = None, expressions: Optional[Dict[str, str]] = None) -> str:
    """
    Avalia uma ou mais expressões matemáticas de forma segura.
    Suporta expressões individuais (string) ou múltiplas em lote (dicionário {id: expressão}).
    """
    import re
    import json
    
    def evaluate_expr(expr_str: str) -> str:
        expr_clean = expr_str.replace(" ", "")
        # Valida se a expressão contém apenas dígitos, operadores (+, -, *, /, .) e parênteses
        # Permite também '**' para potência
        if not re.match(r'^[0-9\+\-\*\/\.\(\)]+$', expr_clean):
            return "Erro: A expressão contém caracteres inválidos."
        try:
            result = eval(expr_clean, {"__builtins__": None}, {})
            return str(result)
        except ZeroDivisionError:
            return "Erro: Divisão por zero."
        except Exception as e:
            return f"Erro: {str(e)}"
            
    # Caso 1: Múltiplas expressões em lote
    if expressions:
        results = {}
        for expr_id, expr_str in expressions.items():
            results[expr_id] = evaluate_expr(expr_str)
        return json.dumps(results, indent=2, ensure_ascii=False)
        
    # Caso 2: Expressão única
    if expression:
        return f"Resultado: {evaluate_expr(expression)}"
        
    return "Erro: Nenhuma expressão ou lote de expressões fornecido."

def invest_tool(**kwargs):
    if 'action' in kwargs:
        if kwargs['action'] == 'get_invest':
            return invest.get_invest()
        elif kwargs['action'] == 'set_invest':
            return invest.set_invest(**kwargs)
        elif kwargs['action'] == 'update_invest':
            return invest.update_invest(**kwargs)
    return f"Erro: Ação inválida para a ferramenta de investimentos.\nParâmetros recebidos: {kwargs}"
    