import asyncio
import pytest
from starlette.testclient import TestClient

import meu_agente_cli.security as sec
from meu_agente_cli.web_app import app
from meu_agente_cli.mcp_server import mcp_server

def test_password_hashing():
    pwd = "minhasenhasegura123"
    hashed = sec.hash_password(pwd)
    assert hashed.startswith("pbkdf2_sha256$")
    assert sec.verify_password(pwd, hashed) is True
    assert sec.verify_password("senhaerrada", hashed) is False

def test_mcp_token_generation():
    raw_token, token_hash, prefix = sec.generate_mcp_token()
    assert raw_token.startswith("mcp_live_")
    assert sec.hash_token(raw_token) == token_hash
    assert len(token_hash) == 64  # SHA-256 hex length

def test_session_token_serializer():
    token = sec.create_session_token(user_name="bruno", display_name="Bruno Roma", role="admin")
    payload = sec.verify_session_token(token)
    assert payload is not None
    assert payload["user_name"] == "bruno"
    assert payload["display_name"] == "Bruno Roma"
    assert payload["role"] == "admin"

def test_mcp_server_tools_registered():
    tools = asyncio.run(mcp_server.list_tools())
    tool_names = [t.name for t in tools]
    expected_tools = [
        "registrar_despesa",
        "registrar_receita",
        "registrar_despesas_lote",
        "comprar_no_cartao",
        "consultar_extrato",
        "obter_resumo_financeiro",
        "listar_contas_mes",
        "marcar_conta_paga",
        "obter_orcamento_diario",
        "consultar_investimentos",
        "atualizar_registro_financeiro",
        "atualizar_conta_mensal",
        "movimentar_renda_fixa",
        "atualizar_saldo_renda_fixa",
    ]
    for expected in expected_tools:
        assert expected in tool_names

def test_web_app_login_redirect():
    client = TestClient(app)
    # Acesso a rota protegida sem cookie redireciona para /login
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    # Rota de login deve retornar 200 HTML
    login_res = client.get("/login")
    assert login_res.status_code == 200
    assert "Meu Financeiro" in login_res.text

def test_web_app_login_failure(monkeypatch):
    client = TestClient(app)
    import meu_agente_cli.db as db
    monkeypatch.setattr(db, "authenticate_user", lambda u, p: None)
    
    response = client.post("/login", data={"username": "wrong", "password": "wrong"})
    assert response.status_code == 400
    assert "Usuário ou senha incorretos" in response.text

def test_web_app_authenticated_dashboard(monkeypatch):
    client = TestClient(app)
    import meu_agente_cli.db as db
    monkeypatch.setattr(db, "get_financial_summary", lambda **kw: {"receitas": 5000.0, "despesas": 4000.0, "saldo": 1000.0})
    mock_budget = {
        "receitas_mes": 5000.0,
        "custos_fixos_mes": 2000.0,
        "saldo_livre_mes": 3000.0,
        "gastos_diarios_mes": 1000.0,
        "saldo_livre_restante": 2000.0,
        "gasto_hoje": 50.0,
        "dias_totais_mes": 30,
        "dias_restantes": 20,
        "teto_diario": 100.0,
        "status_hoje": "ok"
    }
    monkeypatch.setattr(db, "get_daily_budget_summary", lambda **kw: mock_budget)
    monkeypatch.setattr(db, "get_expenses_by_category", lambda **kw: [])
    monkeypatch.setattr(db, "get_monthly_overview", lambda **kw: [])
    monkeypatch.setattr(db, "get_monthly_bills", lambda **kw: [])

    # Cria token de sessão válido
    token = sec.create_session_token(user_name="bruno", display_name="Bruno Roma", role="admin")
    client.cookies.set("finance_session", token)

    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Dashboard Financeiro" in response.text
    assert "Bruno Roma" in response.text

def test_mcp_auth_middleware_rejected():
    client = TestClient(app)
    # Acesso a /sse sem token deve retornar 401
    res = client.get("/sse")
    assert res.status_code == 401
    assert "Unauthorized" in res.text

def test_mcp_auth_middleware_invalid_token(monkeypatch):
    client = TestClient(app)
    import meu_agente_cli.db as db
    monkeypatch.setattr(db, "validate_mcp_token", lambda tok: None)

    res = client.get("/sse", headers={"Authorization": "Bearer mcp_live_token_invalido"})
    assert res.status_code == 401
    assert "Unauthorized" in res.text

def test_mcp_auth_middleware_valid_bearer_token(monkeypatch):
    client = TestClient(app)
    import meu_agente_cli.db as db
    # Mock validate_mcp_token retornando token válido
    monkeypatch.setattr(db, "validate_mcp_token", lambda tok: {"id": 1, "name": "Hermes Agent"})

    # Ao enviar token válido, o middleware permite a requisição seguir (não retorna 401)
    res = client.post("/messages/?session_id=fake_session", headers={"Authorization": "Bearer mcp_live_valido"})
    assert res.status_code != 401

def test_web_app_edit_lancamento(monkeypatch):
    client = TestClient(app)
    token = sec.create_session_token(user_name="bruno", display_name="Bruno Roma", role="admin")
    client.cookies.set("finance_session", token)

    called = {}
    import meu_agente_cli.db as db
    def mock_update(**kwargs):
        called.update(kwargs)
        return True
    monkeypatch.setattr(db, "update_financial_record", mock_update)

    res = client.post("/lancamentos/edit/10", data={
        "tipo": "despesa",
        "categoria": "Supermercado",
        "valor": "125.50",
        "descricao": "Compras semanais",
        "data": "2026-10-01",
        "user_name": "fabiana",
        "mes_ano": "2026-10"
    }, follow_redirects=False)

    assert res.status_code == 303
    assert called["record_id"] == 10
    assert called["amount"] == 125.50
    assert called["category"] == "Supermercado"
    assert called["user_name"] == "fabiana"

def test_web_app_edit_conta(monkeypatch):
    client = TestClient(app)
    token = sec.create_session_token(user_name="bruno", display_name="Bruno Roma", role="admin")
    client.cookies.set("finance_session", token)

    called = {}
    import meu_agente_cli.db as db
    def mock_update_bill(**kwargs):
        called.update(kwargs)
        return True
    monkeypatch.setattr(db, "update_monthly_bill", mock_update_bill)

    res = client.post("/contas/edit/5", data={
        "descricao": "Internet Fibra",
        "valor": "149.90",
        "data_vencimento": "2026-10-15",
        "categoria": "Internet",
        "user_name": "bruno",
        "propagar_futuros": "on",
        "mes_ano": "2026-10"
    }, follow_redirects=False)

    assert res.status_code == 303
    assert called["bill_id"] == "5"
    assert called["new_amount"] == 149.90
    assert called["propagate_future"] is True
    assert called["user_name"] == "bruno"

def test_web_app_movimentar_rf(monkeypatch):
    client = TestClient(app)
    token = sec.create_session_token(user_name="bruno", display_name="Bruno Roma", role="admin")
    client.cookies.set("finance_session", token)

    called = {}
    import meu_agente_cli.invest as invest
    def mock_movimentar(**kwargs):
        called.update(kwargs)
        return True
    monkeypatch.setattr(invest, "add_movimentacao_renda_fixa", mock_movimentar)

    res = client.post("/investimentos/movimentar_rf", data={
        "id_investimento": "2",
        "tipo_movimentacao": "APORTE",
        "valor": "500.00",
        "data_movimentacao": "2026-10-01"
    }, follow_redirects=False)

    assert res.status_code == 303
    assert called["id_investimento"] == 2
    assert called["tipo_movimentacao"] == "APORTE"
    assert called["valor"] == 500.00

def test_web_app_update_saldo_rf(monkeypatch):
    client = TestClient(app)
    token = sec.create_session_token(user_name="bruno", display_name="Bruno Roma", role="admin")
    client.cookies.set("finance_session", token)

    called = {}
    import meu_agente_cli.invest as invest
    def mock_update_saldo(**kwargs):
        called.update(kwargs)
        return True
    monkeypatch.setattr(invest, "update_valor_atual_renda_fixa", mock_update_saldo)

    res = client.post("/investimentos/update_saldo_rf/2", data={
        "novo_valor": "10550.25"
    }, follow_redirects=False)

    assert res.status_code == 303
    assert called["id_investimento"] == 2
    assert called["novo_valor"] == 10550.25



