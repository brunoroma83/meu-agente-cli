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

# =====================================================================
# FUNÇÕES DE CARREGAMENTO E FILTRAGEM DE DADOS
# =====================================================================

def get_period_params(periodo_opcao: str, mes_personalizado: str = "") -> Optional[str]:
    """Converte a opção de período selecionada para o formato MM-YYYY aceito pelo banco."""
    now = datetime.now()
    if periodo_opcao == "Mês Atual":
        return now.strftime("%m-%Y")
    elif periodo_opcao == "Próximo Mês":
        prox_mes = now.month + 1 if now.month < 12 else 1
        prox_ano = now.year if now.month < 12 else now.year + 1
        return f"{prox_mes:02d}-{prox_ano}"
    elif periodo_opcao == "Mês Específico" and mes_personalizado.strip():
        return mes_personalizado.strip()
    return None  # "Todos os Meses"

def load_financial_table_and_kpis(
    periodo_opcao: str,
    mes_personalizado: str,
    tipo_filtro: str,
    categoria_filtro: str,
    busca_texto: str,
    mostrar_inativos: bool = False
):
    """Carrega os registros filtrados, formata a tabela e calcula os KPIs do painel."""
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
        
    # Processa e calcula totais
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
    
    # Formata KPIs
    kpi_rec = f"## 🟢 R$ {total_receitas:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_desp = f"## 🔴 R$ {total_despesas:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_saldo = f"## <span style='color:{saldo_color}'>R$ {saldo:,.2f}</span>".replace(",", "X").replace(".", ",").replace("X", ".")
    kpi_venc = f"## ⏰ {contas_vencendo_hoje} conta(s)"
    
    df = pd.DataFrame(
        table_rows,
        columns=["ID", "Tipo", "Categoria", "Valor", "Descrição", "Data Registro", "Vencimento"]
    )
    
    return kpi_rec, kpi_desp, kpi_saldo, kpi_venc, df

# =====================================================================
# GERAÇÃO DE GRÁFICOS ANALÍTICOS (MATPLOTLIB)
# =====================================================================

def generate_donut_chart(periodo_opcao: str, mes_personalizado: str = ""):
    """Gera gráfico de rosca com as principais despesas por categoria."""
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
        
    # Limita às top 8 categorias e agrupa o restante em 'Outras'
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
    ax.set_title(f"Distribuição de Despesas por Categoria{periodo_titulo}", fontsize=13, fontweight='bold', pad=15)
    plt.tight_layout()
    return fig

def generate_monthly_comparison_chart(year_input: int = datetime.now().year):
    """Gera gráfico de barras comparativo de Receitas vs Despesas mês a mês."""
    overview = db.get_monthly_overview(year_input)
    
    fig, ax = plt.subplots(figsize=(9, 4.5), facecolor="#f8fafc")
    ax.set_facecolor("#f8fafc")
    
    meses = [item["mes_label"].split("/")[0] for item in overview]
    receitas = [item["receitas"] for item in overview]
    despesas = [item["despesas"] for item in overview]
    
    import numpy as np
    x = np.arange(len(meses))
    width = 0.38
    
    rects1 = ax.bar(x - width/2, receitas, width, label='Receitas', color='#10b981', alpha=0.9, edgecolor='white')
    rects2 = ax.bar(x + width/2, despesas, width, label='Despesas', color='#ef4444', alpha=0.9, edgecolor='white')
    
    ax.set_title(f"Evolução Mensal: Receitas vs. Despesas ({year_input})", fontsize=13, fontweight='bold', pad=15)
    ax.set_xticks(x)
    ax.set_xticklabels(meses, fontsize=10)
    ax.legend(frameon=True, facecolor='white', edgecolor='#e2e8f0')
    ax.grid(axis='y', linestyle='--', alpha=0.5)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    
    # Formata eixo Y em reais
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, p: f"R$ {x:,.0f}".replace(",", ".")))
    
    plt.tight_layout()
    return fig

# =====================================================================
# AÇÕES DE CRUD (ADICIONAR / EXCLUIR / RESTAURAR)
# =====================================================================

def add_new_entry(tipo: str, categoria: str, valor: float, descricao: str, vencimento: str):
    """Insere um novo registro financeiro."""
    if valor <= 0:
        return "❌ O valor do lançamento deve ser maior que zero.", gr.update()
    if not categoria.strip():
        return "❌ A categoria é obrigatória.", gr.update()
        
    due_val = vencimento.strip() if vencimento and vencimento.strip() else None
    
    # Valida formato de data se informado
    if due_val:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                due_val = datetime.strptime(due_val, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
                
    success = db.add_financial_record(tipo.lower(), categoria.strip(), valor, descricao.strip(), due_val)
    if success:
        cats = ["Todas"] + db.get_financial_categories()
        return f"✅ Lançamento registrado com sucesso: {tipo} de R$ {valor:.2f} em '{categoria}'!", gr.update(choices=cats)
    else:
        return "❌ Erro ao salvar registro no banco de dados.", gr.update()

def delete_entry_by_id(record_id: int):
    """Inativa (soft delete) um registro financeiro pelo ID."""
    if not record_id or record_id <= 0:
        return "❌ Informe um ID válido para exclusão."
    success = db.delete_financial_record(int(record_id))
    if success:
        return f"✅ Registro #{record_id} inativado com sucesso (Soft Delete)!"
    else:
        return f"❌ Falha ao excluir o registro #{record_id}."

def restore_entry_by_id(record_id: int):
    """Restaura um registro financeiro inativado pelo ID."""
    if not record_id or record_id <= 0:
        return "❌ Informe um ID válido para restauração."
    success = db.restore_financial_record(int(record_id))
    if success:
        return f"✅ Registro #{record_id} restaurado com sucesso para a lista ativa!"
    else:
        return f"❌ Falha ao restaurar o registro #{record_id}."

# =====================================================================
# CONSTRUÇÃO DA INTERFACE GRADIO
# =====================================================================

def build_dashboard():
    """Constrói a interface visual completa do Gradio."""
    initial_cats = ["Todas"] + db.get_financial_categories()
    
    theme = gr.themes.Soft(
        primary_hue="blue",
        secondary_hue="emerald",
        neutral_hue="slate"
    )
    
    with gr.Blocks(title="Meu Agente - Painel Financeiro", theme=theme) as app:
        gr.Markdown(
            """
            # 💼 Meu Agente - Painel Financeiro
            Acompanhamento dinâmico de receitas, despesas, faturas e fluxo de caixa.
            """
        )
        
        with gr.Tabs():
            # -------------------------------------------------------------
            # ABA 1: EXTRATO & KPIS
            # -------------------------------------------------------------
            with gr.TabItem("📊 Extrato & Indicadores"):
                # Cards de Indicadores
                with gr.Row():
                    with gr.Column(scale=1):
                        kpi_rec = gr.Markdown("## 🟢 R$ 0,00", label="Total de Receitas")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Receitas no Período</p>")
                    with gr.Column(scale=1):
                        kpi_desp = gr.Markdown("## 🔴 R$ 0,00", label="Total de Despesas")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Despesas no Período</p>")
                    with gr.Column(scale=1):
                        kpi_saldo = gr.Markdown("## R$ 0,00", label="Saldo Líquido")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Saldo (Receitas - Despesas)</p>")
                    with gr.Column(scale=1):
                        kpi_venc = gr.Markdown("## ⏰ 0", label="Vencendo Hoje")
                        gr.Markdown("<p style='margin-top:-10px;color:#64748b;font-size:13px;'>Contas a Pagar Hoje</p>")
                        
                gr.Markdown("---")
                
                # Barra de Filtros
                with gr.Row():
                    filtro_periodo = gr.Radio(
                        choices=["Mês Atual", "Próximo Mês", "Todos os Meses", "Mês Específico"],
                        value="Mês Atual",
                        label="📅 Período"
                    )
                    filtro_mes_personalizado = gr.Textbox(
                        label="Mês/Ano (MM-YYYY)",
                        placeholder="Ex: 08-2026",
                        visible=False
                    )
                    filtro_tipo = gr.Radio(
                        choices=["Todos", "Despesa", "Receita"],
                        value="Todos",
                        label="🏷️ Tipo de Lançamento"
                    )
                    filtro_categoria = gr.Dropdown(
                        choices=initial_cats,
                        value="Todas",
                        label="📁 Categoria",
                        interactive=True
                    )
                    
                with gr.Row():
                    filtro_busca = gr.Textbox(
                        label="🔍 Busca Rápida",
                        placeholder="Pesquisar por descrição ou nome de categoria...",
                        scale=4
                    )
                    check_inativos = gr.Checkbox(
                        label="Exibir Inativos (Deletados)",
                        value=False,
                        scale=1
                    )
                    btn_atualizar = gr.Button("🔄 Atualizar Lista", variant="primary", scale=1)
                    
                # Tabela de Dados
                tabela_dados = gr.Dataframe(
                    headers=["ID", "Tipo", "Categoria", "Valor", "Descrição", "Data Registro", "Vencimento"],
                    datatype=["number", "str", "str", "str", "str", "str", "str"],
                    interactive=False,
                    wrap=True
                )
                
                # Eventos de Atualização
                def toggle_custom_month(choice):
                    return gr.update(visible=(choice == "Mês Específico"))
                    
                filtro_periodo.change(toggle_custom_month, inputs=[filtro_periodo], outputs=[filtro_mes_personalizado])
                
                filter_inputs = [
                    filtro_periodo,
                    filtro_mes_personalizado,
                    filtro_tipo,
                    filtro_categoria,
                    filtro_busca,
                    check_inativos
                ]
                filter_outputs = [kpi_rec, kpi_desp, kpi_saldo, kpi_venc, tabela_dados]
                
                btn_atualizar.click(load_financial_table_and_kpis, inputs=filter_inputs, outputs=filter_outputs)
                filtro_tipo.change(load_financial_table_and_kpis, inputs=filter_inputs, outputs=filter_outputs)
                filtro_categoria.change(load_financial_table_and_kpis, inputs=filter_inputs, outputs=filter_outputs)
                filtro_periodo.change(load_financial_table_and_kpis, inputs=filter_inputs, outputs=filter_outputs)
                check_inativos.change(load_financial_table_and_kpis, inputs=filter_inputs, outputs=filter_outputs)
                filtro_busca.submit(load_financial_table_and_kpis, inputs=filter_inputs, outputs=filter_outputs)
                
            # -------------------------------------------------------------
            # ABA 2: GRÁFICOS & ANÁLISES
            # -------------------------------------------------------------
            with gr.TabItem("📈 Gráficos & Relatórios"):
                gr.Markdown("### Análise Visual de Despesas e Fluxo de Caixa")
                with gr.Row():
                    grafico_periodo = gr.Radio(
                        choices=["Mês Atual", "Próximo Mês", "Todos os Meses", "Mês Específico"],
                        value="Mês Atual",
                        label="Período do Gráfico de Categorias"
                    )
                    grafico_mes_custom = gr.Textbox(
                        label="Mês/Ano (MM-YYYY)",
                        placeholder="Ex: 08-2026",
                        visible=False
                    )
                    ano_grafico = gr.Number(
                        label="Ano de Comparação",
                        value=datetime.now().year,
                        precision=0
                    )
                    btn_atualizar_graficos = gr.Button("📊 Gerar Gráficos", variant="primary")
                    
                grafico_periodo.change(toggle_custom_month, inputs=[grafico_periodo], outputs=[grafico_mes_custom])
                
                with gr.Row():
                    plot_donut = gr.Plot(label="Despesas por Categoria")
                    plot_bars = gr.Plot(label="Comparativo Anual (Receitas x Despesas)")
                    
                def refresh_plots(periodo, mes_custom, ano):
                    donut = generate_donut_chart(periodo, mes_custom)
                    bars = generate_monthly_comparison_chart(int(ano) if ano else datetime.now().year)
                    return donut, bars
                    
                btn_atualizar_graficos.click(
                    refresh_plots,
                    inputs=[grafico_periodo, grafico_mes_custom, ano_grafico],
                    outputs=[plot_donut, plot_bars]
                )
                
            # -------------------------------------------------------------
            # ABA 3: NOVO LANÇAMENTO & GERENCIAMENTO
            # -------------------------------------------------------------
            with gr.TabItem("➕ Novo Lançamento & Gerenciamento"):
                with gr.Row():
                    # Coluna: Formulário de Inclusão
                    with gr.Column(scale=3):
                        gr.Markdown("### Inserir Novo Lançamento")
                        novo_tipo = gr.Radio(choices=["Despesa", "Receita"], value="Despesa", label="Tipo")
                        novo_categoria = gr.Dropdown(
                            choices=["Alimentação", "Supermercado", "Transporte", "Combustível", "Farmácia", "Saúde", "Casa", "Condomínio", "Energia", "Internet", "Telefonia", "Lazer", "Cartão de Crédito", "Salário", "Outros"],
                            value="Alimentação",
                            allow_custom_value=True,
                            label="Categoria (escolha ou digite uma nova)"
                        )
                        novo_valor = gr.Number(label="Valor (R$)", value=0.0)
                        novo_descricao = gr.Textbox(label="Descrição", placeholder="Ex: Almoço restaurante, compra mercado...")
                        novo_vencimento = gr.Textbox(label="Data de Vencimento (YYYY-MM-DD)", placeholder=f"Ex: {datetime.now().strftime('%Y-%m-%d')}")
                        btn_salvar = gr.Button("💾 Salvar Lançamento", variant="primary")
                        msg_novo = gr.Markdown()
                        
                    # Coluna: Gerenciamento por ID
                    with gr.Column(scale=2):
                        gr.Markdown("### Gerenciamento de Registro")
                        gr.Markdown("Inative um lançamento incorreto ou restaure um item excluído.")
                        acao_id = gr.Number(label="ID do Lançamento", precision=0)
                        with gr.Row():
                            btn_excluir = gr.Button("🗑️ Excluir (Soft Delete)", variant="stop")
                            btn_restaurar = gr.Button("♻️ Restaurar ID", variant="secondary")
                        msg_acao = gr.Markdown()
                        
                btn_salvar.click(
                    add_new_entry,
                    inputs=[novo_tipo, novo_categoria, novo_valor, novo_descricao, novo_vencimento],
                    outputs=[msg_novo, filtro_categoria]
                )
                btn_excluir.click(delete_entry_by_id, inputs=[acao_id], outputs=[msg_acao])
                btn_restaurar.click(restore_entry_by_id, inputs=[acao_id], outputs=[msg_acao])
                
        # Carga inicial dos dados ao abrir a aplicação
        app.load(load_financial_table_and_kpis, inputs=filter_inputs, outputs=filter_outputs)
        app.load(
            refresh_plots,
            inputs=[grafico_periodo, grafico_mes_custom, ano_grafico],
            outputs=[plot_donut, plot_bars]
        )
        
    return app

def launch_dashboard(server_name: str = "0.0.0.0", server_port: int = 7860):
    """Inicializa o servidor web do Gradio."""
    host = os.environ.get("GRADIO_SERVER_NAME", server_name)
    port = int(os.environ.get("GRADIO_SERVER_PORT", server_port))
    app = build_dashboard()
    print(f"[INFO] Inicializando Dashboard Gradio em http://{host}:{port}")
    app.launch(server_name=host, server_port=port, share=False)

if __name__ == "__main__":
    launch_dashboard()
