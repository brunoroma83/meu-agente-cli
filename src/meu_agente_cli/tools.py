import json
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

def finance_tool(
    action: str, 
    category: str = "", 
    amount: float = 0.0, 
    description: str = "", 
    due_date: Optional[str] = None, 
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    record_id: Optional[int] = None, 
    record_ids: Optional[List[int]] = None, 
    items: Optional[List[Dict[str, Any]]] = None,
    query: Optional[str] = None,
    limit: Optional[int] = None,
    **kwargs
) -> str:
    """
    Interface para o módulo financeiro no banco de dados.
    Ações: 'add_receita', 'add_despesa', 'add_bulk', 'delete', 'extrato', 'resumo', 'search' (ou 'busca'/'buscar')
    """
    act = action.strip().lower()
    
    # Extrai argumentos flexíveis de kwargs para compatibilidade
    start_date = start_date or kwargs.get("start_due_date") or kwargs.get("due_date_start") or kwargs.get("data_inicio") or kwargs.get("data_inicial")
    end_date = end_date or kwargs.get("end_due_date") or kwargs.get("due_date_end") or kwargs.get("data_fim") or kwargs.get("data_final")
    due_date = due_date or kwargs.get("data_vencimento") or kwargs.get("vencimento")
    query = query or kwargs.get("q") or kwargs.get("termo") or kwargs.get("busca")
    month_year = kwargs.get("month_year") or kwargs.get("mes") or kwargs.get("mes_ano")
    record_type = kwargs.get("record_type") or kwargs.get("type") or kwargs.get("tipo")
    if not category and kwargs.get("categoria"):
        category = kwargs.get("categoria", "")

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
            
    card_name = kwargs.get("card_name") or kwargs.get("cartao") or kwargs.get("card")
    installments = kwargs.get("installments") or kwargs.get("parcelas") or 1
    buy_date_str = due_date or kwargs.get("buy_date") or kwargs.get("data")

    if act in ("card_buy", "buy_card", "add_card_purchase") or (act == "add_despesa" and card_name):
        if not card_name:
            return "Erro: Nome do cartão ('card_name') é obrigatório para registrar compra no cartão."
        if not category:
            category = "Outros"
        if amount <= 0:
            return "Erro: Valor deve ser maior que zero."
        try:
            installments = int(installments)
        except (ValueError, TypeError):
            installments = 1
        success = db.add_card_purchase(
            card_name=str(card_name).strip(),
            category=category,
            total_amount=amount,
            installments=installments,
            description=description,
            buy_date_str=buy_date_str
        )
        if success:
            parcelas_info = f" em {installments}x" if installments > 1 else " à vista"
            return f"[SUCCESS] Compra no cartão '{card_name}' registrada: R$ {amount:.2f}{parcelas_info} na categoria '{category}' ({description or 'Sem descrição'})."
        else:
            return f"[ERROR] Falha ao registrar compra no cartão '{card_name}' no banco de dados."

    elif act in ("add_receita", "add_despesa"):
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
            
    elif act in ("extrato", "search", "busca", "buscar", "buscar_vencimento", "buscar_por_vencimento", "filtro", "filtrar"):
        is_filtered_search = bool(due_date or start_date or end_date or query or month_year or record_type or category or act != "extrato")
        search_limit = limit if limit is not None else (None if is_filtered_search else 20)
        
        records = db.search_financial_records(
            limit=search_limit,
            month_year=month_year,
            query=query,
            due_date=due_date,
            start_due_date=start_date,
            end_due_date=end_date,
            record_type=record_type,
            category=category if category else None
        )
        if not records:
            detalhes = []
            if due_date:
                detalhes.append(f"com vencimento em {due_date}")
            elif start_date and end_date:
                detalhes.append(f"com vencimento entre {start_date} e {end_date}")
            elif start_date:
                detalhes.append(f"com vencimento a partir de {start_date}")
            elif end_date:
                detalhes.append(f"com vencimento até {end_date}")
            if month_year:
                detalhes.append(f"no mês/ano {month_year}")
            if query:
                detalhes.append(f"contendo '{query}'")
            if category:
                detalhes.append(f"na categoria '{category}'")
                
            criterio_str = " (" + ", ".join(detalhes) + ")" if detalhes else ""
            return f"Nenhum registro financeiro encontrado{criterio_str}."
            
        if due_date:
            titulo = f"Registros financeiros com vencimento em {due_date}:"
        elif start_date and end_date:
            titulo = f"Registros financeiros com vencimento entre {start_date} e {end_date}:"
        elif start_date:
            titulo = f"Registros financeiros com vencimento a partir de {start_date}:"
        elif end_date:
            titulo = f"Registros financeiros com vencimento até {end_date}:"
        elif month_year:
            titulo = f"Registros financeiros com vencimento no mês {month_year}:"
        elif query:
            titulo = f"Registros financeiros encontrados para '{query}':"
        else:
            titulo = f"Extrato das últimas {len(records)} transações:"
            
        output = [titulo]
        sum_rec = 0.0
        sum_desp = 0.0
        for r in records:
            rec_id, r_type, cat, val, desc, dt, due_dt = r
            if str(r_type).lower() == "receita":
                sum_rec += val
            else:
                sum_desp += val
            due_part = f" | Venc: {due_dt.strftime('%d/%m/%Y')}" if due_dt else ""
            desc_part = f" ({desc})" if desc else ""
            output.append(f"[{dt.strftime('%d/%m/%Y')}] #{rec_id} {r_type.upper()} | {cat}: R$ {val:.2f}{due_part}{desc_part}")
            
        output.append("---")
        output.append(f"Total: {len(records)} registro(s) | Receitas: R$ {sum_rec:.2f} | Despesas: R$ {sum_desp:.2f} | Saldo: R$ {sum_rec - sum_desp:.2f}")
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
        return "Erro: Ação financeira desconhecida. Use 'add_receita', 'add_despesa', 'add_bulk', 'delete', 'search' (busca por vencimento/intervalo), 'extrato' ou 'resumo'."

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
    """
    Ferramenta para gestão de carteira de investimentos (Renda Fixa e Ações).
    Suporta registro de movimentações, extratos e consultas consolidadas de patrimônio.
    """
    action = kwargs.get("action", "").strip().lower()
    
    # -------------------------------------------------------------
    # 1. AÇÕES (RENDA VARIÁVEL)
    # -------------------------------------------------------------
    if action in ("registrar_acao", "add_movimentacao_acao", "add_acao"):
        cod = kwargs.get("codigo_acao") or kwargs.get("ticker") or ""
        op = kwargs.get("operacao") or "COMPRA"
        qtd = kwargs.get("quantidade", 0)
        preco = kwargs.get("preco_unitario") or kwargs.get("preco", 0.0)
        taxas = kwargs.get("taxas", 0.0)
        dt = kwargs.get("data_operacao") or kwargs.get("data")
        rel_id = kwargs.get("relacao_id")
        
        ok, msg = invest.add_movimentacao_acao(
            codigo_acao=cod,
            operacao=op,
            quantidade=qtd,
            preco_unitario=preco,
            taxas=taxas,
            data_operacao=dt,
            relacao_id=rel_id
        )
        return msg if ok else f"Erro ao registrar ação: {msg}"
        
    elif action in ("consultar_movimentacoes_acoes", "get_movimentacoes_acoes", "extrato_acoes"):
        cod = kwargs.get("codigo_acao") or kwargs.get("ticker")
        op = kwargs.get("operacao")
        dt_ini = kwargs.get("data_inicio")
        dt_fim = kwargs.get("data_fim")
        movs = invest.get_movimentacoes_acoes(codigo_acao=cod, operacao=op, data_inicio=dt_ini, data_fim=dt_fim)
        # Formata datas para string se necessário
        for m in movs:
            if hasattr(m.get("data_operacao"), "isoformat"):
                m["data_operacao"] = m["data_operacao"].isoformat()
        return json.dumps(movs, indent=2, ensure_ascii=False)
        
    elif action in ("consultar_consolidado_acoes", "get_consolidado_acoes", "carteira_acoes", "consultar_view_acoes"):
        fetch_quotes = kwargs.get("fetch_quotes", True)
        if action == "consultar_view_acoes":
            consolidado = invest.get_view_consolidado_acoes()
        else:
            consolidado = invest.get_consolidado_acoes(fetch_market_prices=fetch_quotes)
        for c in consolidado:
            if hasattr(c.get("data_ultima_operacao"), "isoformat"):
                c["data_ultima_operacao"] = c["data_ultima_operacao"].isoformat()
        return json.dumps(consolidado, indent=2, ensure_ascii=False)

    # -------------------------------------------------------------
    # 2. RENDA FIXA
    # -------------------------------------------------------------
    elif action in ("registrar_renda_fixa", "add_movimentacao_rf", "add_rf"):
        inv_id = kwargs.get("id_investimento") or kwargs.get("id")
        if not inv_id:
            return "Erro: 'id_investimento' é obrigatório para registrar movimentação de renda fixa."
        tipo_mov = kwargs.get("tipo_movimentacao") or kwargs.get("tipo", "APORTE")
        valor = kwargs.get("valor", 0.0)
        dt = kwargs.get("data_movimentacao") or kwargs.get("data")
        
        ok, msg = invest.add_movimentacao_renda_fixa(
            id_investimento=int(inv_id),
            tipo_movimentacao=tipo_mov,
            valor=valor,
            data_movimentacao=dt
        )
        return msg if ok else f"Erro ao registrar renda fixa: {msg}"
        
    elif action in ("cadastrar_titulo_rf", "novo_titulo_rf"):
        nome = kwargs.get("nome_titulo") or kwargs.get("nome", "")
        banco = kwargs.get("nome_banco") or kwargs.get("banco", "Outro")
        tipo = kwargs.get("tipo_investimento") or kwargs.get("tipo", "CDB")
        dt = kwargs.get("data_inicio") or kwargs.get("data")
        v_ini = kwargs.get("valor_inicial") or kwargs.get("valor", 0.0)
        
        novo_id = invest.cadastrar_titulo_renda_fixa(
            nome_titulo=nome,
            nome_banco=banco,
            tipo_investimento=tipo,
            data_inicio=dt,
            valor_inicial=float(v_ini)
        )
        if novo_id:
            return f"Título de Renda Fixa '{nome}' cadastrado com sucesso com ID #{novo_id}!"
        return f"Erro ao cadastrar título de renda fixa '{nome}'."
        
    elif action in ("consultar_movimentacoes_rf", "get_movimentacoes_rf", "extrato_rf"):
        inv_id = kwargs.get("id_investimento")
        tipo = kwargs.get("tipo") or kwargs.get("tipo_movimentacao")
        dt_ini = kwargs.get("data_inicio")
        dt_fim = kwargs.get("data_fim")
        movs = invest.get_movimentacoes_renda_fixa(
            id_investimento=int(inv_id) if inv_id else None,
            tipo=tipo,
            data_inicio=dt_ini,
            data_fim=dt_fim
        )
        for m in movs:
            if hasattr(m.get("data_movimentacao"), "isoformat"):
                m["data_movimentacao"] = m["data_movimentacao"].isoformat()
        return json.dumps(movs, indent=2, ensure_ascii=False)
        
    elif action in ("consultar_consolidado_rf", "get_consolidado_rf", "carteira_rf"):
        consolidado = invest.get_consolidado_renda_fixa(active_only=True)
        for c in consolidado:
            for k in ("data_inicio", "data_ultima_atualizacao"):
                if hasattr(c.get(k), "isoformat"):
                    c[k] = c[k].isoformat()
        return json.dumps(consolidado, indent=2, ensure_ascii=False)
        
    elif action in ("atualizar_cotacao_rf", "update_valor_rf"):
        inv_id = kwargs.get("id_investimento") or kwargs.get("id")
        novo_v = kwargs.get("novo_valor") or kwargs.get("valor_atual")
        if not inv_id or novo_v is None:
            return "Erro: 'id_investimento' e 'novo_valor' são obrigatórios."
        ok, msg = invest.update_valor_atual_renda_fixa(int(inv_id), float(novo_v))
        return msg if ok else f"Erro: {msg}"

    # -------------------------------------------------------------
    # 3. CONSOLIDADO GERAL / CARTEIRA GLOBAL
    # -------------------------------------------------------------
    elif action in ("consultar_consolidado_geral", "get_consolidado_geral", "resumo_carteira", "patrimonio"):
        resumo = invest.get_resumo_patrimonial_geral()
        return json.dumps(resumo, indent=2, ensure_ascii=False)
        
    elif action in ("migrar_legados", "migrar_investimentos"):
        res = invest.migrar_investimentos_legados()
        return json.dumps(res, indent=2, ensure_ascii=False)

    # -------------------------------------------------------------
    # 4. COMPATIBILIDADE RETROATIVA (LEGACY)
    # -------------------------------------------------------------
    elif action == 'get_invest':
        return json.dumps(invest.get_resumo_patrimonial_geral(), indent=2, ensure_ascii=False)
    elif action == 'set_invest':
        ok = invest.set_invest(**kwargs)
        return "Investimento cadastrado com sucesso!" if ok else "Erro ao cadastrar investimento."
    elif action == 'update_invest':
        ok = invest.update_invest(**kwargs)
        return "Investimento atualizado com sucesso!" if ok else "Erro ao atualizar investimento."

    return f"Erro: Ação '{action}' inválida para a ferramenta de investimentos.\nAções disponíveis: 'registrar_acao', 'consultar_movimentacoes_acoes', 'consultar_consolidado_acoes', 'registrar_renda_fixa', 'cadastrar_titulo_rf', 'consultar_movimentacoes_rf', 'consultar_consolidado_rf', 'atualizar_cotacao_rf', 'consultar_consolidado_geral'."

# =====================================================================
# FERRAMENTA: PERFIL DO USUÁRIO (POSTGRESQL)
# =====================================================================

def profile_tool(action: str, category: str = "", content: str = "") -> str:
    """
    Interface para gerenciar informações do perfil do usuário na memória da sessão atual.
    Ações: 'save', 'get', 'delete', 'list'
    Categorias sugeridas: 'familiar', 'profissional', 'academico', 'preferencias', 'rotina', 'desejos', 'politica'
    """
    act = action.strip().lower()
    cat = category.strip().lower()
    
    # Obtém o usuário ativo na sessão
    user_name = db.get_logged_in_user()
    if not user_name:
        return "Erro: Nenhum usuário logado na sessão ativa. Por favor, faça login antes de usar o perfil."
    
    if act == "save":
        if not cat:
            return "Erro: Categoria é obrigatória para salvar informações no perfil."
        if not content:
            return "Erro: O conteúdo não pode ser vazio ao salvar no perfil."
        
        success = db.save_user_profile(cat, content, user_name)
        if success:
            return f"[SUCCESS] Perfil atualizado na categoria '{cat}' para o usuário '{user_name}'!"
        else:
            return f"[ERROR] Falha ao atualizar perfil na categoria '{cat}' para o usuário '{user_name}'."
            
    elif act == "get":
        if not cat:
            return "Erro: Categoria é obrigatória para consultar informações do perfil."
        info = db.get_user_profile(cat, user_name)
        if info:
            return f"Informação do perfil na categoria '{cat}' para '{user_name}':\n{info}"
        else:
            return f"Nenhuma informação encontrada para a categoria '{cat}' para '{user_name}'."
            
    elif act == "delete":
        if not cat:
            return "Erro: Categoria é obrigatória para excluir informações do perfil."
        success = db.delete_user_profile(cat, user_name)
        if success:
            return f"[SUCCESS] Categoria '{cat}' removida do perfil com sucesso para o usuário '{user_name}'."
        else:
            return f"[ERROR] Falha ao remover categoria '{cat}' do perfil para o usuário '{user_name}'."
            
    elif act == "list":
        profile_data = db.get_user_profile(user_name=user_name)
        if not profile_data:
            return f"O perfil do usuário '{user_name}' está vazio."
        
        output = [f"Informações atuais do Perfil de '{user_name}':"]
        for c, val in profile_data.items():
            output.append(f"- {c.capitalize()}: {val}")
        return "\n".join(output)
        
    else:
        return "Erro: Ação desconhecida. Use 'save', 'get', 'delete' ou 'list'."

# =====================================================================
# FERRAMENTAS E UTILITÁRIOS: EXTRAÇÃO E LEITURA DE PDFS
# =====================================================================

def extract_pdf_text(file_path: str, start_page: int = 1, end_page: Optional[int] = None) -> str:
    """
    Extrai o conteúdo de texto de um arquivo PDF local usando PyPDF2.
    Suporta filtro por intervalo de páginas (start_page até end_page).
    Retorna o texto estruturado por páginas ou mensagem informativa de erro/aviso.
    """
    from pathlib import Path
    from PyPDF2 import PdfReader

    clean_path = str(file_path).strip().strip("'\"")
    path_obj = Path(clean_path).expanduser().resolve()

    if not path_obj.exists():
        return f"[Erro: Arquivo PDF não encontrado em '{clean_path}']"

    if not path_obj.is_file():
        return f"[Erro: O caminho informado não é um arquivo: '{clean_path}']"

    try:
        with open(path_obj, "rb") as f:
            reader = PdfReader(f)
            num_pages = len(reader.pages)
            if num_pages == 0:
                return f"[INFO] O arquivo PDF '{path_obj.name}' está vazio (0 páginas)."

            # Valida e ajusta intervalo de páginas
            s_page = max(1, min(int(start_page), num_pages))
            e_page = min(num_pages, int(end_page)) if end_page is not None else num_pages

            pages_text = []
            has_any_text = False
            for i in range(s_page, e_page + 1):
                page = reader.pages[i - 1]
                extracted = page.extract_text()
                if extracted and extracted.strip():
                    has_any_text = True
                    pages_text.append(f"--- Página {i}/{num_pages} ---\n{extracted.strip()}")
                else:
                    pages_text.append(f"--- Página {i}/{num_pages} ---\n[Sem texto legível nesta página]")

            full_text = "\n\n".join(pages_text)

            if not has_any_text:
                return (
                    f"[Aviso: O PDF '{path_obj.name}' (páginas {s_page} a {e_page} de {num_pages}) foi lido, "
                    f"mas nenhum texto legível foi extraído. O documento pode conter apenas imagens ou páginas digitalizadas]"
                )

            return full_text
    except Exception as e:
        return f"[Erro ao ler PDF '{clean_path}': {str(e)}]"

def pdf_tool(file_path: str, start_page: int = 1, end_page: Optional[int] = None) -> str:
    """
    Ferramenta do agente para ler e analisar o conteúdo de arquivos PDF locais.
    Aceita start_page e end_page opcionais para focar em capítulos ou seções específicas.
    """
    from pathlib import Path
    result = extract_pdf_text(file_path, start_page=start_page, end_page=end_page)
    if result.startswith("[Erro"):
        return f"[ERRO] {result}"
    elif result.startswith("[Aviso") or result.startswith("[INFO]"):
        return result

    file_name = Path(str(file_path).strip().strip("'\"")).name
    page_info = f" (páginas {start_page} a {end_page})" if end_page else ""
    return f"[SUCCESS] PDF '{file_name}'{page_info} lido com sucesso!\n\n{result}"

# =====================================================================
# FERRAMENTAS E UTILITÁRIOS: TEXT-TO-SPEECH (TTS)
# =====================================================================

def tts_tool(text: str, voice: str = "francisca", title: str = "audio_resumo") -> str:
    """
    Sintetiza texto em áudio MP3 de alta fidelidade usando vozes neurais brasileiras (edge-tts).
    """
    try:
        from meu_agente_cli import tts
        import os
        from pathlib import Path
        output_path = tts.synthesize_speech(text=text, voice=voice, title=title)
        size_bytes = os.path.getsize(output_path)
        size_kb = size_bytes / 1024
        file_name = Path(output_path).name
        return (
            f"[SUCCESS] Áudio narrado gerado com sucesso!\n"
            f"• Arquivo: {file_name}\n"
            f"• Caminho: {output_path}\n"
            f"• Tamanho: {size_kb:.1f} KB\n"
            f"• Voz utilizada: {voice.capitalize()}\n"
            f"O arquivo de áudio está pronto e disponível para reprodução ou envio direto."
        )
    except Exception as e:
        return f"[ERRO] Falha ao sintetizar áudio via TTS: {str(e)}"

# =====================================================================
# FERRAMENTAS E UTILITÁRIOS: GESTÃO DE AGENTES (META-AGENTE)
# =====================================================================

def manage_agents_tool(
    action: str,
    slug: Optional[str] = None,
    name: Optional[str] = None,
    icon: Optional[str] = None,
    description: Optional[str] = None,
    system_prompt: Optional[str] = None,
    instruction: Optional[str] = None
) -> str:
    """
    Ferramenta para o Meta-Agente listar, consultar, criar, atualizar e aprimorar agentes especializados.
    """
    from meu_agente_cli import db, llm
    action_clean = str(action).strip().lower()

    if action_clean == "list":
        agents = db.list_agents()
        active = db.get_active_agent_slug()
        lines = [f"📋 Agentes Cadastrados ({len(agents)} disponíveis):"]
        for a in agents:
            status = " [ATIVO ⭐]" if a["slug"] == active else ""
            lines.append(f"• {a['icon']} **{a['name']}** (`{a['slug']}`){status}: {a['description']}")
        return "\n".join(lines)

    elif action_clean in ("get_info", "info"):
        if not slug:
            return "[ERRO] Parâmetro 'slug' é obrigatório para consultar informações do agente."
        agent = db.get_agent(slug)
        if not agent:
            return f"[ERRO] Agente '{slug}' não encontrado."
        active = db.get_active_agent_slug()
        status = " (ATIVO)" if agent["slug"] == active else ""
        return (
            f"ℹ️ **Ficha Técnica do Agente: {agent['name']}** (`{agent['slug']}`){status}\n"
            f"• Ícone: {agent['icon']}\n"
            f"• Descrição: {agent['description']}\n"
            f"• Padrão do Sistema: {'Sim' if agent['is_default'] else 'Não'}\n"
            f"• Prompt de Sistema:\n```markdown\n{agent['system_prompt']}\n```"
        )

    elif action_clean == "create":
        if not slug or not name or not system_prompt:
            return "[ERRO] Para criar um agente, 'slug', 'name' e 'system_prompt' são obrigatórios."
        success = db.create_or_update_agent(
            slug=slug,
            name=name,
            icon=icon or "🤖",
            description=description or "",
            system_prompt=system_prompt,
            is_default=False
        )
        if success:
            return f"[SUCCESS] Agente '{name}' (`{slug}`) criado com sucesso! Use `/agent use {slug}` para ativá-lo."
        else:
            return f"[ERRO] Falha ao cadastrar o agente '{slug}' no banco de dados."

    elif action_clean in ("improve", "refine", "update_prompt"):
        if not slug:
            return "[ERRO] Parâmetro 'slug' é obrigatório para aprimorar o agente."
        agent = db.get_agent(slug)
        if not agent:
            return f"[ERRO] Agente '{slug}' não encontrado."
            
        current_prompt = agent["system_prompt"]
        
        if system_prompt:
            new_prompt = system_prompt
        elif instruction:
            meta_prompt = (
                f"Você é um Engenheiro de Prompts e Arquiteto de Agentes especialista.\n"
                f"Você precisa aprimorar o System Prompt de um agente chamado '{agent['name']}' ({agent['slug']}).\n\n"
                f"--- PROMPT ATUAL ---\n{current_prompt}\n\n"
                f"--- INSTRUÇÃO DE MELHORIA DO USUÁRIO ---\n{instruction}\n\n"
                f"Reescreva o System Prompt completo do agente incorporando de forma harmoniosa e profissional a melhoria solicitada.\n"
                f"Retorne EXCLUSIVAMENTE o novo texto do prompt completo, sem blocos de código ou explicações adicionais."
            )
            model = db.get_setting("active_model", "google/gemma-4-31b-qat")
            messages = [{"role": "system", "content": meta_prompt}, {"role": "user", "content": "Refine o prompt agora."}]
            try:
                new_prompt = llm.chat_completion(model, messages, stream=False).strip()
            except Exception as e:
                return f"[ERRO] Falha ao gerar melhoria de prompt via LLM: {e}"
        else:
            return "[ERRO] Forneça 'instruction' (o que deseja melhorar) ou 'system_prompt' (o prompt atualizado)."

        success = db.create_or_update_agent(
            slug=slug,
            name=name or agent["name"],
            icon=icon or agent["icon"],
            description=description or agent["description"],
            system_prompt=new_prompt,
            is_default=agent["is_default"]
        )
        if success:
            return (
                f"[SUCCESS] Agente '{agent['name']}' (`{slug}`) atualizado e aprimorado com sucesso!\n\n"
                f"Novo Prompt:\n```markdown\n{new_prompt}\n```"
            )
        else:
            return f"[ERRO] Falha ao salvar a atualização do agente '{slug}'."

    elif action_clean == "delete":
        if not slug:
            return "[ERRO] Parâmetro 'slug' é obrigatório para exclusão."
        if db.delete_agent(slug):
            return f"[SUCCESS] Agente '{slug}' excluído com sucesso."
        else:
            return f"[ERRO] Não foi possível excluir o agente '{slug}' (ele pode ser um agente protegido do sistema ou não existir)."

    return f"[ERRO] Ação '{action}' desconhecida para manage_agents_tool. Use: list | get_info | create | improve | delete."

def transcribe_audio_tool(file_path: str, context_length: Optional[int] = 2500) -> str:
    """
    Ferramenta de transcrição de áudio (PT-BR).
    Transcreve arquivos de áudio (M4A, MP3, WAV, etc.), salva a transcrição
    completa em um arquivo .txt e retorna o texto limitado ao context_length.
    """
    from pathlib import Path
    from meu_agente_cli.speech_to_text import transcrever_arquivo_audio
    
    p = Path(file_path)
    if not p.exists():
        return f"[ERRO] Arquivo de áudio não encontrado: {file_path}"
        
    transcription = transcrever_arquivo_audio(str(p))
    if not transcription or not transcription.strip():
        return "[AVISO] Não foi possível extrair falas audíveis do arquivo."

    # Salva a transcrição completa em arquivo texto (.txt)
    output_txt = p.parent / f"{p.stem}_transcricao.txt"
    try:
        with open(output_txt, "w", encoding="utf-8") as f:
            f.write(transcription)
    except Exception as e:
        return f"[ERRO] Falha ao salvar transcrição em arquivo texto: {e}"

    # Limita o retorno conforme o tamanho de contexto definido
    retorno_texto = transcription
    if context_length and len(transcription) > context_length:
        retorno_texto = transcription[:context_length] + f"\n\n... [Texto truncado para caber no contexto. Transcrição completa em: {output_txt}]"

    return (
        f"[SUCCESS] Áudio transcrito com sucesso!\n"
        f"📄 Arquivo salvo: {output_txt}\n\n"
        f"Transcrição:\n{retorno_texto}"
    )
