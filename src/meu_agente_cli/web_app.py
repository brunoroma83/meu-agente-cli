import os
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, Form, Response, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

import meu_agente_cli.db as db
import meu_agente_cli.invest as invest
import meu_agente_cli.security as security
from meu_agente_cli.mcp_server import create_mcp_sse_app, MCPAuthMiddleware

logger = logging.getLogger("web_app")

# Inicialização da aplicação FastAPI
app = FastAPI(
    title="Meu Financeiro",
    description="Serviço Integrado de Gestão Financeira com Web UI Leve e MCP Server",
    version="0.2.0"
)

# Configuração dos Templates Jinja2
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Montagem do MCP Server (SSE)
mcp_sse_subapp = create_mcp_sse_app()
app.mount("/mcp", mcp_sse_subapp)
for mcp_route in mcp_sse_subapp.routes:
    app.routes.append(mcp_route)

# =====================================================================
# AUTENTICAÇÃO E CONTROLE DE SESSÃO WEB
# =====================================================================

COOKIE_NAME = "finance_session"

def get_current_user(request: Request) -> Optional[dict]:
    """Extrai e valida o usuário a partir do cookie de sessão assinado."""
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    payload = security.verify_session_token(cookie)
    return payload

class WebAuthMiddleware:
    """
    Middleware ASGI puro que intercepta requisições web.
    Se o usuário não estiver autenticado e tentar acessar uma rota protegida,
    redireciona para a tela de login.
    Rotas públicas: /login, /static, /mcp, /sse, /messages, /openapi.json, /docs
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        public_prefixes = ("/login", "/static", "/mcp", "/sse", "/messages", "/openapi.json", "/docs")
        if any(path.startswith(prefix) for prefix in public_prefixes):
            return await self.app(scope, receive, send)

        request = Request(scope)
        user = get_current_user(request)
        if not user:
            response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
            return await response(scope, receive, send)

        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["user"] = user
        return await self.app(scope, receive, send)

app.add_middleware(WebAuthMiddleware)
app.add_middleware(MCPAuthMiddleware)

def format_date_month_input(now: datetime) -> str:
    return now.strftime("%Y-%m")

def parse_month_year(mes_ano_input: Optional[str]) -> tuple[int, int, str]:
    """Retorna (mes, ano, 'MM-YYYY') a partir de entrada 'YYYY-MM' ou atual."""
    now = datetime.now()
    if mes_ano_input and "-" in mes_ano_input:
        parts = mes_ano_input.split("-")
        try:
            if len(parts[0]) == 4:  # Formato YYYY-MM do input type="month"
                y, m = int(parts[0]), int(parts[1])
            else:  # Formato MM-YYYY
                m, y = int(parts[0]), int(parts[1])
            return m, y, f"{m:02d}-{y}"
        except ValueError:
            pass
    return now.month, now.year, f"{now.month:02d}-{now.year}"


# =====================================================================
# ROTAS PÚBLICAS / AUTENTICAÇÃO
# =====================================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})

@app.post("/login")
async def login_action(request: Request, username: str = Form(...), password: str = Form(...)):
    user = db.authenticate_user(username, password)
    if not user:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Usuário ou senha incorretos. Verifique suas credenciais."},
            status_code=400
        )
        
    session_token = security.create_session_token(
        user_name=user["user_name"],
        display_name=user["display_name"],
        role=user["role"]
    )
    
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_token,
        httponly=True,
        max_age=86400 * 30,
        samesite="lax",
        secure=False  # Pode ser True em HTTPS com proxy
    )
    return response

@app.get("/logout")
async def logout_action():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    return response

@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


# =====================================================================
# DASHBOARD
# =====================================================================

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_view(request: Request, mes_ano: Optional[str] = None, user_name: Optional[str] = None):
    user = request.state.user
    m, y, mes_ano_db = parse_month_year(mes_ano)
    mes_ano_input = f"{y:04d}-{m:02d}"
    
    # Filtro ativo
    u_filter = user_name.strip().lower() if user_name and user_name.strip() else None
    
    # Consultas de dados
    summary = db.get_financial_summary(month_year=mes_ano_db, user_name=u_filter)
    budget = db.get_daily_budget_summary(month_year=mes_ano_db, user_name=u_filter)
    categories_data = db.get_expenses_by_category(month_year=mes_ano_db, user_name=u_filter)
    monthly_history = db.get_monthly_overview(year=y, user_name=u_filter)
    monthly_bills = db.get_monthly_bills(month_year=mes_ano_db, user_name=u_filter)
    
    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "user": user,
        "current_page": "dashboard",
        "active_user_filter": u_filter or "",
        "mes_ano_input": mes_ano_input,
        "current_year": y,
        "summary": summary,
        "budget": budget,
        "categories_data": categories_data,
        "monthly_history": monthly_history,
        "monthly_bills": monthly_bills
    })


# =====================================================================
# CONTAS DO MÊS
# =====================================================================

@app.get("/contas", response_class=HTMLResponse)
async def contas_view(request: Request, mes_ano: Optional[str] = None, user_name: Optional[str] = None):
    user = request.state.user
    m, y, mes_ano_db = parse_month_year(mes_ano)
    mes_ano_input = f"{y:04d}-{m:02d}"
    u_filter = user_name.strip().lower() if user_name and user_name.strip() else None
    
    bills = db.get_monthly_bills(month_year=mes_ano_db, user_name=u_filter)
    total_mes = sum(b["amount"] for b in bills)
    total_pago = sum(b["amount"] for b in bills if b["is_paid"])
    total_pendente = sum(b["amount"] for b in bills if not b["is_paid"])
    
    return templates.TemplateResponse(request=request, name="contas.html", context={
        "user": user,
        "current_page": "contas",
        "active_user_filter": u_filter or "",
        "mes_ano_input": mes_ano_input,
        "bills": bills,
        "total_mes": total_mes,
        "total_pago": total_pago,
        "total_pendente": total_pendente
    })

@app.post("/contas/toggle/{record_id}")
async def toggle_conta_action(request: Request, record_id: str, mes_ano: Optional[str] = None, user_name: Optional[str] = None):
    m, y, mes_ano_db = parse_month_year(mes_ano)
    u_filter = user_name.strip().lower() if user_name and user_name.strip() else None
    
    # Encontra o status atual para inverter
    bills = db.get_monthly_bills(month_year=mes_ano_db, user_name=u_filter)
    current_status = False
    for b in bills:
        if str(b["id"]) == str(record_id):
            current_status = b["is_paid"]
            break
            
    db.toggle_bill_paid(record_id, is_paid=not current_status, month_year=mes_ano_db)
    
    return RedirectResponse(
        url=f"/contas?mes_ano={y:04d}-{m:02d}&user_name={u_filter or ''}",
        status_code=status.HTTP_303_SEE_OTHER
    )

@app.post("/contas/add")
async def add_conta_action(
    request: Request,
    descricao: str = Form(...),
    valor: float = Form(...),
    data_vencimento: str = Form(...),
    categoria: str = Form("Outros"),
    user_name: str = Form("bruno"),
    mes_ano: Optional[str] = Form(None)
):
    m, y, _ = parse_month_year(mes_ano)
    
    # Registra como despesa do tipo 'monthly'
    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO financial_records (type, category, amount, description, due_date, date, nature, user_name, is_paid, active)
            VALUES ('despesa', %s, %s, %s, %s, %s, 'monthly', %s, FALSE, TRUE)
            """,
            (db.clean_string(categoria), float(valor), db.clean_string(descricao), data_vencimento, data_vencimento, user_name.strip().lower())
        )
    conn.commit()
    conn.close()
    
    return RedirectResponse(url=f"/contas?mes_ano={y:04d}-{m:02d}", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/contas/edit/{record_id}")
async def edit_conta_action(
    request: Request,
    record_id: str,
    descricao: str = Form(...),
    valor: float = Form(...),
    data_vencimento: str = Form(...),
    categoria: str = Form("Outros"),
    user_name: str = Form("bruno"),
    propagar_futuros: Optional[str] = Form(None),
    mes_ano: Optional[str] = Form(None)
):
    m, y, mes_ano_db = parse_month_year(mes_ano)
    propagate = (propagar_futuros == "on" or propagar_futuros == "true")
    db.update_monthly_bill(
        bill_id=record_id,
        new_amount=float(valor),
        new_due_date=data_vencimento,
        propagate_future=propagate,
        category=categoria,
        description=descricao,
        user_name=user_name.strip().lower(),
        month_year=mes_ano_db
    )
    return RedirectResponse(
        url=f"/contas?mes_ano={y:04d}-{m:02d}&user_name={user_name.strip().lower()}",
        status_code=status.HTTP_303_SEE_OTHER
    )

@app.post("/contas/projetar")
async def projetar_fixas_action(request: Request, mes_ano: Optional[str] = Form(None), user_name: Optional[str] = Form(None)):
    m, y, _ = parse_month_year(mes_ano)
    db.project_annual_fixed_expenses(year=y)
    return RedirectResponse(url=f"/contas?mes_ano={y:04d}-{m:02d}", status_code=status.HTTP_303_SEE_OTHER)


# =====================================================================
# CARTÕES DE CRÉDITO
# =====================================================================

@app.get("/cartoes", response_class=HTMLResponse)
async def cartoes_view(
    request: Request,
    cartao: Optional[str] = None,
    mes_ano: Optional[str] = None,
    user_name: Optional[str] = None,
    msg: Optional[str] = None,
    error: Optional[str] = None
):
    user = request.state.user
    m, y, mes_ano_db = parse_month_year(mes_ano)
    mes_ano_input = f"{y:04d}-{m:02d}"
    u_filter = user_name.strip().lower() if user_name and user_name.strip() else None
    
    available_cards = db.get_distinct_cards()
    selected_card = cartao if cartao and cartao in available_cards else "Todos"
    
    purchases = db.get_card_purchases(card_name=selected_card, month_year=mes_ano_db, user_name=u_filter)
    total_fatura = sum(p["amount"] for p in purchases)
    
    # Carrega todos os cartões cadastrados para a área de gestão
    registered_cards = db.list_credit_cards(active_only=False, user_name=u_filter)
    
    return templates.TemplateResponse(request=request, name="cartoes.html", context={
        "user": user,
        "current_page": "cartoes",
        "active_user_filter": u_filter or "",
        "mes_ano_input": mes_ano_input,
        "available_cards": available_cards,
        "selected_card": selected_card,
        "purchases": purchases,
        "total_fatura": total_fatura,
        "registered_cards": registered_cards,
        "feedback_msg": msg,
        "feedback_error": error
    })

@app.post("/cartoes/add")
async def add_compra_cartao_action(
    request: Request,
    cartao: str = Form(...),
    descricao: str = Form(...),
    valor: float = Form(...),
    parcelas: int = Form(1),
    categoria: str = Form("Outros"),
    data_compra: Optional[str] = Form(None),
    user_name: str = Form("bruno"),
    mes_ano: Optional[str] = Form(None),
    cartao_filtro: Optional[str] = Form(None)
):
    m, y, _ = parse_month_year(mes_ano)
    ok, msg = db.add_card_purchase(
        card_name=cartao,
        category=categoria,
        total_amount=float(valor),
        installments=int(parcelas),
        description=descricao,
        buy_date_str=data_compra,
        user_name=user_name.strip().lower()
    )
    url = f"/cartoes?cartao={cartao_filtro or cartao}&mes_ano={y:04d}-{m:02d}"
    if not ok:
        import urllib.parse
        url += f"&error={urllib.parse.quote(msg)}"
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

@app.post("/cartoes/manage/add")
async def create_card_action(
    request: Request,
    nome: str = Form(...),
    banco: str = Form(...),
    dia_vencimento: int = Form(...),
    dia_fechamento: Optional[int] = Form(None),
    user_name: str = Form("bruno")
):
    ok, msg = db.create_credit_card(
        name=nome,
        bank=banco,
        due_day=int(dia_vencimento),
        closing_day=int(dia_fechamento) if dia_fechamento else None,
        user_name=user_name.strip().lower()
    )
    import urllib.parse
    param = "msg" if ok else "error"
    return RedirectResponse(url=f"/cartoes?{param}={urllib.parse.quote(msg)}", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/cartoes/manage/edit/{card_id}")
async def edit_card_action(
    card_id: int,
    nome: str = Form(...),
    banco: str = Form(...),
    dia_vencimento: int = Form(...),
    dia_fechamento: Optional[int] = Form(None),
    user_name: str = Form("bruno"),
    active: Optional[str] = Form(None)
):
    is_active = (active == "on" or active == "true")
    ok, msg = db.update_credit_card(
        card_id=card_id,
        name=nome,
        bank=banco,
        due_day=int(dia_vencimento),
        closing_day=int(dia_fechamento) if dia_fechamento else None,
        user_name=user_name.strip().lower(),
        active=is_active
    )
    import urllib.parse
    param = "msg" if ok else "error"
    return RedirectResponse(url=f"/cartoes?{param}={urllib.parse.quote(msg)}", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/cartoes/manage/toggle/{card_id}")
async def toggle_card_action(card_id: int):
    ok, msg = db.toggle_credit_card_active(card_id)
    import urllib.parse
    param = "msg" if ok else "error"
    return RedirectResponse(url=f"/cartoes?{param}={urllib.parse.quote(msg)}", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/cartoes/manage/delete/{card_id}")
async def delete_card_action(card_id: int):
    ok, msg = db.delete_credit_card(card_id)
    import urllib.parse
    param = "msg" if ok else "error"
    return RedirectResponse(url=f"/cartoes?{param}={urllib.parse.quote(msg)}", status_code=status.HTTP_303_SEE_OTHER)


# =====================================================================
# RECEITAS & GASTOS DIÁRIOS
# =====================================================================

@app.get("/lancamentos", response_class=HTMLResponse)
async def lancamentos_view(request: Request, q: Optional[str] = None, mes_ano: Optional[str] = None, user_name: Optional[str] = None):
    user = request.state.user
    m, y, mes_ano_db = parse_month_year(mes_ano)
    mes_ano_input = f"{y:04d}-{m:02d}"
    u_filter = user_name.strip().lower() if user_name and user_name.strip() else None
    
    records = db.search_financial_records(
        limit=100,
        month_year=mes_ano_db,
        query=q,
        user_name=u_filter
    )
    
    # Transforma registros em formato amigável para template
    items = []
    for r in records:
        items.append({
            "id": r[0],
            "type": r[1],
            "category": r[2],
            "amount": float(r[3]),
            "description": r[4],
            "date": r[5],
            "due_date": r[6],
            "user_name": r[7] if len(r) > 7 else "bruno"
        })
        
    return templates.TemplateResponse(request=request, name="lancamentos.html", context={
        "user": user,
        "current_page": "lancamentos",
        "active_user_filter": u_filter or "",
        "mes_ano_input": mes_ano_input,
        "search_query": q or "",
        "records": items
    })

@app.post("/lancamentos/add")
async def add_lancamento_action(
    request: Request,
    tipo: str = Form("despesa"),
    categoria: str = Form("Outros"),
    valor: float = Form(...),
    descricao: str = Form(""),
    user_name: str = Form("bruno"),
    mes_ano: Optional[str] = Form(None)
):
    m, y, _ = parse_month_year(mes_ano)
    db.add_financial_record(
        record_type=tipo,
        category=categoria,
        amount=float(valor),
        description=descricao,
        user_name=user_name.strip().lower()
    )
    return RedirectResponse(url=f"/lancamentos?mes_ano={y:04d}-{m:02d}", status_code=status.HTTP_303_SEE_OTHER)
@app.post("/lancamentos/edit/{record_id}")
async def edit_lancamento_action(
    request: Request,
    record_id: int,
    tipo: str = Form(...),
    categoria: str = Form(...),
    valor: float = Form(...),
    descricao: str = Form(""),
    data: Optional[str] = Form(None),
    user_name: str = Form("bruno"),
    mes_ano: Optional[str] = Form(None)
):
    m, y, _ = parse_month_year(mes_ano)
    db.update_financial_record(
        record_id=record_id,
        amount=float(valor),
        description=descricao,
        category=categoria,
        record_type=tipo,
        due_date=data,
        date=data,
        user_name=user_name.strip().lower()
    )
    return RedirectResponse(
        url=f"/lancamentos?mes_ano={y:04d}-{m:02d}&user_name={user_name.strip().lower()}",
        status_code=status.HTTP_303_SEE_OTHER
    )

@app.post("/lancamentos/delete/{record_id}")
async def delete_lancamento_action(record_id: int, mes_ano: Optional[str] = None, user_name: Optional[str] = None):
    db.delete_financial_record(record_id)
    m, y, _ = parse_month_year(mes_ano)
    return RedirectResponse(
        url=f"/lancamentos?mes_ano={y:04d}-{m:02d}&user_name={user_name or ''}",
        status_code=status.HTTP_303_SEE_OTHER
    )


# =====================================================================
# INVESTIMENTOS (AÇÕES & RENDA FIXA)
# =====================================================================

@app.get("/investimentos", response_class=HTMLResponse)
async def investimentos_view(request: Request):
    user = request.state.user
    resumo = invest.get_resumo_patrimonial_geral()
    acoes = invest.get_consolidado_acoes(fetch_market_prices=False)
    renda_fixa = invest.get_consolidado_renda_fixa(active_only=True)
    
    return templates.TemplateResponse(request=request, name="investimentos.html", context={
        "user": user,
        "current_page": "investimentos",
        "active_user_filter": "",
        "resumo": resumo,
        "acoes": acoes,
        "renda_fixa": renda_fixa
    })

@app.post("/investimentos/add_acao")
async def add_acao_action(
    codigo_acao: str = Form(...),
    operacao: str = Form("COMPRA"),
    quantidade: int = Form(...),
    preco_unitario: float = Form(...),
    taxas: float = Form(0.0),
    data_operacao: Optional[str] = Form(None)
):
    invest.add_movimentacao_acao(
        codigo_acao=codigo_acao,
        operacao=operacao,
        quantidade=int(quantidade),
        preco_unitario=float(preco_unitario),
        taxas=float(taxas),
        data_operacao=data_operacao
    )
    return RedirectResponse(url="/investimentos", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/investimentos/add_rf")
async def add_rf_action(
    nome_titulo: str = Form(...),
    nome_banco: str = Form(...),
    tipo_investimento: str = Form("CDB"),
    valor_investido: float = Form(...),
    data_inicio: str = Form(...)
):
    invest.cadastrar_titulo_renda_fixa(
        nome_titulo=nome_titulo,
        nome_banco=nome_banco,
        tipo_investimento=tipo_investimento,
        data_inicio=data_inicio,
        valor_inicial=float(valor_investido)
    )
    return RedirectResponse(url="/investimentos", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/investimentos/movimentar_rf")
async def movimentar_rf_action(
    id_investimento: int = Form(...),
    tipo_movimentacao: str = Form(...),
    valor: float = Form(...),
    data_movimentacao: Optional[str] = Form(None)
):
    invest.add_movimentacao_renda_fixa(
        id_investimento=int(id_investimento),
        tipo_movimentacao=tipo_movimentacao,
        valor=float(valor),
        data_movimentacao=data_movimentacao
    )
    return RedirectResponse(url="/investimentos", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/investimentos/update_saldo_rf/{id_investimento}")
async def update_saldo_rf_action(
    id_investimento: int,
    novo_valor: float = Form(...)
):
    invest.update_valor_atual_renda_fixa(
        id_investimento=int(id_investimento),
        novo_valor=float(novo_valor)
    )
    return RedirectResponse(url="/investimentos", status_code=status.HTTP_303_SEE_OTHER)



# =====================================================================
# TOKENS MCP & AUDITORIA DE ACESSOS (HERMES)
# =====================================================================

@app.get("/tokens", response_class=HTMLResponse)
async def tokens_view(request: Request, new_raw_token: Optional[str] = None):
    user = request.state.user
    tokens = db.list_mcp_tokens()
    access_logs = db.get_mcp_access_logs(limit=50)
    
    return templates.TemplateResponse(request=request, name="tokens.html", context={
        "user": user,
        "current_page": "tokens",
        "active_user_filter": "",
        "tokens": tokens,
        "access_logs": access_logs,
        "new_raw_token": new_raw_token
    })

@app.post("/tokens/create")
async def create_token_action(request: Request, name: str = Form(...)):
    user = request.state.user
    ok, raw_token, msg = db.create_mcp_token_record(name=name, created_by=user.get("user_name", "bruno"))
    
    tokens = db.list_mcp_tokens()
    access_logs = db.get_mcp_access_logs(limit=50)
    
    return templates.TemplateResponse(request=request, name="tokens.html", context={
        "user": user,
        "current_page": "tokens",
        "active_user_filter": "",
        "tokens": tokens,
        "access_logs": access_logs,
        "new_raw_token": raw_token if ok else None
    })

@app.post("/tokens/revoke/{token_id}")
async def revoke_token_action(token_id: int):
    db.revoke_mcp_token(token_id)
    return RedirectResponse(url="/tokens", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/tokens/delete/{token_id}")
async def delete_token_action(token_id: int):
    db.delete_mcp_token(token_id)
    return RedirectResponse(url="/tokens", status_code=status.HTTP_303_SEE_OTHER)
