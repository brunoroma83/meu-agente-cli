import json
import logging
import functools
import contextvars
from typing import Optional, List, Dict, Any
from datetime import datetime

from mcp.server.mcpserver import MCPServer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.requests import Request

import meu_agente_cli.db as db
import meu_agente_cli.invest as invest

logger = logging.getLogger("mcp_server")

# Variáveis de contexto para rastrear o token e IP na requisição atual
current_mcp_token = contextvars.ContextVar("current_mcp_token", default=None)
current_client_ip = contextvars.ContextVar("current_client_ip", default="")

# Instância do servidor MCP
mcp_server = MCPServer(
    name="FinanceiroService",
    description="Servidor MCP para Gestão Financeira Pessoal e Familiar (Hermes Agent)"
)

def audit_tool(tool_name: str):
    """Decorator que registra a chamada de ferramenta no banco de auditoria (mcp_access_logs)."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(**kwargs):
            tok = current_mcp_token.get()
            ip = current_client_ip.get()
            t_id = tok.get("id") if tok else None
            t_name = tok.get("name") if tok else "Direto/CLI"
            
            try:
                res = func(**kwargs)
                db.log_mcp_access(
                    token_id=t_id,
                    token_name=t_name,
                    client_ip=ip,
                    tool_name=tool_name,
                    request_params=kwargs,
                    response_summary=res,
                    status="success"
                )
                return res
            except Exception as e:
                err_msg = str(e)
                logger.exception("Erro ao executar ferramenta MCP %s", tool_name)
                db.log_mcp_access(
                    token_id=t_id,
                    token_name=t_name,
                    client_ip=ip,
                    tool_name=tool_name,
                    request_params=kwargs,
                    response_summary=None,
                    status="error",
                    error_message=err_msg
                )
                return f"[ERRO] Falha ao executar {tool_name}: {err_msg}"
        return wrapper
    return decorator


# =====================================================================
# FERRAMENTAS EXPOSTAS PARA O HERMES
# =====================================================================

@mcp_server.tool(
    name="registrar_despesa",
    description="Registra uma nova despesa financeira. Parâmetros: valor (obrigatório, positivo), categoria, descricao, data_vencimento (YYYY-MM-DD, opcional) e user_name ('bruno' ou 'fabiana')."
)
@audit_tool("registrar_despesa")
def registrar_despesa(
    valor: float,
    categoria: str = "Outros",
    descricao: str = "",
    data_vencimento: Optional[str] = None,
    user_name: str = "bruno"
) -> str:
    if valor <= 0:
        return "[ERRO] O valor da despesa deve ser maior que zero."
    ok = db.add_financial_record(
        record_type="despesa",
        category=categoria,
        amount=valor,
        description=descricao,
        due_date=data_vencimento,
        user_name=user_name
    )
    if ok:
        return f"[SUCESSO] Despesa de R$ {valor:.2f} registrada para '{user_name.capitalize()}' na categoria '{categoria}'."
    return "[ERRO] Falha ao salvar despesa no banco de dados."


@mcp_server.tool(
    name="registrar_receita",
    description="Registra uma nova receita financeira. Parâmetros: valor (obrigatório, positivo), categoria, descricao, data (YYYY-MM-DD, opcional) e user_name ('bruno' ou 'fabiana')."
)
@audit_tool("registrar_receita")
def registrar_receita(
    valor: float,
    categoria: str = "Salário",
    descricao: str = "",
    data: Optional[str] = None,
    user_name: str = "bruno"
) -> str:
    if valor <= 0:
        return "[ERRO] O valor da receita deve ser maior que zero."
    ok = db.add_financial_record(
        record_type="receita",
        category=categoria,
        amount=valor,
        description=descricao,
        due_date=data,
        user_name=user_name
    )
    if ok:
        return f"[SUCESSO] Receita de R$ {valor:.2f} registrada para '{user_name.capitalize()}' na categoria '{categoria}'."
    return "[ERRO] Falha ao salvar receita no banco de dados."


@mcp_server.tool(
    name="registrar_despesas_lote",
    description="Registra múltiplos lançamentos financeiros de uma só vez (ex: notas fiscais ou extrato importado). Recebe uma lista de itens, onde cada item tem: type ('despesa' ou 'receita'), amount, category, description, due_date (opcional) e user_name (opcional)."
)
@audit_tool("registrar_despesas_lote")
def registrar_despesas_lote(itens: List[Dict[str, Any]], user_name: str = "bruno") -> str:
    if not itens:
        return "[ERRO] A lista de lançamentos em lote está vazia."
    ok = db.add_financial_records_bulk(itens, default_user_name=user_name)
    if ok:
        return f"[SUCESSO] {len(itens)} lançamentos registrados em lote com sucesso."
    return "[ERRO] Falha ao registrar lançamentos em lote no banco."


@mcp_server.tool(
    name="comprar_no_cartao",
    description="Registra uma compra no cartão de crédito, calculando automaticamente as parcelas e faturas futuras. Parâmetros: cartao (nome do cartão, ex: 'BB', 'Itaú', 'Porto'), valor, parcelas (default 1), categoria, descricao, data_compra (YYYY-MM-DD, opcional) e user_name ('bruno' ou 'fabiana')."
)
@audit_tool("comprar_no_cartao")
def comprar_no_cartao(
    cartao: str,
    valor: float,
    parcelas: int = 1,
    categoria: str = "Outros",
    descricao: str = "",
    data_compra: Optional[str] = None,
    user_name: str = "bruno"
) -> str:
    if valor <= 0:
        return "[ERRO] O valor deve ser maior que zero."
    ok = db.add_card_purchase(
        card_name=cartao,
        category=categoria,
        total_amount=valor,
        installments=max(1, parcelas),
        description=descricao,
        buy_date_str=data_compra,
        user_name=user_name
    )
    if ok:
        return f"[SUCESSO] Compra de R$ {valor:.2f} ({parcelas}x) registrada no cartão '{cartao}' para '{user_name.capitalize()}'."
    return f"[ERRO] Falha ao registrar compra no cartão '{cartao}'."


@mcp_server.tool(
    name="consultar_extrato",
    description="Consulta lançamentos financeiros ativos com filtros opcionais: mes_ano ('MM-YYYY'), categoria, termo_busca, tipo ('receita' ou 'despesa'), user_name ('bruno', 'fabiana' ou vazio para ver familiar/ambos) e limite (default 50)."
)
@audit_tool("consultar_extrato")
def consultar_extrato(
    mes_ano: Optional[str] = None,
    categoria: Optional[str] = None,
    termo_busca: Optional[str] = None,
    tipo: Optional[str] = None,
    user_name: Optional[str] = None,
    limite: int = 50
) -> str:
    records = db.search_financial_records(
        limit=limite,
        month_year=mes_ano,
        query=termo_busca,
        record_type=tipo,
        category=categoria,
        user_name=user_name
    )
    if not records:
        return "Nenhum lançamento financeiro encontrado com os critérios fornecidos."
    
    linhas = []
    linhas.append(f"Extrato Financeiro ({len(records)} encontrados):")
    for r in records:
        rid, rtype, cat, val, desc, dt, due, owner = r
        dt_str = due.strftime("%d/%m/%Y") if due else (dt.strftime("%d/%m/%Y") if dt else "-")
        linhas.append(f"- #{rid} [{dt_str}] ({owner.capitalize()}) {rtype.upper()}: R$ {val:.2f} | {cat} - {desc}")
    return "\n".join(linhas)


@mcp_server.tool(
    name="obter_resumo_financeiro",
    description="Retorna o resumo consolidado de receitas, despesas e saldo do mês. Parâmetros opcionais: mes_ano ('MM-YYYY') e user_name ('bruno', 'fabiana' ou None para visão familiar completa)."
)
@audit_tool("obter_resumo_financeiro")
def obter_resumo_financeiro(mes_ano: Optional[str] = None, user_name: Optional[str] = None) -> str:
    summary = db.get_financial_summary(month_year=mes_ano, user_name=user_name)
    titular_str = f" do titular '{user_name.capitalize()}'" if user_name else " Geral (Familiar)"
    periodo_str = f" no período {mes_ano}" if mes_ano else " (Geral)"
    return (
        f"Resumo Financeiro{titular_str}{periodo_str}:\n"
        f"- Total de Receitas: R$ {summary['receitas']:.2f}\n"
        f"- Total de Despesas: R$ {summary['despesas']:.2f}\n"
        f"- Saldo Líquido: R$ {summary['saldo']:.2f}"
    )


@mcp_server.tool(
    name="listar_contas_mes",
    description="Lista as contas fixas e faturas de cartão do mês especificado (MM-YYYY). Permite saber o que já está pago e o que está pendente, filtrando por titular se desejado."
)
@audit_tool("listar_contas_mes")
def listar_contas_mes(mes_ano: Optional[str] = None, user_name: Optional[str] = None) -> str:
    bills = db.get_monthly_bills(month_year=mes_ano, user_name=user_name)
    if not bills:
        return "Nenhuma conta fixa ou fatura encontrada para o mês informado."
    
    tot_pendente = sum(b["amount"] for b in bills if not b["is_paid"])
    tot_pago = sum(b["amount"] for b in bills if b["is_paid"])
    
    linhas = [f"Contas e Faturas do Mês ({mes_ano or 'Atual'}):"]
    linhas.append(f"Total Pago: R$ {tot_pago:.2f} | Total Pendente: R$ {tot_pendente:.2f}\n")
    
    for b in bills:
        status_ico = "✅ PAGO" if b["is_paid"] else "⏳ PENDENTE"
        dt_venc = b["due_date"].strftime("%d/%m/%Y") if hasattr(b.get("due_date"), "strftime") else str(b.get("due_date") or "-")
        owner = b.get("user_name", "bruno").capitalize()
        linhas.append(f"- ID {b['id']} | [{status_ico}] Venc: {dt_venc} | R$ {b['amount']:.2f} | {b['name']} ({owner})")
    
    return "\n".join(linhas)


@mcp_server.tool(
    name="marcar_conta_paga",
    description="Marca uma conta fixa ou fatura de cartão como paga ou pendente. Parâmetros: record_id (o ID numérico ou 'card_NOME') e is_paid (True para paga, False para pendente)."
)
@audit_tool("marcar_conta_paga")
def marcar_conta_paga(record_id: str, is_paid: bool = True, mes_ano: Optional[str] = None) -> str:
    ok = db.toggle_bill_paid(record_id, is_paid=is_paid, month_year=mes_ano)
    estado = "Paga" if is_paid else "Pendente"
    if ok:
        return f"[SUCESSO] Conta/Fatura #{record_id} marcada como '{estado}'."
    return f"[ERRO] Falha ao atualizar o status da conta #{record_id}."


@mcp_server.tool(
    name="obter_orcamento_diario",
    description="Consulta o orçamento diário (teto de gastos do dia restante) para o mês atual ou especificado. Parâmetros: mes_ano ('MM-YYYY') e user_name ('bruno', 'fabiana' ou None)."
)
@audit_tool("obter_orcamento_diario")
def obter_orcamento_diario(mes_ano: Optional[str] = None, user_name: Optional[str] = None) -> str:
    b = db.get_daily_budget_summary(month_year=mes_ano, user_name=user_name)
    titular_str = f" de {user_name.capitalize()}" if user_name else " Familiar"
    return (
        f"Orçamento Diário{titular_str}:\n"
        f"- Receitas do Mês: R$ {b['receitas_mes']:.2f}\n"
        f"- Custos Fixos: R$ {b['custos_fixos_mes']:.2f}\n"
        f"- Gastos Diários Realizados: R$ {b['gastos_diarios_mes']:.2f}\n"
        f"- Saldo Livre Restante: R$ {b['saldo_livre_restante']:.2f}\n"
        f"- Dias Restantes no Mês: {b['dias_restantes']}\n"
        f"- Teto Recomendado por Dia: R$ {b['teto_diario']:.2f}\n"
        f"- Gasto Realizado Hoje: R$ {b['gasto_hoje']:.2f} (Status: {b['status_hoje'].upper()})"
    )


@mcp_server.tool(
    name="consultar_investimentos",
    description="Consulta a carteira de investimentos (Ações e Renda Fixa) com resumo patrimonial e rentabilidade."
)
@audit_tool("consultar_investimentos")
def consultar_investimentos(tipo: str = "todos") -> str:
    tipo_clean = tipo.strip().lower()
    resumo = invest.get_resumo_patrimonial_geral()
    linhas = [
        "Resumo Patrimonial de Investimentos:",
        f"- Renda Fixa: R$ {resumo.get('valor_atual_rf', 0.0):.2f}",
        f"- Ações (Custódia): R$ {resumo.get('valor_atual_acoes', 0.0):.2f}",
        f"- Total Consolidado: R$ {resumo.get('valor_atual', 0.0):.2f}\n"
    ]
    
    if tipo_clean in ("acoes", "todos"):
        try:
            acoes = invest.get_consolidado_acoes(fetch_market_prices=False)
            if acoes:
                linhas.append("Ações em Custódia:")
                for a in acoes:
                    linhas.append(f"- ID/Ticker {a['codigo_acao']}: Qtd {a['quantidade_custodia']} | Preço Médio R$ {a['preco_medio']:.2f} | Valor Mercado R$ {a.get('valor_mercado', 0.0):.2f}")
        except Exception as e:
            linhas.append(f"(Não foi possível listar detalhes de ações: {e})")
            
    if tipo_clean in ("renda_fixa", "rf", "todos"):
        try:
            rf = invest.get_consolidado_renda_fixa(active_only=True)
            if rf:
                linhas.append("\nTítulos de Renda Fixa:")
                for t in rf:
                    linhas.append(f"- #{t['id']} {t['nome_titulo']} ({t['nome_banco']}): R$ {t['valor_atual']:.2f} (Rentabilidade: {t.get('rentabilidade_pct', 0.0):.2f}%)")
        except Exception as e:
            linhas.append(f"(Não foi possível listar detalhes de renda fixa: {e})")
            
    return "\n".join(linhas)


@mcp_server.tool(
    name="atualizar_registro_financeiro",
    description="Altera os dados de um lançamento financeiro existente (gasto diário, receita ou compra). Útil quando o usuário corrigir o valor de uma despesa, trocar o titular ('bruno' ou 'fabiana'), mudar a categoria, data ou descrição. Parâmetros: record_id (int, obrigatório), novo_valor (float, opcional), nova_descricao (str, opcional), nova_categoria (str, opcional), nova_data (str, opcional YYYY-MM-DD), novo_titular (str, opcional 'bruno' ou 'fabiana')."
)
@audit_tool("atualizar_registro_financeiro")
def atualizar_registro_financeiro(
    record_id: int,
    novo_valor: Optional[float] = None,
    nova_descricao: Optional[str] = None,
    nova_categoria: Optional[str] = None,
    nova_data: Optional[str] = None,
    novo_titular: Optional[str] = None
) -> str:
    existente = db.get_financial_record_by_id(record_id)
    if not existente:
        return f"[ERRO] Registro financeiro #{record_id} não encontrado ou inativo."
        
    ok = db.update_financial_record(
        record_id=record_id,
        description=nova_descricao,
        category=nova_categoria,
        amount=novo_valor,
        date=nova_data,
        due_date=nova_data,
        user_name=novo_titular
    )
    if ok:
        detalhes = []
        if novo_valor is not None: detalhes.append(f"Valor: R$ {novo_valor:.2f}")
        if nova_descricao is not None: detalhes.append(f"Descrição: '{nova_descricao}'")
        if nova_categoria is not None: detalhes.append(f"Categoria: '{nova_categoria}'")
        if nova_data is not None: detalhes.append(f"Data: '{nova_data}'")
        if novo_titular is not None: detalhes.append(f"Titular: '{novo_titular.capitalize()}'")
        return f"[SUCESSO] Registro #{record_id} atualizado com sucesso! Alterações: {', '.join(detalhes)}."
    return f"[ERRO] Falha ao atualizar registro financeiro #{record_id}."


@mcp_server.tool(
    name="atualizar_conta_mensal",
    description="Altera o valor e/ou vencimento de uma conta mensal fixa ou fatura. Permite atualizar apenas o mês atual ou propagar o reajuste para todos os meses seguintes do ano. Parâmetros: record_id (int, obrigatório), novo_valor (float, obrigatório), novo_vencimento (str, opcional YYYY-MM-DD), propagar_meses_futuros (bool, opcional padrão False), novo_titular (str, opcional 'bruno' ou 'fabiana')."
)
@audit_tool("atualizar_conta_mensal")
def atualizar_conta_mensal(
    record_id: int,
    novo_valor: float,
    novo_vencimento: Optional[str] = None,
    propagar_meses_futuros: bool = False,
    novo_titular: Optional[str] = None
) -> str:
    existente = db.get_financial_record_by_id(record_id)
    if not existente:
        return f"[ERRO] Conta #{record_id} não encontrada ou inativa."
        
    ok = db.update_monthly_bill(
        record_id=record_id,
        new_amount=novo_valor,
        new_due_date=novo_vencimento,
        propagate_future=propagar_meses_futuros,
        user_name=novo_titular
    )
    if ok:
        msg = f"[SUCESSO] Conta #{record_id} atualizada para R$ {novo_valor:.2f}."
        if propagar_meses_futuros:
            msg += " O novo valor foi propagado para os meses posteriores deste ano."
        return msg
    return f"[ERRO] Falha ao atualizar conta #{record_id}."


@mcp_server.tool(
    name="movimentar_renda_fixa",
    description="Registra uma nova movimentação em um título de Renda Fixa existente. Parâmetros: id_investimento (int, obrigatório), tipo_movimentacao (str, obrigatório: 'APORTE', 'RESGATE', 'JUROS_RECEBIDOS' ou 'IMPOSTO'), valor (float, positivo), data (str, opcional YYYY-MM-DD)."
)
@audit_tool("movimentar_renda_fixa")
def movimentar_renda_fixa(
    id_investimento: int,
    tipo_movimentacao: str,
    valor: float,
    data: Optional[str] = None
) -> str:
    ok, msg = invest.add_movimentacao_renda_fixa(
        id_investimento=id_investimento,
        tipo_movimentacao=tipo_movimentacao,
        valor=valor,
        data_movimentacao=data
    )
    return f"[SUCESSO] {msg}" if ok else f"[ERRO] {msg}"


@mcp_server.tool(
    name="atualizar_saldo_renda_fixa",
    description="Atualiza a cotação/saldo de mercado atual de um título de Renda Fixa (ex: atualizar o saldo consolidado do Tesouro Direto ou CDB conforme o extrato bancário). Parâmetros: id_investimento (int, obrigatório), novo_valor (float, obrigatório, maior ou igual a zero)."
)
@audit_tool("atualizar_saldo_renda_fixa")
def atualizar_saldo_renda_fixa(
    id_investimento: int,
    novo_valor: float
) -> str:
    ok, msg = invest.update_valor_atual_renda_fixa(
        id_investimento=id_investimento,
        novo_valor=novo_valor
    )
    return f"[SUCESSO] {msg}" if ok else f"[ERRO] {msg}"


# =====================================================================
# MIDDLEWARE DE AUTENTICAÇÃO DO MCP SERVER
# =====================================================================

class MCPAuthMiddleware:
    """
    Middleware ASGI puro que protege todas as rotas do MCP (SSE e Messages).
    Verifica o token enviado via header 'Authorization: Bearer <TOKEN>'
    ou parâmetro de URL '?token=<TOKEN>'.
    Valida no banco de dados se o token existe e está ativo.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        if not (path.startswith("/sse") or path.startswith("/messages") or path.startswith("/mcp")):
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("latin1")
        token_candidate = ""
        if auth_header.startswith("Bearer "):
            token_candidate = auth_header[7:].strip()
        else:
            from urllib.parse import parse_qs
            query_string = scope.get("query_string", b"").decode("latin1")
            params = parse_qs(query_string)
            if "token" in params and params["token"]:
                token_candidate = params["token"][0].strip()

        client = scope.get("client")
        client_ip = client[0] if client else "127.0.0.1"
        current_client_ip.set(client_ip)

        token_info = db.validate_mcp_token(token_candidate) if token_candidate else None
        if not token_info:
            logger.warning("Acesso não autorizado ao MCP vindo de %s para %s", client_ip, path)
            response = JSONResponse(
                {"error": "Unauthorized: Token MCP inválido ou não fornecido. Use 'Authorization: Bearer <TOKEN>' ou '?token=<TOKEN>'."},
                status_code=401
            )
            return await response(scope, receive, send)

        current_mcp_token.set(token_info)
        return await self.app(scope, receive, send)


from mcp.server.transport_security import TransportSecuritySettings

def create_mcp_sse_app():
    """Retorna a aplicação Starlette SSE com o middleware de segurança ativo."""
    app = mcp_server.sse_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    app.add_middleware(MCPAuthMiddleware)
    return app
