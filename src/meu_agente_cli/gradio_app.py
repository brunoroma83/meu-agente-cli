import os
import sys
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
    elif periodo_opcao == "Mês Específico" and mes_personalizado.strip():
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
    
    kpi_tot = f"## 📅 R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_pago = f"## 🟢 R$ {pagas:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_pend = f"## ⏳ R$ {pendentes:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_prox = f"## ⏰ {prox_venc}"
    
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

def add_new_monthly_bill_action(nome: str, categoria: str, valor: float, dia_venc: int, periodo_opcao: str, mes_personalizado: str):
    """Cadastra um novo compromisso mensal recorrente."""
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
        
    msg = f"✅ Conta recorrente '{nome}' de R$ {valor:.2f} (Venc: dia {dia}) cadastrada com sucesso!"
    kpi_t, kpi_pg, kpi_pd, kpi_px, df = load_monthly_bills_tab(periodo_opcao, mes_personalizado)
    return msg, kpi_t, kpi_pg, kpi_pd, kpi_px, df

# =====================================================================
# ABA 2: CARTÕES DE CRÉDITO (EXTRATO POR CARTÃO)
# =====================================================================

def load_card_purchases_tab(cartao_selecionado: str, periodo_opcao: str, mes_personalizado: str = ""):
    """Carrega o extrato de compras e faturas de um cartão específico."""
    month_year = get_period_params(periodo_opcao, mes_personalizado)
    items = db.get_card_purchases(card_name=cartao_selecionado, month_year=month_year)
    total = sum(item["amount"] for item in items)
    all_paid = all(item["is_paid"] for item in items) if items else False
    st_label = "🟢 Fatura Paga" if (all_paid and items) else ("⏳ Fatura em Aberto" if items else "Sem compras no mês")
    
    vencimentos = [it["due_date"] for it in items if it["due_date"]]
    venc_str = vencimentos[0].strftime("%d/%m/%Y") if vencimentos else "-"
    
    kpi_tot = f"## 💳 R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_venc = f"## 📅 {venc_str}"
    kpi_qtd = f"## 🛍️ {len(items)} compras/parcelas"
    kpi_status = f"## {st_label}"
    
    rows = []
    for it in items:
        dt_venc = it["due_date"].strftime("%d/%m/%Y") if it["due_date"] else "-"
        dt_buy = it["buy_date"].strftime("%d/%m/%Y") if it["buy_date"] else "-"
        st = "🟢 Paga" if it["is_paid"] else "⏳ Aberta"
        val_str = f"R$ {it['amount']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        rows.append([
            it["id"],
            it["description"],
            it["category"],
            val_str,
            dt_venc,
            dt_buy,
            st
        ])
    df = pd.DataFrame(rows, columns=["ID", "Descrição da Compra", "Categoria", "Valor", "Vencimento na Fatura", "Data da Compra", "Status"])
    return kpi_tot, kpi_venc, kpi_qtd, kpi_status, df

def add_card_purchase_action(cartao: str, categoria: str, valor_total: float, parcelas: int, descricao: str, data_compra: str, periodo_opcao: str, mes_personalizado: str):
    """Registra uma nova compra no cartão de crédito selecionado."""
    if not cartao:
        return "❌ Selecione um cartão de crédito.", *load_card_purchases_tab(cartao, periodo_opcao, mes_personalizado)
    if valor_total <= 0:
        return "❌ O valor da compra deve ser maior que zero.", *load_card_purchases_tab(cartao, periodo_opcao, mes_personalizado)
        
    p_count = max(1, int(parcelas))
    desc = descricao.strip() if descricao.strip() else f"Compra no cartão {cartao}"
    
    success = db.add_card_purchase(
        card_name=cartao,
        category=categoria.strip(),
        total_amount=valor_total,
        installments=p_count,
        description=desc,
        buy_date_str=data_compra
    )
    
    if success:
        msg = f"✅ Compra de R$ {valor_total:.2f} ({p_count}x) no cartão {cartao} lançada com sucesso!"
    else:
        msg = f"❌ Falha ao registrar compra no cartão {cartao}."
        
    k_tot, k_vnc, k_q, k_st, df = load_card_purchases_tab(cartao, periodo_opcao, mes_personalizado)
    return msg, k_tot, k_vnc, k_q, k_st, df

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
    
    kpi_rec = f"## 🟢 R$ {rec:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_fixas = f"## 🔒 R$ {fixas:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_livre = f"## 🛍️ R$ {livre_rest:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_teto = f"## 🎯 R$ {teto:,.2f} / dia".replace(",", "X").replace(".", ",").replace("X", ".")
    
    cor_hoje = "#10b981" if status_hoje == "ok" else "#ef4444"
    if status_hoje == "ok":
        msg_meta = f"✅ <b>Dentro do limite diário!</b> (Restam {dias_rest} dias no mês)"
    else:
        msg_meta = f"⚠️ <b>Atenção:</b> o gasto de hoje ultrapassou o teto diário recomendado!"
        
    status_card = f"""
    <div style="background-color: #f1f5f9; padding: 12px 18px; border-radius: 8px; border-left: 5px solid {cor_hoje};">
        <span style="font-size: 16px; font-weight: bold; color: {cor_hoje};">Gasto Realizado Hoje: R$ {gasto_hoje:,.2f}</span><br>
        <span style="font-size: 13px; color: #475569;">{msg_meta}</span>
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
    query = busca_texto.strip() if busca_texto.strip() else None
    
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
    saldo_color = "green" if saldo >= 0 else "red"
    
    kpi_rec = f"## 🟢 R$ {total_receitas:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_desp = f"## 🔴 R$ {total_despesas:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_saldo = f"## <span style='color:{saldo_color}'>R$ {saldo:,.2f}</span>".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_venc = f"## ⏰ {contas_vencendo_hoje} conta(s)"
    
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

def load_investments_table_and_kpis(tipo_filtro: str, banco_filtro: str, busca_texto: str, mostrar_inativos: bool = False):
    """Carrega dados consolidados da carteira de investimentos."""
    summary = invest.get_investment_summary()
    t_inv = summary["total_investido"]
    t_atual = summary["valor_atual"]
    lucro = summary["lucro_total"]
    pct = summary["rentabilidade_pct"]
    total_ativos = summary["total_ativos"]
    
    lucro_cor = "green" if lucro >= 0 else "red"
    lucro_sinal = "+" if lucro > 0 else ""
    pct_sinal = "+" if pct > 0 else ""
    
    kpi_inv = f"## 💼 R$ {t_inv:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_atual = f"## 📈 R$ {t_atual:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_lucro = f"## <span style='color:{lucro_cor}'>{lucro_sinal}R$ {lucro:,.2f}</span>".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_rent = f"## <span style='color:{lucro_cor}'>{pct_sinal}{pct:.2f}%</span>"
    kpi_qtd = f"## 🏛️ {total_ativos} ativo(s)"
    
    records = invest.get_investments_filtered(tipo=tipo_filtro, banco=banco_filtro, query=busca_texto, active_only=(not mostrar_inativos))
    rows = []
    for r in records:
        inv_id, nome, banco, tipo, qtd, v_inv, v_atual, luc, pct_item, dt_ini, dt_up, st, act = r
        v_inv_f = float(v_inv) if v_inv is not None else 0.0
        v_atual_f = float(v_atual) if v_atual is not None else v_inv_f
        luc_f = float(luc) if luc is not None else (v_atual_f - v_inv_f)
        pct_f = float(pct_item) if pct_item is not None else ((luc_f / v_inv_f * 100) if v_inv_f > 0 else 0.0)
        
        luc_str = f"{'+' if luc_f > 0 else ''}R$ {luc_f:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        pct_str = f"{'+' if pct_f > 0 else ''}{pct_f:.2f}%"
        dt_ini_str = dt_ini.strftime("%d/%m/%Y") if dt_ini else "-"
        dt_up_str = dt_up.strftime("%d/%m/%Y") if dt_up else "-"
        status_label = "🟢 Ativo" if act else "🔴 Inativo"
        
        rows.append([
            inv_id, nome, banco, tipo, float(qtd) if qtd is not None else 1.0,
            f"R$ {v_inv_f:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            f"R$ {v_atual_f:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            luc_str, pct_str, dt_ini_str, dt_up_str, status_label
        ])
        
    df = pd.DataFrame(rows, columns=["ID", "Título / Ativo", "Instituição", "Classe", "Qtd", "Valor Investido", "Valor Atual", "Lucro/Prej", "Rentabilidade", "Data Início", "Última Atualização", "Status"])
    return kpi_inv, kpi_atual, kpi_lucro, kpi_rent, kpi_qtd, df

def generate_invest_type_chart():
    """Gera gráfico de rosca de alocação por classe de ativo."""
    alloc = invest.get_allocation_by_type()
    fig, ax = plt.subplots(figsize=(6, 4.5), facecolor="#f8fafc")
    ax.set_facecolor("#f8fafc")
    if not alloc:
        ax.text(0.5, 0.5, "Nenhum investimento cadastrado", horizontalalignment='center', verticalalignment='center', fontsize=12, color="#64748b")
        ax.axis('off')
        plt.tight_layout()
        return fig
    labels = list(alloc.keys())
    values = list(alloc.values())
    colors = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#94a3b8']
    wedges, texts, autotexts = ax.pie(values, labels=labels, autopct='%1.1f%%', startangle=140, pctdistance=0.75, colors=colors[:len(values)], wedgeprops=dict(width=0.45, edgecolor='white', linewidth=2))
    for autotext in autotexts:
        autotext.set_color('#1e293b')
        autotext.set_fontweight('bold')
        autotext.set_fontsize(9)
    ax.set_title("Alocação por Classe de Ativo", fontsize=12, fontweight='bold', pad=15)
    plt.tight_layout()
    return fig

def generate_invest_bank_chart():
    """Gera gráfico de barras de alocação por instituição/banco."""
    alloc = invest.get_allocation_by_bank()
    fig, ax = plt.subplots(figsize=(6, 4.5), facecolor="#f8fafc")
    ax.set_facecolor("#f8fafc")
    if not alloc:
        ax.text(0.5, 0.5, "Nenhum investimento cadastrado", horizontalalignment='center', verticalalignment='center', fontsize=12, color="#64748b")
        ax.axis('off')
        plt.tight_layout()
        return fig
    bancos = list(alloc.keys())
    valores = list(alloc.values())
    y_pos = range(len(bancos))
    ax.barh(y_pos, valores, color='#3b82f6', edgecolor='white', alpha=0.9, height=0.55)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(bancos, fontsize=10)
    ax.invert_yaxis()
    ax.set_title("Patrimônio por Instituição / Banco", fontsize=12, fontweight='bold', pad=15)
    ax.grid(axis='x', linestyle='--', alpha=0.5)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, p: f"R$ {x:,.0f}".replace(",", ".")))
    plt.tight_layout()
    return fig

def add_new_investment_action(nome: str, banco: str, tipo: str, qtd: float, valor: float, data_ini: str):
    """Cadastra um novo investimento."""
    if not nome.strip():
        return "❌ O nome do ativo é obrigatório.", gr.update(), gr.update()
    if valor <= 0:
        return "❌ O valor investido deve ser maior que zero.", gr.update(), gr.update()
    dt = data_ini.strip() if data_ini and data_ini.strip() else datetime.now().strftime("%Y-%m-%d")
    success = invest.set_invest(nome_titulo=nome, nome_banco=banco, tipo_investimento=tipo, quantidade=qtd if qtd > 0 else 1.0, valor_investido=valor, data_inicio=dt, valor_atual=valor)
    if success:
        new_types = ["Todas"] + invest.get_distinct_investment_types()
        new_banks = ["Todos"] + invest.get_distinct_banks()
        return f"✅ Investimento '{nome}' cadastrado com sucesso!", gr.update(choices=new_types), gr.update(choices=new_banks)
    return "❌ Erro ao salvar investimento no banco.", gr.update(), gr.update()

def update_investment_quote_action(invest_id: int, novo_valor: float):
    """Atualiza a cotação/valor atual de um investimento."""
    if not invest_id or invest_id <= 0:
        return "❌ Informe um ID válido."
    success = invest.update_investment_valuation(int(invest_id), float(novo_valor))
    if success:
        return f"✅ Cotação do investimento #{invest_id} atualizada para R$ {novo_valor:.2f} com sucesso!"
    return f"❌ Falha ao atualizar investimento #{invest_id}."

# =====================================================================
# CONSTRUÇÃO DA INTERFACE GRADIO
# =====================================================================

def build_dashboard():
    """Constrói a aplicação web completa com todas as visões integradas."""
    initial_cats = ["Todas"] + db.get_financial_categories()
    initial_cards = db.get_distinct_cards()
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
                        kpi_m_total = gr.Markdown("## 📅 R$ 0,00", label="Total de Contas")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Compromissos do Mês</p>")
                    with gr.Column(scale=1):
                        kpi_m_pago = gr.Markdown("## 🟢 R$ 0,00", label="Já Pago")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Contas Liquidadas</p>")
                    with gr.Column(scale=1):
                        kpi_m_pend = gr.Markdown("## ⏳ R$ 0,00", label="Pendente")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>A Pagar no Mês</p>")
                    with gr.Column(scale=1):
                        kpi_m_prox = gr.Markdown("## ⏰ -", label="Próximo Vencimento")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Vencimento Mais Próximo</p>")
                        
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
                    
                filtro_m_periodo.change(toggle_custom_month, inputs=[filtro_m_periodo], outputs=[filtro_m_custom])
                
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
                
                gr.Markdown("---")
                
                with gr.Row():
                    # Liquidação rápida
                    with gr.Column(scale=2):
                        gr.Markdown("### ✅ Marcar Pagamento de Conta / Fatura")
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
                        
                    # Cadastro de nova conta recorrente
                    with gr.Column(scale=3):
                        gr.Markdown("### ➕ Cadastrar Nova Conta Fixa Recorrente")
                        with gr.Row():
                            novo_m_nome = gr.Textbox(label="Nome da Conta", placeholder="Ex: Condomínio, Aluguel, Internet Fibra...")
                            novo_m_cat = gr.Dropdown(choices=["Condomínio", "Casa", "Seguro", "Energia", "Internet", "Telefonia", "Curso", "Impostos", "Saúde", "Outros"], value="Condomínio", allow_custom_value=True, label="Categoria")
                        with gr.Row():
                            novo_m_valor = gr.Number(label="Valor Estimado / Fixo (R$)", value=0.0)
                            novo_m_dia = gr.Number(label="Dia do Vencimento (1-31)", value=10, precision=0)
                        btn_salvar_m = gr.Button("💾 Cadastrar Conta Fixa", variant="primary")
                        msg_novo_m = gr.Markdown()
                        
                        btn_salvar_m.click(
                            add_new_monthly_bill_action,
                            inputs=[novo_m_nome, novo_m_cat, novo_m_valor, novo_m_dia, filtro_m_periodo, filtro_m_custom],
                            outputs=[msg_novo_m, kpi_m_total, kpi_m_pago, kpi_m_pend, kpi_m_prox, tabela_m_contas]
                        )

            # -------------------------------------------------------------
            # ABA 2: CARTÕES DE CRÉDITO (EXTRATO POR CARTÃO)
            # -------------------------------------------------------------
            with gr.TabItem("💳 Cartões de Crédito"):
                gr.Markdown("### 🛍️ Extrato Detalhado por Cartão de Crédito")
                gr.Markdown("Consulte as compras e parcelas de cada cartão individualmente. O valor total da fatura calculada alimenta automaticamente a aba de Contas Mensais.")
                
                with gr.Row():
                    filtro_c_cartao = gr.Dropdown(
                        choices=initial_cards,
                        value=initial_cards[0] if initial_cards else "Cartão de Crédito",
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
                        kpi_c_total = gr.Markdown("## 💳 R$ 0,00", label="Total da Fatura")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Fatura no Mês</p>")
                    with gr.Column(scale=1):
                        kpi_c_venc = gr.Markdown("## 📅 -", label="Vencimento")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Vencimento da Fatura</p>")
                    with gr.Column(scale=1):
                        kpi_c_qtd = gr.Markdown("## 🛍️ 0 compras", label="Lançamentos")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Itens na Fatura</p>")
                    with gr.Column(scale=1):
                        kpi_c_status = gr.Markdown("## ⏳ Aberta", label="Status")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Situação do Pagamento</p>")
                        
                tabela_c_compras = gr.Dataframe(
                    headers=["ID", "Descrição da Compra", "Categoria", "Valor", "Vencimento na Fatura", "Data da Compra", "Status"],
                    datatype=["number", "str", "str", "str", "str", "str", "str"],
                    interactive=False,
                    wrap=True
                )
                
                c_filter_inputs = [filtro_c_cartao, filtro_c_periodo, filtro_c_custom]
                c_filter_outputs = [kpi_c_total, kpi_c_venc, kpi_c_qtd, kpi_c_status, tabela_c_compras]
                
                btn_c_atualizar.click(load_card_purchases_tab, inputs=c_filter_inputs, outputs=c_filter_outputs)
                filtro_c_cartao.change(load_card_purchases_tab, inputs=c_filter_inputs, outputs=c_filter_outputs)
                filtro_c_periodo.change(load_card_purchases_tab, inputs=c_filter_inputs, outputs=c_filter_outputs)
                
                gr.Markdown("---")
                
                # Formulário para lançar compra no cartão
                gr.Markdown("### ➕ Lançar Nova Compra no Cartão")
                with gr.Row():
                    novo_c_cartao = gr.Dropdown(choices=initial_cards, value=initial_cards[0] if initial_cards else "Cartão de Crédito", label="Cartão Utilizado", allow_custom_value=True)
                    novo_c_cat = gr.Dropdown(choices=["Alimentação", "Supermercado", "Eletrônicos", "Vestuário", "Lazer", "Farmácia", "Assinatura", "Outros"], value="Supermercado", allow_custom_value=True, label="Categoria")
                    novo_c_valor = gr.Number(label="Valor Total da Compra (R$)", value=0.0)
                    novo_c_parcelas = gr.Number(label="Número de Parcelas (1 = à vista)", value=1, precision=0)
                with gr.Row():
                    novo_c_desc = gr.Textbox(label="Descrição da Compra", placeholder="Ex: Supermercado Pão de Açúcar, Passagem aérea...")
                    novo_c_data = gr.Textbox(label="Data da Compra (YYYY-MM-DD)", value=datetime.now().strftime("%Y-%m-%d"))
                    btn_salvar_c = gr.Button("💳 Lançar no Cartão", variant="primary", scale=1)
                msg_novo_c = gr.Markdown()
                
                btn_salvar_c.click(
                    add_card_purchase_action,
                    inputs=[novo_c_cartao, novo_c_cat, novo_c_valor, novo_c_parcelas, novo_c_desc, novo_c_data, filtro_c_periodo, filtro_c_custom],
                    outputs=[msg_novo_c, kpi_c_total, kpi_c_venc, kpi_c_qtd, kpi_c_status, tabela_c_compras]
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
                        kpi_d_rec = gr.Markdown("## 🟢 R$ 0,00", label="Receitas do Mês")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Receitas Consolidadas</p>")
                    with gr.Column(scale=1):
                        kpi_d_fixas = gr.Markdown("## 🔒 R$ 0,00", label="Custos Fixos & Faturas")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Comprometido no Mês</p>")
                    with gr.Column(scale=1):
                        kpi_d_livre = gr.Markdown("## 🛍️ R$ 0,00", label="Saldo Livre Restante")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Disponível para Gastos Diários</p>")
                    with gr.Column(scale=1):
                        kpi_d_teto = gr.Markdown("## 🎯 R$ 0,00 / dia", label="Teto Diário Recomendado")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Limite Sugerido por Dia</p>")
                        
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
                        kpi_g_rec = gr.Markdown("## 🟢 R$ 0,00", label="Total de Receitas")
                    with gr.Column(scale=1):
                        kpi_g_desp = gr.Markdown("## 🔴 R$ 0,00", label="Total de Despesas")
                    with gr.Column(scale=1):
                        kpi_g_saldo = gr.Markdown("## R$ 0,00", label="Saldo Líquido")
                    with gr.Column(scale=1):
                        kpi_g_venc = gr.Markdown("## ⏰ 0", label="Vencendo Hoje")
                        
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
                gr.Markdown("### 📈 Visão Patrimonial Consolidada")
                with gr.Row():
                    with gr.Column(scale=1):
                        kpi_inv_total = gr.Markdown("## 💼 R$ 0,00", label="Total Investido")
                    with gr.Column(scale=1):
                        kpi_inv_atual = gr.Markdown("## 📈 R$ 0,00", label="Valor Atual de Mercado")
                    with gr.Column(scale=1):
                        kpi_inv_lucro = gr.Markdown("## R$ 0,00", label="Lucro / Prejuízo Total")
                    with gr.Column(scale=1):
                        kpi_inv_rent = gr.Markdown("## 0,00%", label="Rentabilidade Global")
                    with gr.Column(scale=1):
                        kpi_inv_qtd = gr.Markdown("## 🏛️ 0 ativos", label="Posições")
                        
                with gr.Row():
                    plot_inv_type = gr.Plot(label="Alocação por Classe de Ativo")
                    plot_inv_bank = gr.Plot(label="Alocação por Instituição / Banco")
                    
                with gr.Row():
                    filtro_inv_tipo = gr.Dropdown(choices=initial_invest_types, value="Todas", label="🏷️ Classe / Tipo")
                    filtro_inv_banco = gr.Dropdown(choices=initial_invest_banks, value="Todos", label="🏛️ Instituição")
                    filtro_inv_busca = gr.Textbox(label="🔍 Buscar Ativo", placeholder="Nome ou ticker...")
                    check_inv_inativos = gr.Checkbox(label="Exibir Inativos", value=False)
                    btn_inv_atualizar = gr.Button("🔄 Atualizar Carteira", variant="primary")
                    
                tabela_invest = gr.Dataframe(
                    headers=["ID", "Título / Ativo", "Instituição", "Classe", "Qtd", "Valor Investido", "Valor Atual", "Lucro/Prej", "Rentabilidade", "Data Início", "Última Atualização", "Status"],
                    datatype=["number", "str", "str", "str", "number", "str", "str", "str", "str", "str", "str", "str"],
                    interactive=False,
                    wrap=True
                )
                
                inv_inputs = [filtro_inv_tipo, filtro_inv_banco, filtro_inv_busca, check_inv_inativos]
                inv_outputs = [kpi_inv_total, kpi_inv_atual, kpi_inv_lucro, kpi_inv_rent, kpi_inv_qtd, tabela_invest]
                
                def refresh_invest_all(tipo, banco, busca, inativos):
                    k_inv, k_at, k_luc, k_rent, k_qtd, df = load_investments_table_and_kpis(tipo, banco, busca, inativos)
                    g_type = generate_invest_type_chart()
                    g_bank = generate_invest_bank_chart()
                    return k_inv, k_at, k_luc, k_rent, k_qtd, df, g_type, g_bank
                    
                btn_inv_atualizar.click(refresh_invest_all, inputs=inv_inputs, outputs=inv_outputs + [plot_inv_type, plot_inv_bank])
                filtro_inv_tipo.change(load_investments_table_and_kpis, inputs=inv_inputs, outputs=inv_outputs)
                filtro_inv_banco.change(load_investments_table_and_kpis, inputs=inv_inputs, outputs=inv_outputs)
                check_inv_inativos.change(load_investments_table_and_kpis, inputs=inv_inputs, outputs=inv_outputs)
                filtro_inv_busca.submit(load_investments_table_and_kpis, inputs=inv_inputs, outputs=inv_outputs)
                
                gr.Markdown("---")
                with gr.Row():
                    with gr.Column(scale=3):
                        gr.Markdown("### ➕ Cadastrar Novo Ativo")
                        novo_inv_nome = gr.Textbox(label="Título / Ativo", placeholder="Ex: Tesouro Selic 2029, PETR4, MXRF11...")
                        with gr.Row():
                            novo_inv_banco = gr.Dropdown(choices=["Banco do Brasil", "Itaú", "Bradesco", "Santander", "Nubank", "Inter", "XP Investimentos", "BTG Pactual", "Rico", "Outro"], value="Banco do Brasil", allow_custom_value=True, label="Instituição / Banco")
                            novo_inv_tipo = gr.Dropdown(choices=["AÇÃO", "TESOURO DIRETO", "CDB / RENDA FIXA", "FII / FUNDO IMOBILIÁRIO", "FUNDO DE INVESTIMENTO", "CRIPTO", "OUTROS"], value="AÇÃO", allow_custom_value=True, label="Classe / Tipo")
                        with gr.Row():
                            novo_inv_qtd = gr.Number(label="Quantidade", value=1.0)
                            novo_inv_valor = gr.Number(label="Valor Investido Total (R$)", value=0.0)
                            novo_inv_data = gr.Textbox(label="Data Início (YYYY-MM-DD)", value=datetime.now().strftime("%Y-%m-%d"))
                        btn_salvar_inv = gr.Button("💾 Cadastrar Ativo", variant="primary")
                        msg_novo_inv = gr.Markdown()
                        
                    with gr.Column(scale=2):
                        gr.Markdown("### 📈 Atualizar Cotação")
                        cotacao_id = gr.Number(label="ID do Ativo", precision=0)
                        cotacao_novo_valor = gr.Number(label="Novo Valor Atual (R$)")
                        btn_atualizar_cotacao = gr.Button("📊 Atualizar Cotação", variant="primary")
                        msg_cotacao = gr.Markdown()
                        
                btn_salvar_inv.click(add_new_investment_action, inputs=[novo_inv_nome, novo_inv_banco, novo_inv_tipo, novo_inv_qtd, novo_inv_valor, novo_inv_data], outputs=[msg_novo_inv, filtro_inv_tipo, filtro_inv_banco])
                btn_atualizar_cotacao.click(update_investment_quote_action, inputs=[cotacao_id, cotacao_novo_valor], outputs=[msg_cotacao])
                
        # Cargas iniciais de todas as abas
        app.load(load_monthly_bills_tab, inputs=m_filter_inputs, outputs=m_filter_outputs)
        app.load(load_card_purchases_tab, inputs=c_filter_inputs, outputs=c_filter_outputs)
        app.load(load_daily_expenses_and_budget, inputs=d_filter_inputs, outputs=d_filter_outputs)
        app.load(load_financial_table_and_kpis, inputs=g_inputs, outputs=g_outputs)
        app.load(refresh_plots, inputs=[filtro_g_periodo, filtro_g_custom], outputs=[plot_donut, plot_bars])
        app.load(refresh_invest_all, inputs=inv_inputs, outputs=inv_outputs + [plot_inv_type, plot_inv_bank])
        
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
