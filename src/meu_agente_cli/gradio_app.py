import os
import sys
import re
from datetime import datetime
from typing import Optional, List, Tuple
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import gradio as gr

from meu_agente_cli import db
from meu_agente_cli import invest

# =====================================================================
# FUNÇÕES DE APOIO E PARÂMETROS
# =====================================================================

def get_period_params(periodo_opcao: str, mes_personalizado: str = "") -> Optional[str]:
    """Converte a opção de período selecionada para o formato MM-YYYY."""
    now = datetime.now()
    if periodo_opcao == "Mês Atual":
        return now.strftime("%m-%Y")
    elif periodo_opcao == "Próximo Mês":
        prox_mes = now.month + 1 if now.month < 12 else 1
        prox_ano = now.year if now.month < 12 else now.year + 1
        return f"{prox_mes:02d}-{prox_ano}"
    elif periodo_opcao == "Mês Específico" and mes_personalizado and mes_personalizado.strip():
        return mes_personalizado.strip()
    return None

def toggle_custom_month(choice):
    """Exibe ou esconde o campo de mês personalizado."""
    return gr.update(visible=(choice == "Mês Específico"))

# =====================================================================
# ABA 1: CONTAS MENSAIS E FATURAS (CUSTOS FIXOS)
# =====================================================================

def load_monthly_bills_tab(periodo_opcao: str, mes_personalizado: str = ""):
    """Carrega as contas mensais e faturas consolidadas do mês."""
    month_year = get_period_params(periodo_opcao, mes_personalizado)
    bills = db.get_monthly_bills(month_year)
    total = sum(b["amount"] for b in bills)
    pagas = sum(b["amount"] for b in bills if b["is_paid"])
    pendentes = total - pagas
    
    proximos = [b["due_date"] for b in bills if not b["is_paid"] and b["due_date"]]
    prox_venc = min(proximos).strftime("%d/%m/%Y") if proximos else "Tudo pago! 🎉"
    
    v_tot = f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    v_pago = f"R$ {pagas:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    v_pend = f"R$ {pendentes:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    
    kpi_tot = f"## 📅 {v_tot}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Compromissos do Mês</span>"
    kpi_pago = f"## 🟢 {v_pago}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Contas Liquidadas</span>"
    kpi_pend = f"## ⏳ {v_pend}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>A Pagar no Mês</span>"
    kpi_prox = f"## ⏰ {prox_venc}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Vencimento Mais Próximo</span>"
    
    rows = []
    for b in bills:
        dt_venc = b["due_date"].strftime("%d/%m/%Y") if b["due_date"] else "-"
        st_label = "🟢 Paga" if b["is_paid"] else "⏳ Pendente"
        tipo_label = "💳 Fatura Cartão" if b.get("is_card_invoice") else "📄 Conta Fixa"
        val_str = f"R$ {b['amount']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        rows.append([
            str(b["id"]),
            b["name"],
            b["category"],
            dt_venc,
            val_str,
            st_label,
            tipo_label
        ])
        
    df = pd.DataFrame(rows, columns=["ID", "Conta / Fatura", "Categoria", "Vencimento", "Valor", "Status", "Tipo"])
    return kpi_tot, kpi_pago, kpi_pend, kpi_prox, df

def toggle_bill_payment_action(record_id_input: str, mark_as_paid: bool, periodo_opcao: str, mes_personalizado: str):
    """Marca uma conta mensal ou fatura como paga ou reabre para pendente."""
    if not record_id_input or not str(record_id_input).strip():
        return "❌ Informe ou selecione o ID da conta/fatura.", *load_monthly_bills_tab(periodo_opcao, mes_personalizado)
        
    rid = str(record_id_input).strip()
    month_year = get_period_params(periodo_opcao, mes_personalizado)
    success = db.toggle_bill_paid(rid, is_paid=mark_as_paid, month_year=month_year)
    
    action_text = "paga" if mark_as_paid else "reaberta como pendente"
    if success:
        msg = f"✅ Conta/Fatura '{rid}' marcada como {action_text} com sucesso!"
    else:
        msg = f"❌ Falha ao atualizar o status da conta/fatura '{rid}'."
        
    kpi_t, kpi_pg, kpi_pd, kpi_px, df = load_monthly_bills_tab(periodo_opcao, mes_personalizado)
    return msg, kpi_t, kpi_pg, kpi_pd, kpi_px, df

def project_annual_fixed_expenses_action(ano_input: int, periodo_opcao: str, mes_personalizado: str):
    """Gera a projeção anual de todas as despesas fixas para o ano especificado."""
    ano = int(ano_input) if ano_input and int(ano_input) > 2000 else datetime.now().year
    res = db.project_annual_fixed_expenses(year=ano, start_month=1)
    
    gerados = res.get("gerados", 0)
    tot_val = res.get("total_valor", 0.0)
    meses = res.get("meses_afetados", [])
    
    if gerados > 0:
        meses_str = ", ".join(str(m) for m in meses)
        msg = f"🔮 **Previsão Anual Gerada!** {gerados} lançamento(s) criados para o ano {ano} (meses: {meses_str}), totalizando R$ {tot_val:,.2f} previstos com base nos últimos valores."
    else:
        msg = f"ℹ️ Todas as contas fixas recorrentes do ano {ano} já estão lançadas e em dia (nenhuma duplicação gerada)."
        
    kpi_t, kpi_pg, kpi_pd, kpi_px, df = load_monthly_bills_tab(periodo_opcao, mes_personalizado)
    return msg, kpi_t, kpi_pg, kpi_pd, kpi_px, df

def add_new_monthly_bill_action(
    nome: str, 
    categoria: str, 
    valor: float, 
    dia_venc: int, 
    projetar_ano: bool,
    periodo_opcao: str, 
    mes_personalizado: str
):
    """Cadastra um novo compromisso mensal recorrente com opção de projeção anual."""
    if not nome.strip():
        return "❌ O nome da conta é obrigatório.", *load_monthly_bills_tab(periodo_opcao, mes_personalizado)
    if valor <= 0:
        return "❌ O valor da conta deve ser maior que zero.", *load_monthly_bills_tab(periodo_opcao, mes_personalizado)
        
    dia = max(1, min(31, int(dia_venc)))
    # 1. Salva nos modelos de contas recorrentes
    db.add_recurring_bill(nome.strip(), categoria.strip(), valor, dia)
    
    # 2. Cria o registro para o mês corrente/selecionado
    month_year = get_period_params(periodo_opcao, mes_personalizado)
    now = datetime.now()
    if month_year and "-" in month_year:
        m, y = int(month_year.split("-")[0]), int(month_year.split("-")[1])
    else:
        m, y = now.month, now.year
        
    import calendar
    max_d = calendar.monthrange(y, m)[1]
    due_date_str = f"{y:04d}-{m:02d}-{min(dia, max_d):02d}"
    
    db.add_financial_record("despesa", categoria.strip(), valor, nome.strip(), due_date_str)
    
    # Marca nature como 'monthly' no registro recém inserido
    try:
        conn = db.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE financial_records SET nature = 'monthly' WHERE id = (SELECT MAX(id) FROM financial_records)"
            )
        conn.commit()
        conn.close()
    except Exception:
        pass
        
    extra_msg = ""
    if projetar_ano:
        proj_res = db.project_annual_fixed_expenses(year=y, start_month=1)
        if proj_res.get("gerados", 0) > 0:
            extra_msg = f" e projetada para {proj_res['gerados']} meses deste ano!"
            
    msg = f"✅ Conta recorrente '{nome}' de R$ {valor:.2f} (Venc: dia {dia}) cadastrada{extra_msg}"
    kpi_t, kpi_pg, kpi_pd, kpi_px, df = load_monthly_bills_tab(periodo_opcao, mes_personalizado)
    return msg, kpi_t, kpi_pg, kpi_pd, kpi_px, df

def delete_monthly_bill_action(record_id_input: str, periodo_opcao: str, mes_personalizado: str):
    """Exclui (soft delete) uma conta mensal fixa."""
    if not record_id_input or not str(record_id_input).strip():
        return "❌ Informe ou selecione o ID da conta a ser excluída.", *load_monthly_bills_tab(periodo_opcao, mes_personalizado)
        
    rid_str = str(record_id_input).strip()
    if rid_str.startswith("card_"):
        return "⚠️ Faturas de cartão são consolidadas a partir das compras do cartão. Para excluir compras ou parcelas, utilize a aba '💳 Cartões de Crédito'.", *load_monthly_bills_tab(periodo_opcao, mes_personalizado)
        
    try:
        rid = int(rid_str)
    except ValueError:
        return f"❌ ID inválido: '{rid_str}'.", *load_monthly_bills_tab(periodo_opcao, mes_personalizado)
        
    rec = db.get_financial_record_by_id(rid)
    if not rec:
        return f"❌ Conta com ID #{rid} não encontrada ou já inativa.", *load_monthly_bills_tab(periodo_opcao, mes_personalizado)
        
    success = db.delete_financial_record(rid)
    if success:
        nome_desc = rec.get("description") or rec.get("category") or ""
        msg = f"🗑️ Conta #{rid} ('{nome_desc}') excluída com sucesso!"
    else:
        msg = f"❌ Falha ao excluir a conta #{rid}."
        
    kpi_t, kpi_pg, kpi_pd, kpi_px, df = load_monthly_bills_tab(periodo_opcao, mes_personalizado)
    return msg, kpi_t, kpi_pg, kpi_pd, kpi_px, df

def load_bill_data_for_edit(record_id_input: str):
    """Busca os dados de uma conta para preencher o formulário de edição."""
    if not record_id_input or not str(record_id_input).strip():
        return gr.update(), gr.update(), gr.update(), gr.update(), "❌ Informe o ID da conta."
    rid_str = str(record_id_input).strip()
    if rid_str.startswith("card_"):
        return gr.update(), gr.update(), gr.update(), gr.update(), "⚠️ Faturas de cartão são consolidadas a partir de compras. Para editar compras, use a aba '💳 Cartões de Crédito'."
    try:
        rid = int(rid_str)
    except ValueError:
        return gr.update(), gr.update(), gr.update(), gr.update(), f"❌ ID inválido: '{rid_str}'."
        
    rec = db.get_financial_record_by_id(rid)
    if not rec:
        return gr.update(), gr.update(), gr.update(), gr.update(), f"❌ Conta #{rid} não encontrada."
        
    dt_val = rec.get("due_date") or rec.get("date")
    due_str = dt_val.strftime("%Y-%m-%d") if dt_val else ""
    return (
        rec.get("description", "") or "",
        rec.get("category", "Condomínio"),
        float(rec.get("amount", 0.0)),
        due_str,
        f"ℹ️ Dados da conta #{rid} carregados com sucesso."
    )

def update_monthly_bill_action(
    record_id_input: str, 
    nome: str, 
    categoria: str, 
    valor: float, 
    vencimento: str, 
    propagar: bool,
    periodo_opcao: str, 
    mes_personalizado: str
):
    """Atualiza as informações de uma conta fixa existente com suporte a propagação para meses seguintes."""
    if not record_id_input or not str(record_id_input).strip():
        return "❌ Informe o ID da conta a ser atualizada.", *load_monthly_bills_tab(periodo_opcao, mes_personalizado)
        
    rid_str = str(record_id_input).strip()
    if rid_str.startswith("card_"):
        return "⚠️ Faturas de cartão são consolidadas a partir das compras do cartão. Para editar compras, utilize a aba '💳 Cartões de Crédito'.", *load_monthly_bills_tab(periodo_opcao, mes_personalizado)
        
    try:
        rid = int(rid_str)
    except ValueError:
        return f"❌ ID inválido: '{rid_str}'.", *load_monthly_bills_tab(periodo_opcao, mes_personalizado)
        
    if not nome or not nome.strip():
        return "❌ O nome da conta é obrigatório.", *load_monthly_bills_tab(periodo_opcao, mes_personalizado)
        
    if valor is None or valor <= 0:
        return "❌ O valor da conta deve ser maior que zero.", *load_monthly_bills_tab(periodo_opcao, mes_personalizado)
        
    # Tratamento de vencimento: se for apenas número (dia do mês 1-31) ou data completa
    venc_str = vencimento.strip() if vencimento else ""
    if venc_str.isdigit() and 1 <= int(venc_str) <= 31:
        month_year = get_period_params(periodo_opcao, mes_personalizado)
        now = datetime.now()
        if month_year and "-" in month_year:
            m, y = int(month_year.split("-")[0]), int(month_year.split("-")[1])
        else:
            m, y = now.month, now.year
        import calendar
        max_d = calendar.monthrange(y, m)[1]
        venc_str = f"{y:04d}-{m:02d}-{min(int(venc_str), max_d):02d}"
        
    # 1. Atualiza valor e vencimento (com propagação se selecionado)
    success = db.update_monthly_bill(
        record_id=rid,
        new_amount=float(valor),
        new_due_date=venc_str if venc_str else None,
        propagate_future=bool(propagar)
    )
    
    # 2. Atualiza descrição e categoria se alteradas
    if success:
        try:
            db.update_financial_record(
                record_id=rid,
                description=nome.strip(),
                category=categoria.strip() if categoria else "Outros",
                amount=float(valor),
                due_date=venc_str if venc_str else None
            )
        except Exception:
            pass
            
        prop_msg = " e propagado para os meses seguintes deste ano!" if propagar else " com sucesso!"
        msg = f"✅ Conta #{rid} ('{nome.strip()}') atualizada para R$ {valor:.2f}{prop_msg}"
    else:
        msg = f"❌ Falha ao atualizar a conta #{rid}. Verifique se o registro existe."
        
    kpi_t, kpi_pg, kpi_pd, kpi_px, df = load_monthly_bills_tab(periodo_opcao, mes_personalizado)
    return msg, kpi_t, kpi_pg, kpi_pd, kpi_px, df

def on_select_monthly_bill(evt: gr.SelectData, df: pd.DataFrame):
    """Preenche automaticamente os formulários ao clicar em uma linha da tabela."""
    if evt is None or evt.index is None:
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    row_idx = evt.index[0]
    if row_idx >= len(df):
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    row = df.iloc[row_idx]
    rid = str(row["ID"])
    nome = str(row["Conta / Fatura"])
    cat = str(row["Categoria"])
    venc = str(row["Vencimento"])
    
    # Tratamento do valor formatado: ex: "R$ 1.250,00" -> 1250.0
    val_raw = str(row["Valor"]).replace("R$", "").replace(".", "").replace(",", ".").strip()
    try:
        val = float(val_raw)
    except ValueError:
        val = 0.0
        
    due_iso = venc
    if "/" in venc:
        parts = venc.split("/")
        if len(parts) == 3:
            due_iso = f"{parts[2]}-{parts[1]}-{parts[0]}"
            
    return rid, rid, rid, nome, cat, val, due_iso

# =====================================================================
# ABA: RECEITAS & ENTRADAS
# =====================================================================

RECEITA_CATEGORIAS = ["Salário", "Freelance", "Investimentos", "Dividendos", "Pró-Labore", "Aluguel", "Reembolso", "Vendas", "Prêmio / Bônus", "Outros"]

def load_incomes_tab(periodo_opcao: str, mes_personalizado: str = "", categoria: str = "Todas"):
    """Carrega as receitas consolidadas do mês e calcula os KPIs de renda."""
    month_year = get_period_params(periodo_opcao, mes_personalizado)
    cat_filtro = None if categoria in ["Todas", "Todos", "", None] else categoria
    incomes = db.get_monthly_incomes(month_year=month_year, category=cat_filtro)
    
    total = sum(item["amount"] for item in incomes)
    salarios = sum(item["amount"] for item in incomes if item["category"].strip().lower() in ["salário", "salario", "pró-labore", "pro-labore", "remuneração", "fixo"])
    extras = total - salarios
    qtd = len(incomes)
    
    v_tot = f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    v_sal = f"R$ {salarios:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    v_ext = f"R$ {extras:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    
    kpi_tot = f"## 🟢 {v_tot}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Total de Receitas no Mês</span>"
    kpi_sal = f"## 💼 {v_sal}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Salário / Renda Principal</span>"
    kpi_ext = f"## 📈 {v_ext}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Rendas Extras / Outras</span>"
    kpi_qtd = f"## 🔢 {qtd} entrada(s)\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Lançamentos no Mês</span>"
    
    rows = []
    for item in incomes:
        dt_val = item["due_date"] or item["date"]
        dt_str = dt_val.strftime("%d/%m/%Y") if dt_val else "-"
        val_str = f"R$ {item['amount']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        rows.append([
            str(item["id"]),
            item["description"],
            item["category"],
            dt_str,
            val_str
        ])
        
    df = pd.DataFrame(rows, columns=["ID", "Descrição / Fonte", "Categoria", "Data / Previsão", "Valor"])
    return kpi_tot, kpi_sal, kpi_ext, kpi_qtd, df

def add_income_action(descricao: str, categoria: str, valor: float, data_str: str, periodo_opcao: str, mes_personalizado: str, cat_filtro: str):
    """Registra uma nova receita financeira."""
    if not descricao or not descricao.strip():
        return "❌ A descrição da receita é obrigatória.", *load_incomes_tab(periodo_opcao, mes_personalizado, cat_filtro)
    if valor is None or valor <= 0:
        return "❌ O valor da receita deve ser maior que zero.", *load_incomes_tab(periodo_opcao, mes_personalizado, cat_filtro)
        
    dt_clean = data_str.strip() if data_str else ""
    if dt_clean.isdigit() and 1 <= int(dt_clean) <= 31:
        month_year = get_period_params(periodo_opcao, mes_personalizado)
        now = datetime.now()
        if month_year and "-" in month_year:
            m, y = int(month_year.split("-")[0]), int(month_year.split("-")[1])
        else:
            m, y = now.month, now.year
        import calendar
        max_d = calendar.monthrange(y, m)[1]
        dt_clean = f"{y:04d}-{m:02d}-{min(int(dt_clean), max_d):02d}"
    elif not dt_clean:
        dt_clean = datetime.now().strftime("%Y-%m-%d")
        
    success = db.add_financial_record(
        record_type="receita",
        category=categoria.strip() if categoria else "Outros",
        amount=float(valor),
        description=descricao.strip(),
        due_date=dt_clean
    )
    
    if success:
        msg = f"✅ Receita '{descricao.strip()}' de R$ {valor:.2f} lançada com sucesso!"
    else:
        msg = f"❌ Falha ao registrar receita."
        
    kpi_t, kpi_s, kpi_e, kpi_q, df = load_incomes_tab(periodo_opcao, mes_personalizado, cat_filtro)
    return msg, kpi_t, kpi_s, kpi_e, kpi_q, df

def update_income_action(record_id_input: str, descricao: str, categoria: str, valor: float, data_str: str, periodo_opcao: str, mes_personalizado: str, cat_filtro: str):
    """Atualiza as informações de uma receita existente."""
    if not record_id_input or not str(record_id_input).strip():
        return "❌ Informe o ID da receita a ser atualizada.", *load_incomes_tab(periodo_opcao, mes_personalizado, cat_filtro)
        
    try:
        rid = int(str(record_id_input).strip())
    except ValueError:
        return f"❌ ID inválido: '{record_id_input}'.", *load_incomes_tab(periodo_opcao, mes_personalizado, cat_filtro)
        
    if not descricao or not descricao.strip():
        return "❌ A descrição da receita é obrigatória.", *load_incomes_tab(periodo_opcao, mes_personalizado, cat_filtro)
    if valor is None or valor <= 0:
        return "❌ O valor da receita deve ser maior que zero.", *load_incomes_tab(periodo_opcao, mes_personalizado, cat_filtro)
        
    dt_clean = data_str.strip() if data_str else ""
    if dt_clean.isdigit() and 1 <= int(dt_clean) <= 31:
        month_year = get_period_params(periodo_opcao, mes_personalizado)
        now = datetime.now()
        if month_year and "-" in month_year:
            m, y = int(month_year.split("-")[0]), int(month_year.split("-")[1])
        else:
            m, y = now.month, now.year
        import calendar
        max_d = calendar.monthrange(y, m)[1]
        dt_clean = f"{y:04d}-{m:02d}-{min(int(dt_clean), max_d):02d}"
        
    success = db.update_financial_record(
        record_id=rid,
        description=descricao.strip(),
        category=categoria.strip() if categoria else "Outros",
        amount=float(valor),
        due_date=dt_clean if dt_clean else None
    )
    
    if success:
        msg = f"✅ Receita #{rid} ('{descricao.strip()}') atualizada com sucesso!"
    else:
        msg = f"❌ Falha ao atualizar receita #{rid}. Verifique se o ID existe."
        
    kpi_t, kpi_s, kpi_e, kpi_q, df = load_incomes_tab(periodo_opcao, mes_personalizado, cat_filtro)
    return msg, kpi_t, kpi_s, kpi_e, kpi_q, df

def delete_income_action(record_id_input: str, periodo_opcao: str, mes_personalizado: str, cat_filtro: str):
    """Inativa logicamente (soft delete) uma receita."""
    if not record_id_input or not str(record_id_input).strip():
        return "❌ Informe ou selecione o ID da receita a excluir.", *load_incomes_tab(periodo_opcao, mes_personalizado, cat_filtro)
        
    try:
        rid = int(str(record_id_input).strip())
    except ValueError:
        return f"❌ ID inválido: '{record_id_input}'.", *load_incomes_tab(periodo_opcao, mes_personalizado, cat_filtro)
        
    rec = db.get_financial_record_by_id(rid)
    if not rec or str(rec.get("type", "")).lower() != "receita":
        return f"❌ Receita #{rid} não encontrada ou já inativa.", *load_incomes_tab(periodo_opcao, mes_personalizado, cat_filtro)
        
    success = db.delete_financial_record(rid)
    if success:
        msg = f"🗑️ Receita #{rid} ('{rec.get('description', '')}') excluída com sucesso!"
    else:
        msg = f"❌ Falha ao excluir a receita #{rid}."
        
    kpi_t, kpi_s, kpi_e, kpi_q, df = load_incomes_tab(periodo_opcao, mes_personalizado, cat_filtro)
    return msg, kpi_t, kpi_s, kpi_e, kpi_q, df

def load_income_data_for_edit(record_id_input: str):
    """Carrega os dados de uma receita para o formulário de edição."""
    if not record_id_input or not str(record_id_input).strip():
        return gr.update(), gr.update(), gr.update(), gr.update(), "❌ Informe o ID da receita."
    try:
        rid = int(str(record_id_input).strip())
    except ValueError:
        return gr.update(), gr.update(), gr.update(), gr.update(), f"❌ ID inválido: '{record_id_input}'."
        
    rec = db.get_financial_record_by_id(rid)
    if not rec or str(rec.get("type", "")).lower() != "receita":
        return gr.update(), gr.update(), gr.update(), gr.update(), f"❌ Receita #{rid} não encontrada."
        
    dt_val = rec.get("due_date") or rec.get("date")
    dt_iso = dt_val.strftime("%Y-%m-%d") if dt_val else ""
    return (
        rec.get("description", ""),
        rec.get("category", "Salário"),
        float(rec.get("amount", 0.0)),
        dt_iso,
        f"ℹ️ Dados da receita #{rid} carregados com sucesso."
    )

def on_select_income(evt: gr.SelectData, df: pd.DataFrame):
    """Preenche os campos de exclusão e edição ao clicar em uma linha da tabela de receitas."""
    if evt is None or evt.index is None:
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    row_idx = evt.index[0]
    if row_idx >= len(df):
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    row = df.iloc[row_idx]
    rid = str(row["ID"])
    desc = str(row["Descrição / Fonte"])
    cat = str(row["Categoria"])
    dt_val = str(row["Data / Previsão"])
    
    val_raw = str(row["Valor"]).replace("R$", "").replace(".", "").replace(",", ".").strip()
    try:
        val = float(val_raw)
    except ValueError:
        val = 0.0
        
    dt_iso = dt_val
    if "/" in dt_val:
        parts = dt_val.split("/")
        if len(parts) == 3:
            dt_iso = f"{parts[2]}-{parts[1]}-{parts[0]}"
            
    return rid, rid, desc, cat, val, dt_iso


# =====================================================================
# ABA 2: CARTÕES DE CRÉDITO (EXTRATO POR CARTÃO)
# =====================================================================

def load_card_purchases_tab(cartao_selecionado: str, periodo_opcao: str, mes_personalizado: str = ""):
    """Carrega o extrato de compras e faturas de um cartão específico ou de todos os cartões."""
    month_year = get_period_params(periodo_opcao, mes_personalizado)
    items = db.get_card_purchases(card_name=cartao_selecionado, month_year=month_year)
    total = sum(item["amount"] for item in items)
    all_paid = all(item["is_paid"] for item in items) if items else False
    
    is_all_cards = not cartao_selecionado or cartao_selecionado in ["Todos os Cartões", "Todos", "Cartão de Crédito"]
    
    if items:
        if all_paid:
            st_label = "🟢 Fatura(s) Paga(s)"
        else:
            st_label = "⏳ Fatura em Aberto"
    else:
        st_label = "Sem compras no mês"
    
    vencimentos = [it["due_date"] for it in items if it["due_date"]]
    if is_all_cards:
        if vencimentos:
            unique_venc = sorted(list(set(v.strftime("%d/%m") for v in vencimentos)))
            venc_str = ", ".join(unique_venc) if len(unique_venc) <= 4 else f"{unique_venc[0]} a {unique_venc[-1]}"
        else:
            venc_str = "-"
    else:
        venc_str = vencimentos[0].strftime("%d/%m/%Y") if vencimentos else "-"
    
    if total < 0:
        v_tot = f"-R$ {abs(total):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    else:
        v_tot = f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
    card_title = "Todas as Faturas" if is_all_cards else f"Fatura {cartao_selecionado}"
    kpi_tot = f"## 💳 {v_tot}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>{card_title} no Mês</span>"
    kpi_venc = f"## 📅 {venc_str}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Vencimento da Fatura</span>"
    kpi_qtd = f"## 🛍️ {len(items)} compras/parcelas\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Itens na Fatura</span>"
    kpi_status = f"## {st_label}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Situação do Pagamento</span>"
    
    rows = []
    for it in items:
        dt_venc = it["due_date"].strftime("%d/%m/%Y") if it["due_date"] else "-"
        dt_buy = it["buy_date"].strftime("%d/%m/%Y") if it["buy_date"] else "-"
        if it["amount"] < 0:
            val_str = f"-R$ {abs(it['amount']):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            st = "↩️ Estorno/Crédito"
        else:
            val_str = f"R$ {it['amount']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            st = "🟢 Paga" if it["is_paid"] else "⏳ Aberta"
            
        rows.append([
            it["id"],
            it.get("card_name", "-"),
            it["description"],
            it["category"],
            val_str,
            dt_venc,
            dt_buy,
            st
        ])
    df = pd.DataFrame(rows, columns=["ID", "Cartão", "Descrição da Compra", "Categoria", "Valor", "Vencimento na Fatura", "Data da Compra", "Status"])
    return kpi_tot, kpi_venc, kpi_qtd, kpi_status, df

def refresh_card_purchases_tab(cartao_selecionado: str, periodo_opcao: str, mes_personalizado: str = ""):
    """Atualiza as opções de cartões do banco e recarrega o extrato."""
    db_cards = [c for c in db.get_distinct_cards() if c != "Cartão de Crédito"]
    choices = ["Todos os Cartões"] + db_cards
    sel = cartao_selecionado if cartao_selecionado in choices else "Todos os Cartões"
    k_tot, k_venc, k_qtd, k_st, df = load_card_purchases_tab(sel, periodo_opcao, mes_personalizado)
    return gr.update(choices=choices, value=sel), k_tot, k_venc, k_qtd, k_st, df

def add_card_purchase_action(cartao: str, categoria: str, valor_total: float, parcelas: int, descricao: str, data_compra: str, periodo_opcao: str, mes_personalizado: str):
    """Registra uma nova compra ou estorno no cartão de crédito selecionado."""
    if not cartao or cartao.strip() in ["Todos os Cartões", "Todos"]:
        return "❌ Selecione um cartão específico (ex: BB, C6, Itaú, Porto-Seguro).", gr.update(), *load_card_purchases_tab(cartao, periodo_opcao, mes_personalizado)
    
    try:
        val_f = float(valor_total)
    except (TypeError, ValueError):
        val_f = 0.0
        
    if abs(val_f) < 0.001:
        return "❌ O valor não pode ser zero. Digite um valor positivo para compra ou negativo para estorno.", gr.update(), *load_card_purchases_tab(cartao, periodo_opcao, mes_personalizado)
        
    p_count = max(1, int(parcelas))
    is_refund = val_f < 0
    tipo_operacao = "Estorno" if is_refund else "Compra"
    
    cat_final = categoria.strip() if categoria and categoria.strip() else ("Estorno" if is_refund else "Outros")
    
    if not descricao or not descricao.strip():
        desc = f"Estorno no cartão {cartao}" if is_refund else f"Compra no cartão {cartao}"
    else:
        desc = descricao.strip()
    
    success = db.add_card_purchase(
        card_name=cartao,
        category=cat_final,
        total_amount=val_f,
        installments=p_count,
        description=desc,
        buy_date_str=data_compra
    )
    
    db_cards = [c for c in db.get_distinct_cards() if c != "Cartão de Crédito"]
    choices = ["Todos os Cartões"] + db_cards
    
    valor_fmt = f"-R$ {abs(val_f):.2f}" if is_refund else f"R$ {val_f:.2f}"
    
    if success:
        msg = f"✅ {tipo_operacao} de {valor_fmt} ({p_count}x) no cartão {cartao} lançado com sucesso!"
    else:
        msg = f"❌ Falha ao registrar {tipo_operacao.lower()} no cartão {cartao}."
        
    k_tot, k_vnc, k_q, k_st, df = load_card_purchases_tab(cartao, periodo_opcao, mes_personalizado)
    return msg, gr.update(choices=choices, value=cartao), k_tot, k_vnc, k_q, k_st, df

# =====================================================================
# ABA 3: GASTOS DIÁRIOS E TETO POR DIA (ORÇAMENTO DIÁRIO)
# =====================================================================

def load_daily_expenses_and_budget(periodo_opcao: str, mes_personalizado: str = "", categoria: str = "Todas", busca: str = ""):
    """Calcula a disponibilidade orçamentária por dia e carrega os gastos diários."""
    month_year = get_period_params(periodo_opcao, mes_personalizado)
    budget = db.get_daily_budget_summary(month_year)
    
    rec = budget["receitas_mes"]
    fixas = budget["custos_fixos_mes"]
    livre_rest = budget["saldo_livre_restante"]
    gasto_hoje = budget["gasto_hoje"]
    dias_rest = budget["dias_restantes"]
    teto = budget["teto_diario"]
    status_hoje = budget["status_hoje"]
    
    v_rec = f"R$ {rec:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    v_fixas = f"R$ {fixas:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    v_livre = f"R$ {livre_rest:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    v_teto = f"R$ {teto:,.2f} / dia".replace(",", "X").replace(".", ",").replace("X", ".")

    kpi_rec = f"## 🟢 {v_rec}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Receitas Consolidadas</span>"
    kpi_fixas = f"## 🔒 {v_fixas}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Comprometido no Mês</span>"
    kpi_livre = f"## 🛍️ {v_livre}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Disponível para Gastos Diários</span>"
    kpi_teto = f"## 🎯 {v_teto}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Limite Sugerido por Dia</span>"
    
    cor_hoje = "#10b981" if status_hoje == "ok" else "#ef4444"
    if status_hoje == "ok":
        msg_meta = f"✅ <b>Dentro do limite diário!</b> (Restam {dias_rest} dias no mês)"
    else:
        msg_meta = f"⚠️ <b>Atenção:</b> o gasto de hoje ultrapassou o teto diário recomendado!"
        
    status_card = f"""
    <div style="background-color: rgba(241, 245, 249, 0.08); border: 1px solid rgba(148, 163, 184, 0.2); padding: 12px 18px; border-radius: 8px; border-left: 5px solid {cor_hoje};">
        <span style="font-size: 16px; font-weight: bold; color: {cor_hoje};">Gasto Realizado Hoje: R$ {gasto_hoje:,.2f}</span><br>
        <span style="font-size: 13px; color: #94a3b8;">{msg_meta}</span>
    </div>
    """.replace(",", "X").replace(".", ",").replace("X", ".")
    
    cat_filtro = None if categoria in ["Todas", "Todos", "", None] else categoria
    raw_daily = db.get_daily_expenses(month_year=month_year, category=cat_filtro, query=busca)
    
    rows = []
    for r in raw_daily:
        rid, rtype, cat, val, desc, dt, due = r
        dt_str = dt.strftime("%d/%m/%Y") if dt else "-"
        val_str = f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        rows.append([rid, cat, val_str, desc, dt_str])
        
    df = pd.DataFrame(rows, columns=["ID", "Categoria", "Valor", "Descrição", "Data"])
    return kpi_rec, kpi_fixas, kpi_livre, kpi_teto, status_card, df

def add_quick_daily_expense(categoria: str, valor: float, descricao: str, data_gasto: str, periodo_opcao: str, mes_personalizado: str, cat_atual: str, busca_atual: str):
    """Registra uma despesa rotineira diária avulsa."""
    if valor <= 0:
        return "❌ O valor deve ser maior que zero.", *load_daily_expenses_and_budget(periodo_opcao, mes_personalizado, cat_atual, busca_atual)
    if not categoria.strip():
        return "❌ A categoria é obrigatória.", *load_daily_expenses_and_budget(periodo_opcao, mes_personalizado, cat_atual, busca_atual)
        
    dt_str = data_gasto.strip() if data_gasto and data_gasto.strip() else datetime.now().strftime("%Y-%m-%d")
    success = db.add_financial_record("despesa", categoria.strip(), valor, descricao.strip(), due_date=dt_str)
    
    if success:
        # Marca como nature = 'daily'
        try:
            conn = db.get_connection()
            with conn.cursor() as cur:
                cur.execute("UPDATE financial_records SET nature = 'daily' WHERE id = (SELECT MAX(id) FROM financial_records)")
            conn.commit()
            conn.close()
        except Exception:
            pass
        msg = f"✅ Gasto diário de R$ {valor:.2f} em '{categoria}' registrado com sucesso!"
    else:
        msg = "❌ Erro ao registrar gasto no banco de dados."
        
    k_rec, k_fix, k_liv, k_teto, st_card, df = load_daily_expenses_and_budget(periodo_opcao, mes_personalizado, cat_atual, busca_atual)
    return msg, k_rec, k_fix, k_liv, k_teto, st_card, df

# =====================================================================
# ABA 4: EXTRATO GERAL & RELATÓRIOS CONSOLIDADOS
# =====================================================================

def load_financial_table_and_kpis(
    periodo_opcao: str,
    mes_personalizado: str,
    tipo_filtro: str,
    categoria_filtro: str,
    busca_texto: str,
    mostrar_inativos: bool = False
):
    """Carrega o extrato contábil unificado com filtros gerais."""
    month_year = get_period_params(periodo_opcao, mes_personalizado)
    rec_type = tipo_filtro.lower() if tipo_filtro in ["Receita", "Despesa"] else None
    cat = None if categoria_filtro in ["Todas", "", None] else categoria_filtro
    query = busca_texto.strip() if busca_texto and busca_texto.strip() else None
    
    if mostrar_inativos:
        raw_records = db.get_deleted_financial_records()
    else:
        raw_records = db.search_financial_records(
            month_year=month_year,
            record_type=rec_type,
            category=cat,
            query=query,
            order_asc=False
        )
        
    total_receitas = 0.0
    total_despesas = 0.0
    table_rows = []
    today_date = datetime.now().date()
    contas_vencendo_hoje = 0
    
    for r in raw_records:
        rec_id, r_type, c_cat, val, desc, dt, due_dt = r
        r_type_clean = str(r_type).strip().lower()
        val_float = float(val)
        
        if r_type_clean == "receita":
            total_receitas += val_float
            type_display = "🟢 Receita"
        else:
            total_despesas += val_float
            type_display = "🔴 Despesa"
            if due_dt:
                d_date = due_dt.date() if isinstance(due_dt, datetime) else due_dt
                if d_date == today_date:
                    contas_vencendo_hoje += 1
                    
        dt_str = dt.strftime("%d/%m/%Y") if dt else "-"
        due_str = due_dt.strftime("%d/%m/%Y") if due_dt else "-"
        
        table_rows.append([
            rec_id,
            type_display,
            c_cat,
            f"R$ {val_float:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            desc or "",
            dt_str,
            due_str
        ])
        
    saldo = total_receitas - total_despesas
    saldo_color = "#10b981" if saldo >= 0 else "#ef4444"
    v_rec = f"R$ {total_receitas:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    v_desp = f"R$ {total_despesas:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    v_saldo = f"R$ {saldo:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    
    kpi_rec = f"## 🟢 {v_rec}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Total de Receitas</span>"
    kpi_desp = f"## 🔴 {v_desp}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Total de Despesas</span>"
    kpi_saldo = f"## <span style='color:{saldo_color}'>{v_saldo}</span>\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Saldo Líquido</span>"
    kpi_venc = f"## ⏰ {contas_vencendo_hoje} conta(s)\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Vencendo Hoje</span>"
    
    df = pd.DataFrame(
        table_rows,
        columns=["ID", "Tipo", "Categoria", "Valor", "Descrição", "Data Registro", "Vencimento"]
    )
    return kpi_rec, kpi_desp, kpi_saldo, kpi_venc, df

def generate_donut_chart(periodo_opcao: str, mes_personalizado: str = ""):
    """Gera gráfico de rosca de despesas por categoria."""
    month_year = get_period_params(periodo_opcao, mes_personalizado)
    exp_dict = db.get_expenses_by_category(month_year)
    
    fig, ax = plt.subplots(figsize=(7, 5), facecolor="#f8fafc")
    ax.set_facecolor("#f8fafc")
    
    if not exp_dict:
        ax.text(0.5, 0.5, "Nenhuma despesa encontrada para este período",
                horizontalalignment='center', verticalalignment='center',
                fontsize=12, color="#64748b")
        ax.axis('off')
        plt.tight_layout()
        return fig
        
    sorted_cats = sorted(exp_dict.items(), key=lambda x: x[1], reverse=True)
    if len(sorted_cats) > 8:
        top_items = sorted_cats[:7]
        outras_val = sum(v for _, v in sorted_cats[7:])
        labels = [k for k, _ in top_items] + ["Outras"]
        values = [v for _, v in top_items] + [outras_val]
    else:
        labels = [k for k, _ in sorted_cats]
        values = [v for _, v in sorted_cats]
        
    colors = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', '#06b6d4', '#ec4899', '#94a3b8']
    
    wedges, texts, autotexts = ax.pie(
        values,
        labels=labels,
        autopct='%1.1f%%',
        startangle=140,
        pctdistance=0.75,
        colors=colors[:len(values)],
        wedgeprops=dict(width=0.45, edgecolor='white', linewidth=2)
    )
    for autotext in autotexts:
        autotext.set_color('#1e293b')
        autotext.set_fontweight('bold')
        autotext.set_fontsize(9)
        
    periodo_titulo = f" ({month_year})" if month_year else " (Geral)"
    ax.set_title(f"Distribuição Geral de Despesas{periodo_titulo}", fontsize=13, fontweight='bold', pad=15)
    plt.tight_layout()
    return fig

def generate_monthly_comparison_chart(year_input: int = datetime.now().year):
    """Gera gráfico comparativo anual de Receitas vs Despesas."""
    overview = db.get_monthly_overview(year_input)
    fig, ax = plt.subplots(figsize=(9, 4.5), facecolor="#f8fafc")
    ax.set_facecolor("#f8fafc")
    
    meses = [item["mes_label"].split("/")[0] for item in overview]
    receitas = [item["receitas"] for item in overview]
    despesas = [item["despesas"] for item in overview]
    
    import numpy as np
    x = np.arange(len(meses))
    width = 0.38
    
    ax.bar(x - width/2, receitas, width, label='Receitas', color='#10b981', alpha=0.9, edgecolor='white')
    ax.bar(x + width/2, despesas, width, label='Despesas', color='#ef4444', alpha=0.9, edgecolor='white')
    
    ax.set_title(f"Evolução Mensal: Receitas vs. Despesas ({year_input})", fontsize=13, fontweight='bold', pad=15)
    ax.set_xticks(x)
    ax.set_xticklabels(meses, fontsize=10)
    ax.legend(frameon=True, facecolor='white', edgecolor='#e2e8f0')
    ax.grid(axis='y', linestyle='--', alpha=0.5)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, p: f"R$ {x:,.0f}".replace(",", ".")))
    plt.tight_layout()
    return fig

# =====================================================================
# ABA 5: CARTEIRA DE INVESTIMENTOS
# =====================================================================

def load_invest_macro_kpis():
    """Carrega os KPIs consolidados globais da carteira."""
    resumo = invest.get_resumo_patrimonial_geral()
    t_inv = resumo["total_investido"]
    t_atual = resumo["valor_atual"]
    lucro = resumo["lucro_total"]
    pct = resumo["rentabilidade_pct"]
    total_ativos = resumo["total_ativos"]
    
    lucro_cor = "green" if lucro >= 0 else "red"
    lucro_sinal = "+" if lucro > 0 else ""
    pct_sinal = "+" if pct > 0 else ""
    
    v_inv = f"R$ {t_inv:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    v_atual = f"R$ {t_atual:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    v_lucro = f"{lucro_sinal}R$ {lucro:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    
    kpi_inv = f"## 💼 {v_inv}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Total Investido (Custo)</span>"
    kpi_atual = f"## 📈 {v_atual}\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Patrimônio Total a Mercado</span>"
    kpi_lucro = f"## <span style='color:{lucro_cor}'>{v_lucro}</span>\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Lucro / Prejuízo Consolidado</span>"
    kpi_rent = f"## <span style='color:{lucro_cor}'>{pct_sinal}{pct:.2f}%</span>\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Rentabilidade Global</span>"
    kpi_qtd = f"## 🏛️ {total_ativos} ativo(s)\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Renda Fixa + Ações</span>"
    
    g_macro = generate_invest_macro_chart()
    g_ativos = generate_invest_assets_chart()
    return kpi_atual, kpi_inv, kpi_lucro, kpi_rent, kpi_qtd, g_macro, g_ativos

def generate_invest_macro_chart():
    """Gera gráfico de rosca de alocação macro (Renda Fixa vs Ações)."""
    alloc = invest.get_alocacao_macro()
    fig, ax = plt.subplots(figsize=(5.5, 4.2), facecolor="#f8fafc")
    ax.set_facecolor("#f8fafc")
    if not alloc:
        ax.text(0.5, 0.5, "Nenhum investimento cadastrado", horizontalalignment='center', verticalalignment='center', fontsize=11, color="#64748b")
        ax.axis('off')
        plt.tight_layout()
        return fig
    labels = list(alloc.keys())
    values = list(alloc.values())
    colors = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6']
    wedges, texts, autotexts = ax.pie(values, labels=labels, autopct='%1.1f%%', startangle=140, pctdistance=0.75, colors=colors[:len(values)], wedgeprops=dict(width=0.45, edgecolor='white', linewidth=2))
    for autotext in autotexts:
        autotext.set_color('#1e293b')
        autotext.set_fontweight('bold')
        autotext.set_fontsize(9)
    ax.set_title("Alocação Patrimonial (Classe)", fontsize=11, fontweight='bold', pad=12)
    plt.tight_layout()
    return fig

def generate_invest_assets_chart():
    """Gera gráfico de barras de patrimônio por ativo/ticker."""
    alloc = invest.get_alocacao_por_ativo()
    fig, ax = plt.subplots(figsize=(6.5, 4.2), facecolor="#f8fafc")
    ax.set_facecolor("#f8fafc")
    if not alloc:
        ax.text(0.5, 0.5, "Nenhum ativo cadastrado", horizontalalignment='center', verticalalignment='center', fontsize=11, color="#64748b")
        ax.axis('off')
        plt.tight_layout()
        return fig
    sorted_items = sorted(alloc.items(), key=lambda x: x[1], reverse=True)[:8]
    ativos = [item[0] for item in sorted_items]
    valores = [item[1] for item in sorted_items]
    y_pos = range(len(ativos))
    ax.barh(y_pos, valores, color='#10b981', edgecolor='white', alpha=0.9, height=0.55)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(ativos, fontsize=9)
    ax.invert_yaxis()
    ax.set_title("Maiores Posições em Carteira (R$)", fontsize=11, fontweight='bold', pad=12)
    ax.grid(axis='x', linestyle='--', alpha=0.5)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, p: f"R$ {x:,.0f}".replace(",", ".")))
    plt.tight_layout()
    return fig

def load_acoes_tab(filtro_ticker: str = "", filtro_op: str = "Todas"):
    """Carrega tabela consolidada de ações e tabela de movimentações."""
    # 1. Consolidado
    posicoes = invest.get_consolidado_acoes(fetch_market_prices=True)
    rows_cons = []
    for p in posicoes:
        if p["quantidade_custodia"] <= 0 and p["lucro_realizado"] == 0:
            continue
        cod = p["codigo_acao"]
        qtd = p["quantidade_custodia"]
        pm = p["preco_medio"]
        custo = p["custo_total"]
        cot = p["cotacao_atual"]
        v_merc = p["valor_mercado"]
        luc_nao_real = p["lucro_nao_realizado"]
        rent = p["rentabilidade_pct"]
        luc_real = p["lucro_realizado"]
        
        sinal_l = "+" if luc_nao_real > 0 else ""
        sinal_r = "+" if rent > 0 else ""
        sinal_lr = "+" if luc_real > 0 else ""
        
        rows_cons.append([
            cod,
            qtd,
            f"R$ {pm:.2f}".replace(".", ","),
            f"R$ {custo:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            f"R$ {cot:.2f}".replace(".", ",") if cot > 0 else "-",
            f"R$ {v_merc:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            f"{sinal_l}R$ {luc_nao_real:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            f"{sinal_r}{rent:.2f}%",
            f"{sinal_lr}R$ {luc_real:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        ])
    df_cons = pd.DataFrame(rows_cons, columns=["Código", "Custódia", "Preço Médio", "Custo Total", "Cotação Atual", "Valor Mercado", "Lucro Não Realizado", "Rentabilidade", "Lucro Realizado"])
    
    # 2. Movimentações
    movs = invest.get_movimentacoes_acoes(codigo_acao=filtro_ticker, operacao=filtro_op)
    rows_mov = []
    for m in movs:
        dt_str = m["data_operacao"].strftime("%d/%m/%Y") if hasattr(m["data_operacao"], "strftime") else str(m["data_operacao"])
        rows_mov.append([
            m["id"],
            dt_str,
            m["codigo_acao"],
            m["operacao"],
            m["quantidade"],
            f"R$ {m['preco_unitario']:.2f}".replace(".", ","),
            f"R$ {m['taxas']:.2f}".replace(".", ","),
            f"R$ {m['valor_total']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        ])
    df_mov = pd.DataFrame(rows_mov, columns=["ID", "Data", "Código", "Operação", "Qtd", "Preço Unit.", "Taxas", "Valor Total"])
    return df_cons, df_mov

def load_renda_fixa_tab(filtro_tipo: str = "Todas"):
    """Carrega tabela consolidada de renda fixa e tabela de movimentações."""
    # 1. Consolidado
    titulos = invest.get_consolidado_renda_fixa(active_only=True)
    rows_cons = []
    dropdown_titulos = []
    for t in titulos:
        dropdown_titulos.append(f"#{t['id']} - {t['nome_titulo']}")
        s_inv = t["saldo_investido"]
        t_ap = t["total_aportado"]
        t_resg = t["total_resgatado"]
        t_jur = t["juros_recebidos"]
        t_imp = t["impostos"]
        v_at = t["valor_atual"]
        luc = t["lucro_rendimento"]
        pct = t["rentabilidade_pct"]
        sinal_luc = "+" if luc > 0 else ""
        sinal_pct = "+" if pct > 0 else ""
        
        rows_cons.append([
            t["id"],
            t["nome_titulo"],
            t["nome_banco"],
            t["tipo_investimento"],
            f"R$ {s_inv:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            f"R$ {t_ap:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            f"R$ {t_resg:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            f"R$ {t_jur:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            f"R$ {t_imp:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            f"R$ {v_at:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            f"{sinal_luc}R$ {luc:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            f"{sinal_pct}{pct:.2f}%"
        ])
    df_cons = pd.DataFrame(rows_cons, columns=["ID", "Título / Ativo", "Instituição", "Classe", "Saldo Investido", "Total Aportado", "Total Resgatado", "Juros", "Impostos", "Valor Atual", "Rendimento", "Rentabilidade"])
    
    # 2. Movimentações
    movs = invest.get_movimentacoes_renda_fixa(tipo=filtro_tipo)
    rows_mov = []
    for m in movs:
        dt_str = m["data_movimentacao"].strftime("%d/%m/%Y") if hasattr(m["data_movimentacao"], "strftime") else str(m["data_movimentacao"])
        rows_mov.append([
            m["id"],
            m["nome_titulo"],
            m["nome_banco"],
            dt_str,
            m["tipo_movimentacao"],
            f"R$ {m['valor']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        ])
    df_mov = pd.DataFrame(rows_mov, columns=["ID", "Título", "Instituição", "Data", "Tipo Movimentação", "Valor"])
    return df_cons, df_mov, gr.update(choices=dropdown_titulos), gr.update(choices=dropdown_titulos)

def add_acao_action(codigo: str, operacao: str, qtd: float, preco: float, taxas: float, data_op: str):
    """Registra uma movimentação de ação."""
    if not codigo or not codigo.strip():
        return "❌ Código da ação é obrigatório."
    if qtd <= 0 or preco <= 0:
        return "❌ Quantidade e preço devem ser maiores que zero."
    ok, msg = invest.add_movimentacao_acao(
        codigo_acao=codigo,
        operacao=operacao,
        quantidade=int(qtd),
        preco_unitario=float(preco),
        taxas=float(taxas or 0.0),
        data_operacao=data_op.strip() if data_op else None
    )
    return f"✅ {msg}" if ok else f"❌ {msg}"

def add_rf_mov_action(id_titulo_str: str, tipo_mov: str, valor: float, data_mov: str):
    """Registra uma movimentação em título de renda fixa."""
    if not id_titulo_str:
        return "❌ Selecione um título válido."
    try:
        match = re.search(r'#(\d+)', id_titulo_str)
        t_id = int(match.group(1)) if match else int(id_titulo_str)
    except Exception:
        return "❌ ID do título inválido."
        
    ok, msg = invest.add_movimentacao_renda_fixa(
        id_investimento=t_id,
        tipo_movimentacao=tipo_mov,
        valor=float(valor or 0.0),
        data_movimentacao=data_mov.strip() if data_mov else None
    )
    return f"✅ {msg}" if ok else f"❌ {msg}"

def add_rf_titulo_action(nome: str, banco: str, tipo: str, dt_ini: str, valor_ini: float):
    """Cadastra um novo título de renda fixa."""
    if not nome or not nome.strip():
        return "❌ O nome do título é obrigatório."
    new_id = invest.cadastrar_titulo_renda_fixa(
        nome_titulo=nome,
        nome_banco=banco,
        tipo_investimento=tipo,
        data_inicio=dt_ini.strip() if dt_ini else None,
        valor_inicial=float(valor_ini or 0.0)
    )
    if new_id:
        return f"✅ Título '{nome}' cadastrado com sucesso (ID #{new_id})!"
    return f"❌ Erro ao cadastrar título '{nome}'."

def update_rf_valor_action(id_titulo_str: str, novo_valor: float):
    """Atualiza o valor de mercado de um título de renda fixa."""
    if not id_titulo_str:
        return "❌ Selecione um título válido."
    try:
        match = re.search(r'#(\d+)', id_titulo_str)
        t_id = int(match.group(1)) if match else int(id_titulo_str)
    except Exception:
        return "❌ ID do título inválido."
        
    ok, msg = invest.update_valor_atual_renda_fixa(t_id, float(novo_valor or 0.0))
    return f"✅ {msg}" if ok else f"❌ {msg}"

def migrar_legados_dashboard_action():
    """Aciona migração de legados pelo dashboard."""
    res = invest.migrar_investimentos_legados()
    msgs = res.get("mensagens", [])
    if not msgs:
        return "ℹ️ Nenhuma migração necessária (tabelas de movimentação já continham registros ou não havia legados pendentes)."
    return "✅ Migração concluída com sucesso:\n" + "\n".join(f"- {m}" for m in msgs)

# =====================================================================
# CONSTRUÇÃO DA INTERFACE GRADIO
# =====================================================================

def build_dashboard():
    """Constrói a aplicação web completa com todas as visões integradas."""
    initial_cats = ["Todas"] + db.get_financial_categories()
    initial_cards = [c for c in db.get_distinct_cards() if c != "Cartão de Crédito"]
    card_choices = ["Todos os Cartões"] + initial_cards
    initial_invest_types = ["Todas"] + invest.get_distinct_investment_types()
    initial_invest_banks = ["Todos"] + invest.get_distinct_banks()
    
    theme = gr.themes.Soft(
        primary_hue="blue",
        secondary_hue="emerald",
        neutral_hue="slate"
    )
    
    with gr.Blocks(title="Meu Agente - Gestão Financeira & Investimentos") as app:
        gr.Markdown(
            """
            # 💼 Meu Agente - Gestão Financeira & Investimentos
            Controle integrado de contas fixas mensais, compras de cartões, gastos diários e investimentos.
            """
        )
        
        with gr.Tabs():
            # -------------------------------------------------------------
            # ABA 1: CONTAS MENSAIS & FATURAS (CUSTOS FIXOS)
            # -------------------------------------------------------------
            with gr.TabItem("📅 Contas Mensais & Faturas"):
                gr.Markdown("### 🔒 Custos Fixos & Faturas Consolidadas do Mês")
                gr.Markdown("Acompanhe os compromissos mensais (aluguel, condomínio, luz, internet e faturas de cartão) e gerencie o checklist de pagamentos.")
                
                with gr.Row():
                    with gr.Column(scale=1):
                        kpi_m_total = gr.Markdown("## 📅 R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Compromissos do Mês</span>")
                    with gr.Column(scale=1):
                        kpi_m_pago = gr.Markdown("## 🟢 R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Contas Liquidadas</span>")
                    with gr.Column(scale=1):
                        kpi_m_pend = gr.Markdown("## ⏳ R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>A Pagar no Mês</span>")
                    with gr.Column(scale=1):
                        kpi_m_prox = gr.Markdown("## ⏰ -\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Vencimento Mais Próximo</span>")
                        
                gr.Markdown("---")
                
                with gr.Row():
                    filtro_m_periodo = gr.Radio(
                        choices=["Mês Atual", "Próximo Mês", "Mês Específico"],
                        value="Mês Atual",
                        label="📅 Mês de Referência"
                    )
                    filtro_m_custom = gr.Textbox(
                        label="Mês/Ano (MM-YYYY)",
                        placeholder="Ex: 09-2026",
                        visible=False
                    )
                    btn_m_atualizar = gr.Button("🔄 Atualizar Contas", variant="primary", scale=1)
                    btn_m_projetar_ano = gr.Button("🔮 Previsão Anual de Gastos Fixos", variant="secondary", scale=1)
                    
                filtro_m_periodo.change(toggle_custom_month, inputs=[filtro_m_periodo], outputs=[filtro_m_custom])
                msg_m_proj = gr.Markdown()
                
                tabela_m_contas = gr.Dataframe(
                    headers=["ID", "Conta / Fatura", "Categoria", "Vencimento", "Valor", "Status", "Tipo"],
                    datatype=["str", "str", "str", "str", "str", "str", "str"],
                    interactive=False,
                    wrap=True
                )
                
                m_filter_inputs = [filtro_m_periodo, filtro_m_custom]
                m_filter_outputs = [kpi_m_total, kpi_m_pago, kpi_m_pend, kpi_m_prox, tabela_m_contas]
                
                btn_m_atualizar.click(load_monthly_bills_tab, inputs=m_filter_inputs, outputs=m_filter_outputs)
                filtro_m_periodo.change(load_monthly_bills_tab, inputs=m_filter_inputs, outputs=m_filter_outputs)
                
                btn_m_projetar_ano.click(
                    lambda p, c: project_annual_fixed_expenses_action(datetime.now().year, p, c),
                    inputs=[filtro_m_periodo, filtro_m_custom],
                    outputs=[msg_m_proj, kpi_m_total, kpi_m_pago, kpi_m_pend, kpi_m_prox, tabela_m_contas]
                )
                
                gr.Markdown("---")
                
                # Linha 1: Liquidação e Exclusão Rápida
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### ✅ Marcar Pagamento de Conta / Fatura\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Dica: Clique em qualquer linha da tabela para preencher os campos automaticamente.</span>")
                        input_m_id = gr.Textbox(label="ID da Conta ou Fatura", placeholder="Ex: 121 (conta) ou card_Itau (fatura)")
                        with gr.Row():
                            btn_marcar_pago = gr.Button("🟢 Marcar como Paga", variant="primary")
                            btn_reabrir_pago = gr.Button("↩️ Reabrir (Pendente)", variant="secondary")
                        msg_m_acao = gr.Markdown()
                        
                        btn_marcar_pago.click(
                            lambda rid, p, c: toggle_bill_payment_action(rid, True, p, c),
                            inputs=[input_m_id, filtro_m_periodo, filtro_m_custom],
                            outputs=[msg_m_acao, kpi_m_total, kpi_m_pago, kpi_m_pend, kpi_m_prox, tabela_m_contas]
                        )
                        btn_reabrir_pago.click(
                            lambda rid, p, c: toggle_bill_payment_action(rid, False, p, c),
                            inputs=[input_m_id, filtro_m_periodo, filtro_m_custom],
                            outputs=[msg_m_acao, kpi_m_total, kpi_m_pago, kpi_m_pend, kpi_m_prox, tabela_m_contas]
                        )
                        
                    with gr.Column(scale=1):
                        gr.Markdown("### 🗑️ Excluir Conta Fixa\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Inativa logicamente a entrada do mês preservando a integridade dos dados.</span>")
                        del_m_id = gr.Textbox(label="ID da Conta a Excluir", placeholder="Ex: 121")
                        btn_excluir_m = gr.Button("🗑️ Excluir Conta Fixa", variant="stop")
                        msg_del_m = gr.Markdown()
                        
                        btn_excluir_m.click(
                            delete_monthly_bill_action,
                            inputs=[del_m_id, filtro_m_periodo, filtro_m_custom],
                            outputs=[msg_del_m, kpi_m_total, kpi_m_pago, kpi_m_pend, kpi_m_prox, tabela_m_contas]
                        )

                gr.Markdown("---")

                # Linha 2: Edição e Cadastro de Nova Conta
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### ✏️ Atualizar Informações da Conta Fixa\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Altere nome, categoria, valor ou data de vencimento da conta selecionada.</span>")
                        with gr.Row():
                            edit_m_id = gr.Textbox(label="ID da Conta", placeholder="Ex: 121", scale=2)
                            btn_carregar_edit = gr.Button("🔍 Carregar Dados", scale=1)
                        with gr.Row():
                            edit_m_nome = gr.Textbox(label="Nome / Descrição da Conta", placeholder="Ex: Condomínio Edifício...")
                            edit_m_cat = gr.Dropdown(choices=["Condomínio", "Casa", "Seguro", "Energia", "Internet", "Telefonia", "Curso", "Impostos", "Saúde", "Outros"], value="Condomínio", allow_custom_value=True, label="Categoria")
                        with gr.Row():
                            edit_m_valor = gr.Number(label="Novo Valor (R$)", value=0.0)
                            edit_m_venc = gr.Textbox(label="Data de Vencimento", placeholder="YYYY-MM-DD ou Dia (1-31)")
                        edit_m_propagar = gr.Checkbox(label="Propagar este novo valor para os meses seguintes deste ano", value=False)
                        btn_salvar_edit = gr.Button("💾 Salvar Alterações", variant="primary")
                        msg_edit_m = gr.Markdown()
                        
                        btn_carregar_edit.click(
                            load_bill_data_for_edit,
                            inputs=[edit_m_id],
                            outputs=[edit_m_nome, edit_m_cat, edit_m_valor, edit_m_venc, msg_edit_m]
                        )
                        btn_salvar_edit.click(
                            update_monthly_bill_action,
                            inputs=[edit_m_id, edit_m_nome, edit_m_cat, edit_m_valor, edit_m_venc, edit_m_propagar, filtro_m_periodo, filtro_m_custom],
                            outputs=[msg_edit_m, kpi_m_total, kpi_m_pago, kpi_m_pend, kpi_m_prox, tabela_m_contas]
                        )

                    with gr.Column(scale=1):
                        gr.Markdown("### ➕ Cadastrar Nova Conta Fixa Recorrente\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Cadastra um compromisso recorrente e lança automaticamente no mês.</span>")
                        with gr.Row():
                            novo_m_nome = gr.Textbox(label="Nome da Conta", placeholder="Ex: Condomínio, Aluguel, Internet Fibra...")
                            novo_m_cat = gr.Dropdown(choices=["Condomínio", "Casa", "Seguro", "Energia", "Internet", "Telefonia", "Curso", "Impostos", "Saúde", "Outros"], value="Condomínio", allow_custom_value=True, label="Categoria")
                        with gr.Row():
                            novo_m_valor = gr.Number(label="Valor Estimado / Fixo (R$)", value=0.0)
                            novo_m_dia = gr.Number(label="Dia do Vencimento (1-31)", value=10, precision=0)
                        novo_m_projetar = gr.Checkbox(label="Projetar para todos os meses do ano", value=True)
                        btn_salvar_m = gr.Button("💾 Cadastrar Conta Fixa", variant="primary")
                        msg_novo_m = gr.Markdown()
                        
                        btn_salvar_m.click(
                            add_new_monthly_bill_action,
                            inputs=[novo_m_nome, novo_m_cat, novo_m_valor, novo_m_dia, novo_m_projetar, filtro_m_periodo, filtro_m_custom],
                            outputs=[msg_novo_m, kpi_m_total, kpi_m_pago, kpi_m_pend, kpi_m_prox, tabela_m_contas]
                        )

                # Evento de clique na linha da tabela para autopreenchimento
                tabela_m_contas.select(
                    on_select_monthly_bill,
                    inputs=[tabela_m_contas],
                    outputs=[input_m_id, del_m_id, edit_m_id, edit_m_nome, edit_m_cat, edit_m_valor, edit_m_venc]
                )

            # -------------------------------------------------------------
            # ABA 2: RECEITAS & ENTRADAS
            # -------------------------------------------------------------
            with gr.TabItem("💵 Receitas & Entradas"):
                gr.Markdown("### 💵 Gestão de Receitas & Entradas Financeiras")
                gr.Markdown("Cadastre suas fontes de renda (salário, freelances, dividendos, vendas), acompanhe os recebimentos do mês e atualize registros.")
                
                with gr.Row():
                    with gr.Column(scale=1):
                        kpi_r_total = gr.Markdown("## 🟢 R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Total de Receitas no Mês</span>")
                    with gr.Column(scale=1):
                        kpi_r_salario = gr.Markdown("## 💼 R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Salário / Renda Principal</span>")
                    with gr.Column(scale=1):
                        kpi_r_extras = gr.Markdown("## 📈 R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Rendas Extras / Outras</span>")
                    with gr.Column(scale=1):
                        kpi_r_qtd = gr.Markdown("## 🔢 0 entrada(s)\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Lançamentos no Mês</span>")
                        
                gr.Markdown("---")
                
                with gr.Row():
                    filtro_r_periodo = gr.Radio(
                        choices=["Mês Atual", "Próximo Mês", "Mês Específico"],
                        value="Mês Atual",
                        label="📅 Mês de Referência"
                    )
                    filtro_r_custom = gr.Textbox(
                        label="Mês/Ano (MM-YYYY)",
                        placeholder="Ex: 09-2026",
                        visible=False
                    )
                    filtro_r_cat = gr.Dropdown(
                        choices=["Todas"] + RECEITA_CATEGORIAS,
                        value="Todas",
                        label="🏷️ Categoria"
                    )
                    btn_r_atualizar = gr.Button("🔄 Atualizar Receitas", variant="primary", scale=1)
                    
                filtro_r_periodo.change(toggle_custom_month, inputs=[filtro_r_periodo], outputs=[filtro_r_custom])
                
                tabela_r_receitas = gr.Dataframe(
                    headers=["ID", "Descrição / Fonte", "Categoria", "Data / Previsão", "Valor"],
                    datatype=["str", "str", "str", "str", "str"],
                    interactive=False,
                    wrap=True
                )
                
                r_filter_inputs = [filtro_r_periodo, filtro_r_custom, filtro_r_cat]
                r_filter_outputs = [kpi_r_total, kpi_r_salario, kpi_r_extras, kpi_r_qtd, tabela_r_receitas]
                
                btn_r_atualizar.click(load_incomes_tab, inputs=r_filter_inputs, outputs=r_filter_outputs)
                filtro_r_periodo.change(load_incomes_tab, inputs=r_filter_inputs, outputs=r_filter_outputs)
                filtro_r_cat.change(load_incomes_tab, inputs=r_filter_inputs, outputs=r_filter_outputs)
                
                gr.Markdown("---")
                
                # Exclusão rápida
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### 🗑️ Excluir Receita\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Dica: Clique em qualquer linha da tabela para selecionar a receita automaticamente.</span>")
                        del_r_id = gr.Textbox(label="ID da Receita a Excluir", placeholder="Ex: 10")
                        btn_excluir_r = gr.Button("🗑️ Excluir Receita", variant="stop")
                        msg_del_r = gr.Markdown()
                        
                        btn_excluir_r.click(
                            delete_income_action,
                            inputs=[del_r_id, filtro_r_periodo, filtro_r_custom, filtro_r_cat],
                            outputs=[msg_del_r, kpi_r_total, kpi_r_salario, kpi_r_extras, kpi_r_qtd, tabela_r_receitas]
                        )
                        
                gr.Markdown("---")
                
                # Edição e Novo Cadastro
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### ✏️ Atualizar Informações da Receita\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Altere descrição, categoria, valor ou data da receita selecionada.</span>")
                        with gr.Row():
                            edit_r_id = gr.Textbox(label="ID da Receita", placeholder="Ex: 10", scale=2)
                            btn_carregar_edit_r = gr.Button("🔍 Carregar Dados", scale=1)
                        with gr.Row():
                            edit_r_desc = gr.Textbox(label="Descrição / Fonte da Receita", placeholder="Ex: Salário Empresa...")
                            edit_r_cat = gr.Dropdown(choices=RECEITA_CATEGORIAS, value="Salário", allow_custom_value=True, label="Categoria")
                        with gr.Row():
                            edit_r_valor = gr.Number(label="Novo Valor (R$)", value=0.0)
                            edit_r_data = gr.Textbox(label="Data de Recebimento", placeholder="YYYY-MM-DD ou Dia (1-31)")
                        btn_salvar_edit_r = gr.Button("💾 Salvar Alterações", variant="primary")
                        msg_edit_r = gr.Markdown()
                        
                        btn_carregar_edit_r.click(
                            load_income_data_for_edit,
                            inputs=[edit_r_id],
                            outputs=[edit_r_desc, edit_r_cat, edit_r_valor, edit_r_data, msg_edit_r]
                        )
                        btn_salvar_edit_r.click(
                            update_income_action,
                            inputs=[edit_r_id, edit_r_desc, edit_r_cat, edit_r_valor, edit_r_data, filtro_r_periodo, filtro_r_custom, filtro_r_cat],
                            outputs=[msg_edit_r, kpi_r_total, kpi_r_salario, kpi_r_extras, kpi_r_qtd, tabela_r_receitas]
                        )

                    with gr.Column(scale=1):
                        gr.Markdown("### ➕ Cadastrar Nova Receita\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Registre uma nova entrada de dinheiro (salário, freelance, dividendos, etc.).</span>")
                        with gr.Row():
                            novo_r_desc = gr.Textbox(label="Descrição / Fonte da Receita", placeholder="Ex: Salário Empresa, Freela Consultoria...")
                            novo_r_cat = gr.Dropdown(choices=RECEITA_CATEGORIAS, value="Salário", allow_custom_value=True, label="Categoria")
                        with gr.Row():
                            novo_r_valor = gr.Number(label="Valor (R$)", value=0.0)
                            novo_r_data = gr.Textbox(label="Data (YYYY-MM-DD ou Dia 1-31)", value=datetime.now().strftime("%Y-%m-%d"))
                        btn_salvar_r = gr.Button("💾 Cadastrar Receita", variant="primary")
                        msg_novo_r = gr.Markdown()
                        
                        btn_salvar_r.click(
                            add_income_action,
                            inputs=[novo_r_desc, novo_r_cat, novo_r_valor, novo_r_data, filtro_r_periodo, filtro_r_custom, filtro_r_cat],
                            outputs=[msg_novo_r, kpi_r_total, kpi_r_salario, kpi_r_extras, kpi_r_qtd, tabela_r_receitas]
                        )

                # Evento de clique na linha da tabela de receitas para autopreenchimento
                tabela_r_receitas.select(
                    on_select_income,
                    inputs=[tabela_r_receitas],
                    outputs=[del_r_id, edit_r_id, edit_r_desc, edit_r_cat, edit_r_valor, edit_r_data]
                )

            # -------------------------------------------------------------
            # ABA 3: CARTÕES DE CRÉDITO (EXTRATO POR CARTÃO)
            # -------------------------------------------------------------
            with gr.TabItem("💳 Cartões de Crédito"):
                gr.Markdown("### 🛍️ Extrato Detalhado por Cartão de Crédito")
                gr.Markdown("Consulte as compras e parcelas de cada cartão individualmente. O valor total da fatura calculada alimenta automaticamente a aba de Contas Mensais.")
                
                with gr.Row():
                    filtro_c_cartao = gr.Dropdown(
                        choices=card_choices,
                        value="Todos os Cartões",
                        label="💳 Selecione o Cartão",
                        interactive=True
                    )
                    filtro_c_periodo = gr.Radio(
                        choices=["Mês Atual", "Próximo Mês", "Mês Específico"],
                        value="Mês Atual",
                        label="📅 Mês da Fatura"
                    )
                    filtro_c_custom = gr.Textbox(
                        label="Mês/Ano (MM-YYYY)",
                        placeholder="Ex: 09-2026",
                        visible=False
                    )
                    btn_c_atualizar = gr.Button("🔄 Atualizar Extrato", variant="primary", scale=1)
                    
                filtro_c_periodo.change(toggle_custom_month, inputs=[filtro_c_periodo], outputs=[filtro_c_custom])
                
                with gr.Row():
                    with gr.Column(scale=1):
                        kpi_c_total = gr.Markdown("## 💳 R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Fatura no Mês</span>")
                    with gr.Column(scale=1):
                        kpi_c_venc = gr.Markdown("## 📅 -\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Vencimento da Fatura</span>")
                    with gr.Column(scale=1):
                        kpi_c_qtd = gr.Markdown("## 🛍️ 0 compras\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Itens na Fatura</span>")
                    with gr.Column(scale=1):
                        kpi_c_status = gr.Markdown("## ⏳ Aberta\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Situação do Pagamento</span>")
                        
                tabela_c_compras = gr.Dataframe(
                    headers=["ID", "Cartão", "Descrição da Compra", "Categoria", "Valor", "Vencimento na Fatura", "Data da Compra", "Status"],
                    datatype=["number", "str", "str", "str", "str", "str", "str", "str"],
                    interactive=False,
                    wrap=True
                )
                
                c_filter_inputs = [filtro_c_cartao, filtro_c_periodo, filtro_c_custom]
                c_filter_outputs = [kpi_c_total, kpi_c_venc, kpi_c_qtd, kpi_c_status, tabela_c_compras]
                
                btn_c_atualizar.click(refresh_card_purchases_tab, inputs=c_filter_inputs, outputs=[filtro_c_cartao, *c_filter_outputs])
                filtro_c_cartao.change(load_card_purchases_tab, inputs=c_filter_inputs, outputs=c_filter_outputs)
                filtro_c_periodo.change(load_card_purchases_tab, inputs=c_filter_inputs, outputs=c_filter_outputs)
                
                gr.Markdown("---")
                
                # Formulário para lançar compra ou estorno no cartão
                gr.Markdown("### ➕ Lançar Compra ou Estorno no Cartão\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Para estornos ou créditos que abatem a fatura, basta digitar o valor com sinal negativo (ex: -50.00).</span>")
                with gr.Row():
                    novo_c_cartao = gr.Dropdown(choices=initial_cards if initial_cards else ["BB", "C6", "Itau", "Porto-Seguro"], value=initial_cards[0] if initial_cards else "BB", label="Cartão Utilizado", allow_custom_value=True)
                    novo_c_cat = gr.Dropdown(choices=["Alimentação", "Supermercado", "Eletrônicos", "Vestuário", "Lazer", "Farmácia", "Assinatura", "Estorno", "Outros"], value="Supermercado", allow_custom_value=True, label="Categoria")
                    novo_c_valor = gr.Number(label="Valor (R$) [negativo para estornos]", value=0.0)
                    novo_c_parcelas = gr.Number(label="Número de Parcelas (1 = à vista)", value=1, precision=0)
                with gr.Row():
                    novo_c_desc = gr.Textbox(label="Descrição da Compra / Estorno", placeholder="Ex: Supermercado Pão de Açúcar, Estorno compra cancelada...")
                    novo_c_data = gr.Textbox(label="Data da Operação (YYYY-MM-DD)", value=datetime.now().strftime("%Y-%m-%d"))
                    btn_salvar_c = gr.Button("💳 Lançar no Cartão", variant="primary", scale=1)
                msg_novo_c = gr.Markdown()
                
                btn_salvar_c.click(
                    add_card_purchase_action,
                    inputs=[novo_c_cartao, novo_c_cat, novo_c_valor, novo_c_parcelas, novo_c_desc, novo_c_data, filtro_c_periodo, filtro_c_custom],
                    outputs=[msg_novo_c, filtro_c_cartao, kpi_c_total, kpi_c_venc, kpi_c_qtd, kpi_c_status, tabela_c_compras]
                )

            # -------------------------------------------------------------
            # ABA 3: GASTOS DIÁRIOS & TETO POR DIA (ORÇAMENTO)
            # -------------------------------------------------------------
            with gr.TabItem("☕ Gastos Diários & Teto por Dia"):
                gr.Markdown("### 🎯 Painel Orçamentário e Ritmo de Gastos Diários")
                gr.Markdown("Acompanhe o seu saldo livre e o **Teto Diário Recomendado** para não estourar o orçamento até o final do mês.")
                
                with gr.Row():
                    filtro_d_periodo = gr.Radio(
                        choices=["Mês Atual", "Próximo Mês", "Mês Específico"],
                        value="Mês Atual",
                        label="📅 Mês de Referência"
                    )
                    filtro_d_custom = gr.Textbox(
                        label="Mês/Ano (MM-YYYY)",
                        placeholder="Ex: 09-2026",
                        visible=False
                    )
                    btn_d_atualizar = gr.Button("🔄 Atualizar Orçamento", variant="primary", scale=1)
                    
                filtro_d_periodo.change(toggle_custom_month, inputs=[filtro_d_periodo], outputs=[filtro_d_custom])
                
                with gr.Row():
                    with gr.Column(scale=1):
                        kpi_d_rec = gr.Markdown("## 🟢 R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Receitas Consolidadas</span>")
                    with gr.Column(scale=1):
                        kpi_d_fixas = gr.Markdown("## 🔒 R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Comprometido no Mês</span>")
                    with gr.Column(scale=1):
                        kpi_d_livre = gr.Markdown("## 🛍️ R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Disponível para Gastos Diários</span>")
                    with gr.Column(scale=1):
                        kpi_d_teto = gr.Markdown("## 🎯 R$ 0,00 / dia\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Limite Sugerido por Dia</span>")
                        
                # Card de status de hoje
                status_card_d = gr.HTML()
                
                gr.Markdown("---")
                gr.Markdown("### ☕ Extrato de Gastos Diários e Rotina")
                
                with gr.Row():
                    filtro_d_cat = gr.Dropdown(choices=["Todas", "Alimentação", "Padaria", "Combustível", "Transporte", "Farmácia", "Lazer", "Outros"], value="Todas", label="Categoria")
                    filtro_d_busca = gr.Textbox(label="🔍 Busca Rápida", placeholder="Pesquisar por almoço, uber, lanche...")
                    
                tabela_d_gastos = gr.Dataframe(
                    headers=["ID", "Categoria", "Valor", "Descrição", "Data"],
                    datatype=["number", "str", "str", "str", "str"],
                    interactive=False,
                    wrap=True
                )
                
                d_filter_inputs = [filtro_d_periodo, filtro_d_custom, filtro_d_cat, filtro_d_busca]
                d_filter_outputs = [kpi_d_rec, kpi_d_fixas, kpi_d_livre, kpi_d_teto, status_card_d, tabela_d_gastos]
                
                btn_d_atualizar.click(load_daily_expenses_and_budget, inputs=d_filter_inputs, outputs=d_filter_outputs)
                filtro_d_periodo.change(load_daily_expenses_and_budget, inputs=d_filter_inputs, outputs=d_filter_outputs)
                filtro_d_cat.change(load_daily_expenses_and_budget, inputs=d_filter_inputs, outputs=d_filter_outputs)
                filtro_d_busca.submit(load_daily_expenses_and_budget, inputs=d_filter_inputs, outputs=d_filter_outputs)
                
                gr.Markdown("---")
                
                # Inclusão de gasto diário rápido
                gr.Markdown("### ➕ Registrar Gasto Diário Rápido")
                with gr.Row():
                    novo_d_cat = gr.Dropdown(choices=["Alimentação", "Padaria", "Combustível", "Transporte", "Farmácia", "Lazer", "Outros"], value="Alimentação", allow_custom_value=True, label="Categoria")
                    novo_d_val = gr.Number(label="Valor (R$)", value=0.0)
                    novo_d_desc = gr.Textbox(label="Descrição", placeholder="Ex: Almoço restaurante, café, padaria...")
                    novo_d_dt = gr.Textbox(label="Data (YYYY-MM-DD)", value=datetime.now().strftime("%Y-%m-%d"))
                    btn_salvar_d = gr.Button("💾 Registrar Gasto Diário", variant="primary", scale=1)
                msg_novo_d = gr.Markdown()
                
                btn_salvar_d.click(
                    add_quick_daily_expense,
                    inputs=[novo_d_cat, novo_d_val, novo_d_desc, novo_d_dt, filtro_d_periodo, filtro_d_custom, filtro_d_cat, filtro_d_busca],
                    outputs=[msg_novo_d, kpi_d_rec, kpi_d_fixas, kpi_d_livre, kpi_d_teto, status_card_d, tabela_d_gastos]
                )

            # -------------------------------------------------------------
            # ABA 4: EXTRATO GERAL & RELATÓRIOS CONSOLIDADOS
            # -------------------------------------------------------------
            with gr.TabItem("📊 Extrato Geral & Relatórios"):
                gr.Markdown("### 📑 Visão Contábil Unificada & Gráficos")
                
                with gr.Row():
                    with gr.Column(scale=1):
                        kpi_g_rec = gr.Markdown("## 🟢 R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Total de Receitas</span>")
                    with gr.Column(scale=1):
                        kpi_g_desp = gr.Markdown("## 🔴 R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Total de Despesas</span>")
                    with gr.Column(scale=1):
                        kpi_g_saldo = gr.Markdown("## R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Saldo Líquido</span>")
                    with gr.Column(scale=1):
                        kpi_g_venc = gr.Markdown("## ⏰ 0\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Vencendo Hoje</span>")
                        
                with gr.Row():
                    filtro_g_periodo = gr.Radio(choices=["Mês Atual", "Próximo Mês", "Todos os Meses", "Mês Específico"], value="Mês Atual", label="📅 Período")
                    filtro_g_custom = gr.Textbox(label="Mês/Ano (MM-YYYY)", visible=False)
                    filtro_g_tipo = gr.Radio(choices=["Todos", "Despesa", "Receita"], value="Todos", label="Tipo")
                    filtro_g_cat = gr.Dropdown(choices=initial_cats, value="Todas", label="Categoria")
                    filtro_g_busca = gr.Textbox(label="🔍 Busca", placeholder="Buscar por descrição...")
                    check_g_inativos = gr.Checkbox(label="Exibir Inativos", value=False)
                    btn_g_atualizar = gr.Button("🔄 Atualizar Lista", variant="primary")
                    
                filtro_g_periodo.change(toggle_custom_month, inputs=[filtro_g_periodo], outputs=[filtro_g_custom])
                
                tabela_g_dados = gr.Dataframe(
                    headers=["ID", "Tipo", "Categoria", "Valor", "Descrição", "Data Registro", "Vencimento"],
                    datatype=["number", "str", "str", "str", "str", "str", "str"],
                    interactive=False,
                    wrap=True
                )
                
                g_inputs = [filtro_g_periodo, filtro_g_custom, filtro_g_tipo, filtro_g_cat, filtro_g_busca, check_g_inativos]
                g_outputs = [kpi_g_rec, kpi_g_desp, kpi_g_saldo, kpi_g_venc, tabela_g_dados]
                
                btn_g_atualizar.click(load_financial_table_and_kpis, inputs=g_inputs, outputs=g_outputs)
                filtro_g_periodo.change(load_financial_table_and_kpis, inputs=g_inputs, outputs=g_outputs)
                filtro_g_tipo.change(load_financial_table_and_kpis, inputs=g_inputs, outputs=g_outputs)
                filtro_g_cat.change(load_financial_table_and_kpis, inputs=g_inputs, outputs=g_outputs)
                check_g_inativos.change(load_financial_table_and_kpis, inputs=g_inputs, outputs=g_outputs)
                filtro_g_busca.submit(load_financial_table_and_kpis, inputs=g_inputs, outputs=g_outputs)
                
                gr.Markdown("---")
                with gr.Row():
                    plot_donut = gr.Plot(label="Distribuição Geral de Despesas")
                    plot_bars = gr.Plot(label="Comparativo Anual (Receitas x Despesas)")
                    
                def refresh_plots(periodo, mes_custom):
                    donut = generate_donut_chart(periodo, mes_custom)
                    bars = generate_monthly_comparison_chart()
                    return donut, bars
                    
                btn_g_atualizar.click(refresh_plots, inputs=[filtro_g_periodo, filtro_g_custom], outputs=[plot_donut, plot_bars])

            # -------------------------------------------------------------
            # ABA 5: CARTEIRA DE INVESTIMENTOS
            # -------------------------------------------------------------
            with gr.TabItem("💼 Carteira de Investimentos"):
                gr.Markdown("### 📈 Visão Geral do Patrimônio Investido")
                with gr.Row():
                    with gr.Column(scale=1):
                        kpi_macro_patrimonio = gr.Markdown("## 📈 R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Patrimônio Total a Mercado</span>")
                    with gr.Column(scale=1):
                        kpi_macro_investido = gr.Markdown("## 💼 R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Total Investido (Custo)</span>")
                    with gr.Column(scale=1):
                        kpi_macro_lucro = gr.Markdown("## R$ 0,00\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Lucro / Prejuízo Total</span>")
                    with gr.Column(scale=1):
                        kpi_macro_rent = gr.Markdown("## 0,00%\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Rentabilidade Global</span>")
                    with gr.Column(scale=1):
                        kpi_macro_qtd = gr.Markdown("## 🏛️ 0 ativos\n<span style='color:#94a3b8;font-size:12px;display:block;margin-top:2px;'>Renda Fixa + Ações</span>")
                        
                with gr.Row():
                    plot_macro_aloc = gr.Plot(label="Alocação Patrimonial")
                    plot_macro_ativos = gr.Plot(label="Maiores Posições (R$)")
                    
                btn_macro_refresh = gr.Button("🔄 Atualizar Indicadores Globais", variant="secondary")
                macro_outputs = [kpi_macro_patrimonio, kpi_macro_investido, kpi_macro_lucro, kpi_macro_rent, kpi_macro_qtd, plot_macro_aloc, plot_macro_ativos]
                btn_macro_refresh.click(load_invest_macro_kpis, outputs=macro_outputs)
                
                gr.Markdown("---")
                
                with gr.Tabs():
                    # =========================================================
                    # SUB-ABA 1: AÇÕES (RENDA VARIÁVEL)
                    # =========================================================
                    with gr.TabItem("📈 Ações (Renda Variável)"):
                        gr.Markdown("#### 📋 Posição Consolidada em Carteira (Custódia, Preço Médio e Lucro)")
                        tabela_acoes_cons = gr.Dataframe(
                            headers=["Código", "Custódia", "Preço Médio", "Custo Total", "Cotação Atual", "Valor Mercado", "Lucro Não Realizado", "Rentabilidade", "Lucro Realizado"],
                            datatype=["str", "number", "str", "str", "str", "str", "str", "str", "str"],
                            interactive=False,
                            wrap=True
                        )
                        
                        gr.Markdown("#### 📜 Extrato de Movimentações (Histórico de Ordens)")
                        with gr.Row():
                            filtro_acao_ticker = gr.Textbox(label="🔍 Filtrar por Código/Ticker", placeholder="Ex: PETR4, VALE3...")
                            filtro_acao_op = gr.Dropdown(choices=["Todas", "COMPRA", "VENDA", "DESDOBRAMENTO"], value="Todas", label="Tipo de Operação")
                            btn_acoes_refresh = gr.Button("🔄 Atualizar Ações", variant="primary")
                            
                        tabela_acoes_mov = gr.Dataframe(
                            headers=["ID", "Data", "Código", "Operação", "Qtd", "Preço Unit.", "Taxas", "Valor Total"],
                            datatype=["number", "str", "str", "str", "number", "str", "str", "str"],
                            interactive=False,
                            wrap=True
                        )
                        
                        acoes_inputs = [filtro_acao_ticker, filtro_acao_op]
                        acoes_outputs = [tabela_acoes_cons, tabela_acoes_mov]
                        
                        btn_acoes_refresh.click(load_acoes_tab, inputs=acoes_inputs, outputs=acoes_outputs)
                        filtro_acao_ticker.submit(load_acoes_tab, inputs=acoes_inputs, outputs=acoes_outputs)
                        filtro_acao_op.change(load_acoes_tab, inputs=acoes_inputs, outputs=acoes_outputs)
                        
                        gr.Markdown("---")
                        gr.Markdown("#### ➕ Registrar Nova Operação de Ações")
                        with gr.Row():
                            acao_in_cod = gr.Textbox(label="Código da Ação", placeholder="Ex: PETR4, VALE3, ITUB4...")
                            acao_in_op = gr.Dropdown(choices=["COMPRA", "VENDA", "DESDOBRAMENTO"], value="COMPRA", label="Operação")
                            acao_in_qtd = gr.Number(label="Quantidade", value=100, precision=0)
                            acao_in_preco = gr.Number(label="Preço Unitário (R$)", value=0.0)
                            acao_in_taxas = gr.Number(label="Taxas / Corretagem (R$)", value=0.0)
                            acao_in_dt = gr.Textbox(label="Data (YYYY-MM-DD)", value=datetime.now().strftime("%Y-%m-%d"))
                            
                        btn_salvar_acao = gr.Button("💾 Lançar Operação de Ação", variant="primary")
                        msg_acao = gr.Markdown()
                        
                        def on_save_acao(cod, op, qtd, preco, taxas, dt, f_t, f_o):
                            res = add_acao_action(cod, op, qtd, preco, taxas, dt)
                            c_df, m_df = load_acoes_tab(f_t, f_o)
                            k_at, k_in, k_lu, k_re, k_qt, g_m, g_at = load_invest_macro_kpis()
                            return res, c_df, m_df, k_at, k_in, k_lu, k_re, k_qt, g_m, g_at
                            
                        btn_salvar_acao.click(
                            on_save_acao,
                            inputs=[acao_in_cod, acao_in_op, acao_in_qtd, acao_in_preco, acao_in_taxas, acao_in_dt, filtro_acao_ticker, filtro_acao_op],
                            outputs=[msg_acao, tabela_acoes_cons, tabela_acoes_mov, kpi_macro_patrimonio, kpi_macro_investido, kpi_macro_lucro, kpi_macro_rent, kpi_macro_qtd, plot_macro_aloc, plot_macro_ativos]
                        )

                    # =========================================================
                    # SUB-ABA 2: RENDA FIXA
                    # =========================================================
                    with gr.TabItem("🏦 Renda Fixa"):
                        gr.Markdown("#### 📋 Posição Consolidada por Título (Saldo, Aportes, Juros e Rentabilidade)")
                        tabela_rf_cons = gr.Dataframe(
                            headers=["ID", "Título / Ativo", "Instituição", "Classe", "Saldo Investido", "Total Aportado", "Total Resgatado", "Juros", "Impostos", "Valor Atual", "Rendimento", "Rentabilidade"],
                            datatype=["number", "str", "str", "str", "str", "str", "str", "str", "str", "str", "str", "str"],
                            interactive=False,
                            wrap=True
                        )
                        
                        gr.Markdown("#### 📜 Extrato de Movimentações de Renda Fixa")
                        with gr.Row():
                            filtro_rf_tipo = gr.Dropdown(choices=["Todas", "APORTE", "RESGATE", "JUROS_RECEBIDOS", "IMPOSTO"], value="Todas", label="Tipo de Movimentação")
                            btn_rf_refresh = gr.Button("🔄 Atualizar Renda Fixa", variant="primary")
                            
                        tabela_rf_mov = gr.Dataframe(
                            headers=["ID", "Título", "Instituição", "Data", "Tipo Movimentação", "Valor"],
                            datatype=["number", "str", "str", "str", "str", "str"],
                            interactive=False,
                            wrap=True
                        )
                        
                        gr.Markdown("---")
                        with gr.Row():
                            with gr.Column(scale=2):
                                gr.Markdown("#### ➕ Registrar Movimentação em Título")
                                rf_in_dropdown_mov = gr.Dropdown(choices=[], label="Título de Renda Fixa")
                                with gr.Row():
                                    rf_in_tipo_mov = gr.Dropdown(choices=["APORTE", "RESGATE", "JUROS_RECEBIDOS", "IMPOSTO"], value="APORTE", label="Tipo")
                                    rf_in_valor_mov = gr.Number(label="Valor (R$)", value=0.0)
                                    rf_in_dt_mov = gr.Textbox(label="Data (YYYY-MM-DD)", value=datetime.now().strftime("%Y-%m-%d"))
                                btn_salvar_rf_mov = gr.Button("💾 Registrar Movimentação", variant="primary")
                                msg_rf_mov = gr.Markdown()
                                
                            with gr.Column(scale=2):
                                gr.Markdown("#### 🏛️ Cadastrar Novo Título")
                                rf_novo_nome = gr.Textbox(label="Nome do Título", placeholder="Ex: Tesouro Selic 2029, CDB Inter 110%...")
                                with gr.Row():
                                    rf_novo_banco = gr.Dropdown(choices=["Banco do Brasil", "Itaú", "Bradesco", "Santander", "Nubank", "Inter", "XP Investimentos", "BTG Pactual", "Rico", "Outro"], value="Banco do Brasil", allow_custom_value=True, label="Banco")
                                    rf_novo_tipo = gr.Dropdown(choices=["TESOURO DIRETO", "CDB", "LCI", "LCA", "CRI", "CRA", "DEBÊNTURE", "FUNDO RENDA FIXA", "OUTROS"], value="CDB", allow_custom_value=True, label="Tipo")
                                with gr.Row():
                                    rf_novo_val_ini = gr.Number(label="Aporte Inicial (R$)", value=0.0)
                                    rf_novo_dt_ini = gr.Textbox(label="Data Início (YYYY-MM-DD)", value=datetime.now().strftime("%Y-%m-%d"))
                                btn_salvar_rf_titulo = gr.Button("💾 Cadastrar Título", variant="primary")
                                msg_rf_titulo = gr.Markdown()
                                
                            with gr.Column(scale=1):
                                gr.Markdown("#### 📈 Atualizar Cotação/Valor")
                                rf_quote_dropdown = gr.Dropdown(choices=[], label="Título")
                                rf_quote_novo_val = gr.Number(label="Novo Valor Atual (R$)")
                                btn_salvar_rf_quote = gr.Button("📊 Atualizar Valor", variant="primary")
                                msg_rf_quote = gr.Markdown()
                                
                        rf_outputs = [tabela_rf_cons, tabela_rf_mov, rf_in_dropdown_mov, rf_quote_dropdown]
                        
                        btn_rf_refresh.click(load_renda_fixa_tab, inputs=[filtro_rf_tipo], outputs=rf_outputs)
                        filtro_rf_tipo.change(load_renda_fixa_tab, inputs=[filtro_rf_tipo], outputs=rf_outputs)
                        
                        def on_save_rf_mov(t_str, t_mov, val, dt, f_tipo):
                            res = add_rf_mov_action(t_str, t_mov, val, dt)
                            c_df, m_df, d1, d2 = load_renda_fixa_tab(f_tipo)
                            k_at, k_in, k_lu, k_re, k_qt, g_m, g_at = load_invest_macro_kpis()
                            return res, c_df, m_df, d1, d2, k_at, k_in, k_lu, k_re, k_qt, g_m, g_at
                            
                        btn_salvar_rf_mov.click(
                            on_save_rf_mov,
                            inputs=[rf_in_dropdown_mov, rf_in_tipo_mov, rf_in_valor_mov, rf_in_dt_mov, filtro_rf_tipo],
                            outputs=[msg_rf_mov, tabela_rf_cons, tabela_rf_mov, rf_in_dropdown_mov, rf_quote_dropdown, kpi_macro_patrimonio, kpi_macro_investido, kpi_macro_lucro, kpi_macro_rent, kpi_macro_qtd, plot_macro_aloc, plot_macro_ativos]
                        )
                        
                        def on_save_rf_titulo(nome, banco, tipo, dt, v_ini, f_tipo):
                            res = add_rf_titulo_action(nome, banco, tipo, dt, v_ini)
                            c_df, m_df, d1, d2 = load_renda_fixa_tab(f_tipo)
                            k_at, k_in, k_lu, k_re, k_qt, g_m, g_at = load_invest_macro_kpis()
                            return res, c_df, m_df, d1, d2, k_at, k_in, k_lu, k_re, k_qt, g_m, g_at
                            
                        btn_salvar_rf_titulo.click(
                            on_save_rf_titulo,
                            inputs=[rf_novo_nome, rf_novo_banco, rf_novo_tipo, rf_novo_dt_ini, rf_novo_val_ini, filtro_rf_tipo],
                            outputs=[msg_rf_titulo, tabela_rf_cons, tabela_rf_mov, rf_in_dropdown_mov, rf_quote_dropdown, kpi_macro_patrimonio, kpi_macro_investido, kpi_macro_lucro, kpi_macro_rent, kpi_macro_qtd, plot_macro_aloc, plot_macro_ativos]
                        )
                        
                        def on_update_rf_quote(t_str, novo_val, f_tipo):
                            res = update_rf_valor_action(t_str, novo_val)
                            c_df, m_df, d1, d2 = load_renda_fixa_tab(f_tipo)
                            k_at, k_in, k_lu, k_re, k_qt, g_m, g_at = load_invest_macro_kpis()
                            return res, c_df, m_df, d1, d2, k_at, k_in, k_lu, k_re, k_qt, g_m, g_at
                            
                        btn_salvar_rf_quote.click(
                            on_update_rf_quote,
                            inputs=[rf_quote_dropdown, rf_quote_novo_val, filtro_rf_tipo],
                            outputs=[msg_rf_quote, tabela_rf_cons, tabela_rf_mov, rf_in_dropdown_mov, rf_quote_dropdown, kpi_macro_patrimonio, kpi_macro_investido, kpi_macro_lucro, kpi_macro_rent, kpi_macro_qtd, plot_macro_aloc, plot_macro_ativos]
                        )

                    # =========================================================
                    # SUB-ABA 3: MIGRAÇÃO & DADOS LEGADOS
                    # =========================================================
                    with gr.TabItem("⚙️ Migração & Dados"):
                        gr.Markdown("#### 🚀 Migração Automática de Dados Legados")
                        gr.Markdown("Caso você tenha dados cadastrados na tabela antiga de investimentos, utilize o botão abaixo para migrar com segurança o Tesouro Direto para `movimentacao_renda_fixa` e as ações antigas para `movimentacao_acoes`.")
                        btn_exec_migracao = gr.Button("🚀 Executar Migração de Legados", variant="secondary")
                        msg_migracao = gr.Markdown()
                        
                        def on_migrar_click(f_a_t, f_a_o, f_rf_t):
                            res = migrar_legados_dashboard_action()
                            a_c, a_m = load_acoes_tab(f_a_t, f_a_o)
                            rf_c, rf_m, d1, d2 = load_renda_fixa_tab(f_rf_t)
                            k_at, k_in, k_lu, k_re, k_qt, g_m, g_at = load_invest_macro_kpis()
                            return res, a_c, a_m, rf_c, rf_m, d1, d2, k_at, k_in, k_lu, k_re, k_qt, g_m, g_at
                            
                        btn_exec_migracao.click(
                            on_migrar_click,
                            inputs=[filtro_acao_ticker, filtro_acao_op, filtro_rf_tipo],
                            outputs=[msg_migracao, tabela_acoes_cons, tabela_acoes_mov, tabela_rf_cons, tabela_rf_mov, rf_in_dropdown_mov, rf_quote_dropdown, kpi_macro_patrimonio, kpi_macro_investido, kpi_macro_lucro, kpi_macro_rent, kpi_macro_qtd, plot_macro_aloc, plot_macro_ativos]
                        )
                        
        # Cargas iniciais de todas as abas
        app.load(load_monthly_bills_tab, inputs=m_filter_inputs, outputs=m_filter_outputs)
        app.load(load_incomes_tab, inputs=r_filter_inputs, outputs=r_filter_outputs)
        app.load(load_card_purchases_tab, inputs=c_filter_inputs, outputs=c_filter_outputs)
        app.load(load_daily_expenses_and_budget, inputs=d_filter_inputs, outputs=d_filter_outputs)
        app.load(load_financial_table_and_kpis, inputs=g_inputs, outputs=g_outputs)
        app.load(refresh_plots, inputs=[filtro_g_periodo, filtro_g_custom], outputs=[plot_donut, plot_bars])
        app.load(load_invest_macro_kpis, outputs=macro_outputs)
        app.load(load_acoes_tab, inputs=acoes_inputs, outputs=acoes_outputs)
        app.load(load_renda_fixa_tab, inputs=[filtro_rf_tipo], outputs=rf_outputs)
        
    return app, theme

def launch_dashboard(server_name: str = "0.0.0.0", server_port: int = 7860):
    """Inicializa o servidor web do Gradio."""
    host = os.environ.get("GRADIO_SERVER_NAME", server_name)
    port = int(os.environ.get("GRADIO_SERVER_PORT", server_port))
    app, theme = build_dashboard()
    print(f"[INFO] Inicializando Dashboard Gradio em http://{host}:{port}", flush=True)
    app.launch(server_name=host, server_port=port, share=False, theme=theme)

if __name__ == "__main__":
    launch_dashboard()
