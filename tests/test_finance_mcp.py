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
        "listar_cartoes_credito",
    ]
    for expected in expected_tools:
        assert expected in tool_names
    # Garante que o agente Hermes NÃO possui ferramentas para criar novos cartões
    assert "cadastrar_cartao" not in tool_names
    assert "criar_cartao" not in tool_names
    assert "adicionar_cartao" not in tool_names

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

def test_credit_card_find_matching():
    import meu_agente_cli.db as db
    # Cadastrados no seed: Porto Seguro, C6, Itau, BB
    c1 = db.find_matching_credit_card("Porto Seguro")
    assert c1 is not None
    assert c1["name"] == "Porto Seguro"

    # Busca aproximada por substring "Porto"
    c2 = db.find_matching_credit_card("Porto")
    assert c2 is not None
    assert c2["name"] == "Porto Seguro"

    # Busca com "cartao c6"
    c3 = db.find_matching_credit_card("cartão C6")
    assert c3 is not None
    assert c3["name"] == "C6"

    # Cartão inexistente
    c4 = db.find_matching_credit_card("Cartao Inexistente 123")
    assert c4 is None

def test_add_card_purchase_rejects_unregistered_card():
    import meu_agente_cli.db as db
    ok, msg = db.add_card_purchase(
        card_name="Cartao Fantasma",
        category="Outros",
        total_amount=100.0,
        installments=1,
        description="Teste invalido"
    )
    assert ok is False
    assert "não encontrado ou inativo" in msg

def test_delete_credit_card_blocked_when_has_purchases(monkeypatch):
    import meu_agente_cli.db as db
    # Mock de cartão com compras vinculadas
    monkeypatch.setattr(db, "get_credit_card_by_id", lambda cid: {"id": 1, "name": "Porto Seguro", "purchases_count": 5})
    
    # Mock do cursor retornando count > 0
    class MockCur:
        def execute(self, sql, params): pass
        def fetchone(self): return [5]
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class MockConn:
        def cursor(self): return MockCur()
        def close(self): pass
        def commit(self): pass

    monkeypatch.setattr(db, "get_connection", lambda: MockConn())

    ok, msg = db.delete_credit_card(1)
    assert ok is False
    assert "Não é possível excluir o cartão" in msg
    assert "apenas ser desativado" in msg

def test_mcp_comprar_no_cartao_unregistered_returns_friendly_error():
    from meu_agente_cli.mcp_server import comprar_no_cartao
    res = comprar_no_cartao(
        cartao="Nubank Desconhecido",
        valor=50.0,
        descricao="Almoço",
        user_name="bruno"
    )
    assert "[ERRO]" in res
    assert "não encontrado ou inativo" in res
    assert "Novos cartões devem ser cadastrados exclusivamente pela interface Web" in res

def test_mcp_listar_cartoes_credito():
    from meu_agente_cli.mcp_server import listar_cartoes_credito
    cards = listar_cartoes_credito()
    assert isinstance(cards, list)
    assert len(cards) >= 1
    nomes = [c["nome"] for c in cards]
    assert "Porto Seguro" in nomes or "C6" in nomes

def test_alterar_senha_propria_sucesso():
    import meu_agente_cli.db as db
    client = TestClient(app)
    
    # Faz login como bruno
    login_res = client.post("/login", data={"username": "bruno", "password": "bruno123"}, follow_redirects=False)
    assert login_res.status_code == 303
    session_cookie = login_res.cookies.get("finance_session")
    client.cookies.set("finance_session", session_cookie)
    
    # Altera senha própria
    res = client.post("/perfil/alterar-senha", data={
        "target_user": "bruno",
        "senha_atual": "bruno123",
        "nova_senha": "senha_teste_456",
        "confirma_senha": "senha_teste_456"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "sucesso" in data["message"].lower()
    
    # Verifica autenticação com a nova senha
    assert db.authenticate_user("bruno", "senha_teste_456") is not None
    assert db.authenticate_user("bruno", "bruno123") is None
    
    # Restaura senha padrão
    db.update_user_password("bruno", "bruno123")

def test_alterar_senha_senha_atual_incorreta():
    client = TestClient(app)
    token = sec.create_session_token(user_name="bruno", display_name="Bruno", role="admin")
    client.cookies.set("finance_session", token)
    
    res = client.post("/perfil/alterar-senha", data={
        "target_user": "bruno",
        "senha_atual": "senha_errada_xyz",
        "nova_senha": "nova_senha_123",
        "confirma_senha": "nova_senha_123"
    })
    assert res.status_code == 400
    data = res.json()
    assert data["success"] is False
    assert "atual está incorreta" in data["message"].lower()

def test_alterar_senha_confirmacao_invalida():
    client = TestClient(app)
    token = sec.create_session_token(user_name="bruno", display_name="Bruno", role="admin")
    client.cookies.set("finance_session", token)
    
    res = client.post("/perfil/alterar-senha", data={
        "target_user": "bruno",
        "senha_atual": "bruno123",
        "nova_senha": "nova_senha_123",
        "confirma_senha": "senha_diferente"
    })
    assert res.status_code == 400
    data = res.json()
    assert data["success"] is False
    assert "não confere" in data["message"].lower()

def test_admin_redefinir_senha_outro_usuario():
    import meu_agente_cli.db as db
    client = TestClient(app)
    # Bruno (admin) redefinindo senha de Fabiana
    token = sec.create_session_token(user_name="bruno", display_name="Bruno", role="admin")
    client.cookies.set("finance_session", token)
    
    res = client.post("/perfil/alterar-senha", data={
        "target_user": "fabiana",
        "nova_senha": "nova_fabiana_senha",
        "confirma_senha": "nova_fabiana_senha"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert db.authenticate_user("fabiana", "nova_fabiana_senha") is not None
    
    # Restaura senha de Fabiana
    db.update_user_password("fabiana", "fabiana123")

def test_usuario_comum_nao_pode_alterar_outro_usuario():
    client = TestClient(app)
    # Fabiana (user comum) tentando alterar a senha de Bruno
    token = sec.create_session_token(user_name="fabiana", display_name="Fabiana", role="user")
    client.cookies.set("finance_session", token)
    
    res = client.post("/perfil/alterar-senha", data={
        "target_user": "bruno",
        "nova_senha": "senha_hacker_123",
        "confirma_senha": "senha_hacker_123"
    })
    assert res.status_code == 403
    data = res.json()
    assert data["success"] is False
    assert "permissão" in data["message"].lower()

def test_edit_conta_action_success():
    client = TestClient(app)
    token = sec.create_session_token(user_name="bruno", display_name="Bruno", role="admin")
    client.cookies.set("finance_session", token)

    res = client.post("/contas/edit/166", data={
        "descricao": "Terapia",
        "valor": "320.00",
        "data_vencimento": "2026-10-10",
        "categoria": "Saúde",
        "user_name": "bruno",
        "mes_ano": "2026-10"
    }, follow_redirects=False)

    assert res.status_code == 303
    assert "/contas" in res.headers.get("location")
