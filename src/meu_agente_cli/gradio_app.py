"""
Dashboard Web do Meu Agente CLI (Gradio)
Interface gráfica para chat com o agente, gerenciamento de memória/notas,
servidores MCP (integrações como finanças e CRM), agentes especialistas e cron jobs.
"""

import os
import sys
import logging
import gradio as gr
import pandas as pd
from datetime import datetime

from meu_agente_cli import db, agent, llm, mcp_client, tools

logger = logging.getLogger(__name__)

# =====================================================================
# FUNÇÕES DE APOIO - CHAT
# =====================================================================

def chat_with_agent(user_message, history):
    """Executa um ciclo do agente e atualiza o histórico do chat."""
    if not user_message or not user_message.strip():
        return history, ""
    
    # Adiciona a mensagem do usuário no histórico
    history = history or []
    history.append({"role": "user", "content": user_message})
    
    try:
        # Executa o turno do agente
        response = agent.process_agent_turn_silent(user_message)
        history.append({"role": "assistant", "content": response})
    except Exception as e:
        logger.exception("Erro ao processar mensagem no chat Gradio")
        history.append({"role": "assistant", "content": f"⚠️ Erro ao processar mensagem: {str(e)}"})
        
    return history, ""

def clear_chat():
    """Limpa o histórico da sessão e do banco de dados."""
    db.clear_chat_history()
    return []

# =====================================================================
# FUNÇÕES DE APOIO - NOTAS & MEMÓRIA
# =====================================================================

def load_notes_df(query=""):
    """Carrega as notas salvas no PostgreSQL."""
    try:
        if query and query.strip():
            notes = db.search_user_notes(query.strip())
        else:
            notes = db.list_all_user_notes()
            
        data = []
        for nid, content, created_at in notes:
            data.append({
                "ID": nid,
                "Data": created_at.strftime("%d/%m/%Y %H:%M") if hasattr(created_at, "strftime") else str(created_at),
                "Conteúdo": content
            })
        return pd.DataFrame(data) if data else pd.DataFrame(columns=["ID", "Data", "Conteúdo"])
    except Exception as e:
        logger.error(f"Erro ao carregar notas: {e}")
        return pd.DataFrame(columns=["ID", "Data", "Conteúdo"])

def add_new_note(content):
    """Adiciona uma nova anotação persistente."""
    if not content or not content.strip():
        return "Erro: O conteúdo não pode ser vazio.", load_notes_df()
    success = db.add_user_note(content.strip())
    if success:
        return "✅ Nota salva com sucesso na memória do agente!", load_notes_df()
    return "❌ Erro ao salvar nota no banco de dados.", load_notes_df()

def delete_note_by_id(note_id):
    """Remove uma nota pelo ID."""
    try:
        nid = int(note_id)
        if db.delete_user_note(nid):
            return f"✅ Nota #{nid} removida com sucesso!", load_notes_df()
        return f"❌ Nota #{nid} não encontrada.", load_notes_df()
    except Exception as e:
        return f"Erro: {e}", load_notes_df()

# =====================================================================
# FUNÇÕES DE APOIO - SERVIDORES MCP
# =====================================================================

def load_mcp_servers_df():
    """Carrega lista de servidores MCP configurados."""
    try:
        servers = db.list_mcp_servers()
        data = []
        for s in servers:
            data.append({
                "Nome": s.get("name", ""),
                "URL": s.get("url", ""),
                "Transport": s.get("transport", "sse"),
                "Ativo": "🟢 Sim" if s.get("is_active") else "🔴 Não",
                "Criado em": str(s.get("created_at", ""))[:19]
            })
        return pd.DataFrame(data) if data else pd.DataFrame(columns=["Nome", "URL", "Transport", "Ativo", "Criado em"])
    except Exception as e:
        logger.error(f"Erro ao carregar servidores MCP: {e}")
        return pd.DataFrame(columns=["Nome", "URL", "Transport", "Ativo", "Criado em"])

def add_mcp_server(name, url, api_key, transport):
    """Adiciona um servidor MCP."""
    if not name or not url:
        return "❌ Nome e URL são obrigatórios.", load_mcp_servers_df()
    success = db.save_mcp_server(
        name=name.strip(),
        url=url.strip(),
        api_key=api_key.strip() if api_key else None,
        transport=transport or "sse"
    )
    if success:
        return f"✅ Servidor MCP '{name}' configurado com sucesso!", load_mcp_servers_df()
    return "❌ Falha ao salvar servidor MCP.", load_mcp_servers_df()

def list_mcp_tools_view(server_name):
    """Lista as ferramentas descobertas de um servidor MCP."""
    if not server_name or not server_name.strip():
        return tools.manage_mcp_tool(action="list_tools")
    return tools.manage_mcp_tool(action="list_tools", name=server_name.strip())

# =====================================================================
# FUNÇÕES DE APOIO - AGENTES ESPECIALISTAS
# =====================================================================

def load_agents_df():
    """Carrega agentes cadastrados no Agent Hub."""
    try:
        agents = db.list_agents()
        active_slug = db.get_active_agent_slug()
        data = []
        for a in agents:
            is_active = a.get("slug") == active_slug
            data.append({
                "Status": "⭐ Ativo" if is_active else "Inativo",
                "Ícone": a.get("icon", "🤖"),
                "Slug": a.get("slug", ""),
                "Nome": a.get("name", ""),
                "Descrição": a.get("description", "")
            })
        return pd.DataFrame(data) if data else pd.DataFrame(columns=["Status", "Ícone", "Slug", "Nome", "Descrição"])
    except Exception as e:
        logger.error(f"Erro ao carregar agentes: {e}")
        return pd.DataFrame(columns=["Status", "Ícone", "Slug", "Nome", "Descrição"])

def switch_active_agent(slug):
    """Altera o agente ativo."""
    if not slug:
        return "❌ Informe o slug do agente.", load_agents_df()
    if db.set_active_agent_slug(slug.strip()):
        return f"✅ Agente ativo alterado para '{slug}'!", load_agents_df()
    return f"❌ Agente '{slug}' não encontrado.", load_agents_df()

# =====================================================================
# FUNÇÕES DE APOIO - CRON JOBS
# =====================================================================

def load_cron_jobs_df():
    """Carrega as tarefas agendadas."""
    try:
        jobs = db.get_active_cron_jobs()
        data = []
        for j in jobs:
            data.append({
                "ID": j.get("id"),
                "Nome": j.get("name", ""),
                "Cron": j.get("cron_expression", ""),
                "Próxima Execução": str(j.get("next_run", ""))[:19],
                "Última Execução": str(j.get("last_run", "Nunca"))[:19] if j.get("last_run") else "Nunca",
                "Prompt/Instrução": j.get("task_prompt", "")
            })
        return pd.DataFrame(data) if data else pd.DataFrame(columns=["ID", "Nome", "Cron", "Próxima Execução", "Última Execução", "Prompt/Instrução"])
    except Exception as e:
        logger.error(f"Erro ao carregar cron jobs: {e}")
        return pd.DataFrame(columns=["ID", "Nome", "Cron", "Próxima Execução", "Última Execução", "Prompt/Instrução"])

# =====================================================================
# CONSTRUÇÃO DA INTERFACE GRADIO
# =====================================================================

def build_app():
    # Inicializa o banco de dados antes de renderizar
    db.init_database()
    
    with gr.Blocks(title="Meu Agente CLI - Dashboard") as app:
        gr.Markdown(
            """
            # 🤖 Meu Agente CLI — Painel de Controle
            *Assistente Pessoal Autônomo com suporte a Servidores MCP, Meta-Agente e Memória de Longo Prazo.*
            """
        )
        
        with gr.Tabs():
            # -------------------------------------------------------------
            # ABA 1: CHAT COM O AGENTE
            # -------------------------------------------------------------
            with gr.TabItem("💬 Chat com o Agente"):
                chatbot = gr.Chatbot(height=520, type="messages", label="Conversa com o Agente")
                with gr.Row():
                    msg_input = gr.Textbox(
                        placeholder="Digite sua mensagem ou comando para o agente (ex: 'Quais ferramentas MCP estão ativas?', 'Qual a previsão do tempo?')...",
                        scale=8,
                        show_label=False
                    )
                    btn_send = gr.Button("Enviar 🚀", variant="primary", scale=1)
                    btn_clear = gr.Button("Limpar 🗑️", scale=1)
                
                # Interações
                btn_send.click(chat_with_agent, inputs=[msg_input, chatbot], outputs=[chatbot, msg_input])
                msg_input.submit(chat_with_agent, inputs=[msg_input, chatbot], outputs=[chatbot, msg_input])
                btn_clear.click(clear_chat, outputs=[chatbot])

            # -------------------------------------------------------------
            # ABA 2: MEMÓRIA & NOTAS
            # -------------------------------------------------------------
            with gr.TabItem("📝 Memória & Notas"):
                gr.Markdown("### Base de Conhecimento e Notas de Longo Prazo")
                with gr.Row():
                    note_search = gr.Textbox(placeholder="Buscar por palavra-chave...", label="Pesquisar Notas", scale=4)
                    btn_search_notes = gr.Button("Buscar 🔍", scale=1)
                    btn_refresh_notes = gr.Button("Recarregar 🔄", scale=1)
                
                notes_table = gr.Dataframe(value=load_notes_df, headers=["ID", "Data", "Conteúdo"], interactive=False)
                
                with gr.Accordion("Adicionar ou Excluir Anotação", open=False):
                    with gr.Row():
                        with gr.Column(scale=3):
                            new_note_text = gr.Textbox(lines=2, placeholder="Digite o conteúdo da nova nota...", label="Nova Anotação")
                            btn_add_note = gr.Button("Salvar Nota 💾", variant="primary")
                        with gr.Column(scale=1):
                            del_note_id = gr.Number(label="ID para Excluir", precision=0)
                            btn_del_note = gr.Button("Excluir Nota ❌", variant="stop")
                    note_status = gr.Textbox(label="Status", interactive=False)

                btn_search_notes.click(load_notes_df, inputs=[note_search], outputs=[notes_table])
                btn_refresh_notes.click(load_notes_df, inputs=[], outputs=[notes_table])
                btn_add_note.click(add_new_note, inputs=[new_note_text], outputs=[note_status, notes_table])
                btn_del_note.click(delete_note_by_id, inputs=[del_note_id], outputs=[note_status, notes_table])

            # -------------------------------------------------------------
            # ABA 3: SERVIDORES MCP (MODEL CONTEXT PROTOCOL)
            # -------------------------------------------------------------
            with gr.TabItem("🔌 Servidores MCP"):
                gr.Markdown("### Conexões Externas via Model Context Protocol (MCP)")
                gr.Markdown("Permite ao agente conectar-se a serviços externos (como o **meu-financeiro** ou CRM) para consumir ferramentas dinamicamente.")
                
                mcp_table = gr.Dataframe(value=load_mcp_servers_df, headers=["Nome", "URL", "Transport", "Ativo", "Criado em"], interactive=False)
                btn_refresh_mcp = gr.Button("Atualizar Lista de Servidores 🔄")
                
                with gr.Row():
                    with gr.Column(scale=2):
                        gr.Markdown("#### Cadastrar Novo Servidor MCP")
                        mcp_name = gr.Textbox(label="Nome do Servidor", placeholder="ex: meu-financeiro")
                        mcp_url = gr.Textbox(label="URL do Servidor", placeholder="ex: http://meu-financeiro:8000/sse")
                        mcp_key = gr.Textbox(label="API Key / Token (Opcional)", placeholder="Bearer token se houver")
                        mcp_transport = gr.Dropdown(choices=["sse", "http"], value="sse", label="Transporte")
                        btn_save_mcp = gr.Button("Salvar Conexão MCP 💾", variant="primary")
                        mcp_save_status = gr.Textbox(label="Resultado", interactive=False)
                    
                    with gr.Column(scale=3):
                        gr.Markdown("#### Explorador de Ferramentas MCP")
                        mcp_inspect_name = gr.Textbox(label="Nome do Servidor para Inspecionar (vazio para todos)", placeholder="meu-financeiro")
                        btn_inspect_tools = gr.Button("Listar Ferramentas Descobertas 🔍")
                        tools_output = gr.Markdown("Clique no botão para consultar as ferramentas ativas.")

                btn_refresh_mcp.click(load_mcp_servers_df, outputs=[mcp_table])
                btn_save_mcp.click(add_mcp_server, inputs=[mcp_name, mcp_url, mcp_key, mcp_transport], outputs=[mcp_save_status, mcp_table])
                btn_inspect_tools.click(list_mcp_tools_view, inputs=[mcp_inspect_name], outputs=[tools_output])

            # -------------------------------------------------------------
            # ABA 4: AGENTES ESPECIALISTAS (AGENT HUB)
            # -------------------------------------------------------------
            with gr.TabItem("🤖 Agentes Especialistas"):
                gr.Markdown("### Central de Agentes (Agent Hub)")
                agents_table = gr.Dataframe(value=load_agents_df, headers=["Status", "Ícone", "Slug", "Nome", "Descrição"], interactive=False)
                
                with gr.Row():
                    switch_slug = gr.Textbox(label="Slug do Agente para Ativar", placeholder="ex: geral, estudo")
                    btn_switch_agent = gr.Button("Ativar Agente ⭐", variant="primary")
                agent_status = gr.Textbox(label="Status", interactive=False)
                
                btn_switch_agent.click(switch_active_agent, inputs=[switch_slug], outputs=[agent_status, agents_table])

            # -------------------------------------------------------------
            # ABA 5: AGENDAMENTOS (CRON JOBS)
            # -------------------------------------------------------------
            with gr.TabItem("⏰ Agendamentos (Cron Jobs)"):
                gr.Markdown("### Tarefas Agendadas em Segundo Plano")
                cron_table = gr.Dataframe(value=load_cron_jobs_df, headers=["ID", "Nome", "Cron", "Próxima Execução", "Última Execução", "Prompt/Instrução"], interactive=False)
                btn_refresh_cron = gr.Button("Atualizar Agendamentos 🔄")
                btn_refresh_cron.click(load_cron_jobs_df, outputs=[cron_table])

    return app

if __name__ == "__main__":
    app = build_app()
    server_port = int(os.environ.get("GRADIO_SERVER_PORT", 7860))
    server_name = os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0")
    print(f"🚀 Iniciando Dashboard do Meu Agente CLI em http://{server_name}:{server_port}...")
    app.launch(server_name=server_name, server_port=server_port)
