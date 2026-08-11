import sys
import os
import re
import telebot
from rich.console import Console

# Adiciona o diretório raiz ao path para importação
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from meu_agente_cli import config, db, agent
from meu_agente_cli.main import handle_slash_command, initialize_components

# Carrega credenciais do Telegram (não requer banco de dados durante a importação)
token = config.get_telegram_bot_token()
authorized_ids = config.get_telegram_authorized_user_ids()

if not token:
    if __name__ == "__main__":
        print("[ERRO] Token do Telegram não encontrado! Configure a variável TELEGRAM_BOT_TOKEN no .env ou config.json.")
        sys.exit(1)
    token = "123456:dummy_token_for_import_validation"

bot = telebot.TeleBot(token)

def is_authorized(message) -> bool:
    """Verifica se o chat ID remetente está na lista de usuários autorizados."""
    if not authorized_ids:
        # Por padrão de segurança reforçada, se a lista estiver vazia, bloqueia tudo
        return False
    return message.chat.id in authorized_ids

def strip_ansi(text: str) -> str:
    """Remove códigos de cores ANSI do console do Rich."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

def format_help_telegram() -> str:
    return (
        "🛡️ *Meu Agente CLI - Comandos Disponíveis:*\n\n"
        "• `/help` - Mostra esta lista de ajuda.\n"
        "• `/status` - Mostra conexões e estado atual de segurança.\n"
        "• `/clear` - Limpa o histórico de conversa com o agente.\n"
        "• `/history <limite>` - Exibe ou altera o tamanho do histórico.\n"
        "• `/models` - Lista e altera os modelos no LM Studio.\n"
        "• `/safe` / `/unsafe` - Ativa ou desativa o Modo Seguro.\n"
        "• `/notes` - Lista todas as anotações salvas.\n"
        "• `/finance` - Resumo financeiro do mês atual.\n"
        "  └─ _Filtros:_ `/finance next`, `/finance all`, `/finance deleted`, `/finance mes=MM-YYYY`, `/finance q=busca`.\n"
        "• `/finance card list` - Lista cartões de crédito cadastrados.\n"
        "• `/finance card add <nome> <fechamento> <vencimento>` - Cadastra cartão.\n"
        "• `/finance card buy <cartao> <categoria> <valor> <parcelas> <descricao> [data]` - Lança despesa no cartão.\n"
        "• `/cron` - Lista tarefas agendadas em segundo plano.\n"
        "• `/backup` - Cria uma cópia de segurança criptografada do banco.\n"
        "• `/restore` - Restaura um backup criptografado.\n"
    )

def format_notes_telegram() -> str:
    notes = db.list_all_user_notes()
    if not notes:
        return "📝 *Memória de Longo Prazo*\n\nNenhuma anotação salva."
        
    linhas = ["📝 *Suas Notas Salvas (Memória de Longo Prazo):*\n"]
    for nid, content, dt in notes:
        dt_str = dt.strftime("%d/%m/%Y %H:%M")
        display_content = content[:200] + "..." if len(content) > 200 else content
        linhas.append(f"• *#{nid}* ({dt_str}):\n  _{display_content}_")
        
    linhas.append("\n_Use o chat normal para fazer perguntas sobre suas notas ou pesquisar por elas._")
    return "\n".join(linhas)

def format_cron_telegram() -> str:
    jobs = db.get_active_cron_jobs()
    if not jobs:
        return "🤖 *Subagentes Agendados (Cron Jobs)*\n\nNenhuma tarefa agendada ativa."
        
    linhas = ["🤖 *Subagentes Agendados (Cron Jobs):*\n"]
    for j in jobs:
        last_str = j["last_run"].strftime("%d/%m/%Y %H:%M") if j["last_run"] else "Nunca"
        next_str = j["next_run"].strftime("%d/%m/%Y %H:%M") if j["next_run"] else "N/A"
        p_desc = j["task_prompt"][:100] + "..." if len(j["task_prompt"]) > 100 else j["task_prompt"]
        
        linhas.append(
            f"• *#{j['id']} - {j['name']}*\n"
            f"  📅 *Cron:* `{j['cron_expression']}`\n"
            f"  🔔 *Próximo Disparo:* `{next_str}` | *Último:* `{last_str}`\n"
            f"  💬 *Prompt:* _{p_desc}_"
        )
        
    return "\n\n".join(linhas)

def format_finance_card_list() -> str:
    cards = db.get_credit_cards().get("cartoes", {})
    if not cards:
        return "💳 *Cartões de Crédito*\n\nNenhum cartão cadastrado. Use `/finance card add` no console ou no bot."
        
    linhas = ["💳 *Cartões de Crédito Cadastrados:*\n"]
    for name, info in cards.items():
        linhas.append(f"• *{name}* - Fechamento: dia `{info['closing_day']}` | Vencimento: dia `{info['due_day']}`")
        
    return "\n".join(linhas)

def format_finance_telegram(parts: list) -> str:
    from datetime import datetime
    now = datetime.now()
    current_month_str = now.strftime("%m-%Y")
    
    if now.month == 12:
        next_month = 1
        next_year = now.year + 1
    else:
        next_month = now.month + 1
        next_year = now.year
    next_month_str = f"{next_month:02d}-{next_year}"
    
    limit = None
    month_year = None
    query = None
    table_title = ""
    show_deleted = False
    
    if len(parts) == 1:
        month_year = current_month_str
        table_title = f"Mês Atual ({current_month_str})"
    else:
        arg = parts[1]
        if arg.lower() == "all":
            table_title = "Todas as Transações"
        elif arg.lower() == "next":
            month_year = next_month_str
            table_title = f"Próximo Mês ({next_month_str})"
        elif arg.lower() == "deleted":
            show_deleted = True
            table_title = "Transações Inativas"
        elif arg.lower().startswith("mes="):
            month_year = arg.split("=")[1]
            table_title = f"Vencimento em {month_year}"
        elif arg.lower().startswith("q="):
            query = arg.split("=")[1]
            table_title = f"Busca por '{query}'"
        else:
            return (
                "❌ *Filtro inválido.*\nUse:\n"
                "• `/finance` (mês atual)\n"
                "• `/finance next` (próximo mês)\n"
                "• `/finance all` (tudo)\n"
                "• `/finance deleted` (excluídos)\n"
                "• `/finance mes=MM-YYYY` (mês específico)\n"
                "• `/finance q=busca` (pesquisar)"
            )
            
    if show_deleted:
        records = db.get_deleted_financial_records()
    else:
        records = db.search_financial_records(limit=limit, month_year=month_year, query=query)
        
    if not records:
        return f"🔍 *{table_title}*\n\nNenhuma transação encontrada com os filtros especificados."
        
    sum_receitas = 0.0
    sum_despesas = 0.0
    for _, rtype, _, val, _, _, _ in records:
        if rtype == "receita":
            sum_receitas += val
        elif rtype == "despesa":
            sum_despesas += val
    sum_saldo = sum_receitas - sum_despesas
    
    resumo = (
        f"📊 *Resumo Financeiro - {table_title}*\n"
        f"🟢 Receitas: `R$ {sum_receitas:.2f}`\n"
        f"🔴 Despesas: `R$ {sum_despesas:.2f}`\n"
        f"⚖️ Saldo: `R$ {sum_saldo:.2f}`\n"
        f"─────────────────────\n"
    )
    
    linhas = []
    for rid, rtype, cat, val, desc, dt, due_dt in records:
        emoji = "🟢" if rtype == "receita" else "🔴"
        dt_str = dt.strftime("%d/%m/%Y")
        due_str = due_dt.strftime("%d/%m/%Y") if due_dt else "N/A"
        desc_str = f" - _{desc}_" if desc else ""
        
        linha = (
            f"• *#{rid}* ({dt_str}) | {emoji} {cat}\n"
            f"  *Valor:* `R$ {val:.2f}` | *Venc:* `{due_str}`{desc_str}"
        )
        linhas.append(linha)
        
    return resumo + "\n\n".join(linhas)

def format_markdown_for_telegram(text: str) -> str:
    """
    Converte markdown padrão da LLM para o formato aceito pelo Telegram Markdown (v1).
    - Substitui '**' por '*' para negrito, preservando blocos de código.
    """
    if not text:
        return text
        
    code_blocks = []
    def replace_code_block(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"
        
    # Regex para capturar blocos de código ```...``` e `...`
    text_temp = re.sub(r'```[\s\S]*?```|`.*?`', replace_code_block, text)
    
    # Converte negrito de '**' para '*'
    text_temp = text_temp.replace('**', '*')
    
    # Restaura blocos de código
    for i, block in enumerate(code_blocks):
        text_temp = text_temp.replace(f"__CODE_BLOCK_{i}__", block)
        
    return text_temp

def reply_formatted(message, text):
    """Envia a resposta formatada para o Telegram, caindo para texto puro se o parser falhar."""
    try:
        formatted_text = format_markdown_for_telegram(text)
        bot.reply_to(message, formatted_text, parse_mode="Markdown")
    except Exception as e:
        print(f"[Warning] Falha ao renderizar Markdown no Telegram: {e}")
        try:
            bot.reply_to(message, text)
        except Exception as e_fallback:
            print(f"[ERROR] Falha crítica ao enviar mensagem: {e_fallback}")

@bot.message_handler(func=lambda msg: True)
def handle_incoming_message(message):
    sender_username = message.from_user.username or "SemUsername"
    print(f"[REQUISICAO] Mensagem recebida de @{sender_username} (ID: {message.chat.id}): '{message.text}'")
    
    if not is_authorized(message):
        print(f"[BLOQUEADO] Tentativa de acesso não autorizada de Chat ID: {message.chat.id} (@{sender_username})")
        bot.reply_to(message, "Acesso negado. Este bot do agente é privado.")
        return
        
    text = message.text.strip()
    if not text:
        return
        
    # Tratamento de Comandos Especiais com Barra
    if text.startswith("/"):
        if text in ["/exit", "/quit"]:
            bot.reply_to(message, "Comandos de finalização (/exit, /quit) são desativados via chat remoto.")
            return
            
        bot.send_chat_action(message.chat.id, 'typing')
        
        parts = text.split()
        cmd = parts[0].lower()
        
        # Filtros e roteamentos personalizados para exibir layouts bonitos no celular
        try:
            if cmd == "/help":
                bot.reply_to(message, format_help_telegram(), parse_mode="Markdown")
                return
            elif cmd == "/notes":
                bot.reply_to(message, format_notes_telegram(), parse_mode="Markdown")
                return
            elif cmd == "/cron" and len(parts) == 1:
                bot.reply_to(message, format_cron_telegram(), parse_mode="Markdown")
                return
            elif cmd == "/finance":
                # Verifica se é uma listagem/filtro normal ou uma ação (delete, restore, import, card add/buy)
                is_list = True
                if len(parts) > 1:
                    sub = parts[1].lower()
                    if sub in ["delete", "restore", "import", "csv"] or (sub in ["card", "cartao"] and len(parts) > 2 and parts[2].lower() in ["add", "buy"]):
                        is_list = False
                        
                if is_list:
                    # Verifica se é listagem de cartões de crédito
                    if len(parts) > 1 and parts[1].lower() in ["card", "cartao"] and (len(parts) == 2 or parts[2].lower() == "list"):
                        bot.reply_to(message, format_finance_card_list(), parse_mode="Markdown")
                    else:
                        bot.reply_to(message, format_finance_telegram(parts), parse_mode="Markdown")
                    return
            
            # Caso contrário, cai no fallback de capturar o console Rich do terminal
            from meu_agente_cli.main import console
            
            with console.capture() as capture:
                handle_slash_command(text)
                
            output = capture.get()
            clean_output = strip_ansi(output).strip()
            
            if not clean_output:
                clean_output = "Comando executado sem retorno textual."
                
            # Formata tabelas ou desenhos unicode em monospace no Telegram
            if any(char in clean_output for char in ["┏", "┃", "─", "┼", "│", "┌", "╭", "╰"]):
                if len(clean_output) > 4000:
                    clean_output = clean_output[:3900] + "\n... (saída longa truncada)"
                bot.reply_to(message, f"```\n{clean_output}\n```", parse_mode="Markdown")
            else:
                if len(clean_output) > 4000:
                    clean_output = clean_output[:3900] + "\n... (saída longa truncada)"
                bot.reply_to(message, clean_output)
        except Exception as e:
            bot.reply_to(message, f"Erro ao executar comando: {e}")
            
    else:
        # Conversa normal com o Agente (Pensamento + Execução Silenciosa de Ferramentas)
        bot.send_chat_action(message.chat.id, 'typing')
        try:
            telegram_instruction = "\n\n[INSTRUÇÃO DO SISTEMA: Você está respondendo via Telegram. Formate sua resposta com listas limpas, quebras de linhas duplas e emojis, sem tabelas ASCII ou caixas unicode complexas, pois serão exibidas em uma tela estreita de celular.]"
            telegram_prompt = f"{text}{telegram_instruction}"
            response = agent.process_agent_turn_silent(telegram_prompt)
            reply_formatted(message, response)
        except Exception as e:
            bot.reply_to(message, f"Erro no processamento da conversa: {e}")

def extract_pdf_text(file_path: str) -> str:
    """Extrai texto de um arquivo PDF usando a biblioteca pypdf."""
    import pypdf
    texto = []
    try:
        reader = pypdf.PdfReader(file_path)
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                texto.append(f"--- Página {i+1} ---\n{page_text}")
        return "\n".join(texto).strip()
    except Exception as e:
        return f"[Erro ao ler PDF: {str(e)}]"

def extract_docx_text(file_path: str) -> str:
    """Extrai texto de um arquivo do Word (.docx) usando python-docx."""
    import docx
    try:
        doc = docx.Document(file_path)
        texto = []
        for paragraph in doc.paragraphs:
            if paragraph.text:
                texto.append(paragraph.text)
        for table in doc.tables:
            for row in table.rows:
                row_text = [cell.text for cell in row.cells]
                texto.append(" | ".join(row_text))
        return "\n".join(texto).strip()
    except Exception as e:
        return f"[Erro ao ler arquivo Word: {str(e)}]"

def extract_xlsx_text(file_path: str) -> str:
    """Extrai dados de planilhas Excel (.xlsx) formatando como tabelas legíveis."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        sheets_data = []
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            sheets_data.append(f"=== Planilha: {sheet_name} ===")
            for row in sheet.iter_rows(values_only=True):
                if any(cell is not None for cell in row):
                    row_str = " | ".join(str(cell) if cell is not None else "" for cell in row)
                    sheets_data.append(row_str)
        return "\n".join(sheets_data).strip()
    except Exception as e:
        return f"[Erro ao ler planilha Excel: {str(e)}]"

@bot.message_handler(content_types=['document'])
def handle_document_upload(message):
    if not is_authorized(message):
        print(f"[BLOQUEADO] Documento recebido de Chat ID não autorizado: {message.chat.id}")
        bot.reply_to(message, "Acesso negado. Este bot do agente é privado.")
        return

    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Cria a pasta uploads/ se não existir
        uploads_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "uploads"))
        os.makedirs(uploads_dir, exist_ok=True)
        
        file_name = message.document.file_name
        file_path = os.path.join(uploads_dir, file_name)
        
        with open(file_path, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        print(f"[DOCUMENTO] Salvo com sucesso em: {file_path}")
        
        # Se for um CSV para importação financeira
        if file_name.lower().endswith(".csv"):
            bot.reply_to(message, f"📥 Arquivo CSV `{file_name}` recebido. Iniciando a importação financeira...")
            dest_csv = os.path.join(uploads_dir, "finance.csv")
            import shutil
            shutil.copy(file_path, dest_csv)
            
            from meu_agente_cli.main import console
            with console.capture() as capture:
                handle_slash_command("/finance import")
            output = strip_ansi(capture.get()).strip()
            bot.reply_to(message, f"✅ *Resultado da Importação:*\n{output}", parse_mode="Markdown")
            return
            
        # Para outros tipos de documentos, extraímos o texto
        text_content = ""
        ext = os.path.splitext(file_name.lower())[1]
        
        if ext == ".pdf":
            text_content = extract_pdf_text(file_path)
        elif ext == ".docx":
            text_content = extract_docx_text(file_path)
        elif ext in [".xlsx", ".xls"]:
            text_content = extract_xlsx_text(file_path)
        elif ext in [".txt", ".log", ".json", ".py", ".sql", ".csv", ".xml", ".ini", ".yaml", ".yml", ".md"]:
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    text_content = f.read()
            except Exception as e:
                text_content = f"[Erro ao ler arquivo de texto: {e}]"
        else:
            bot.reply_to(message, f"❌ Formato de arquivo `{ext}` não suportado para análise automática.")
            return

        if not text_content.strip():
            bot.reply_to(message, "⚠️ O arquivo enviado parece estar vazio ou não foi possível extrair o texto dele.")
            return
            
        # Limita o tamanho do texto extraído para não estourar o limite de tokens
        if len(text_content) > 25000:
            text_content = text_content[:25000] + "\n\n... (conteúdo longo do arquivo truncado para análise) ..."
            
        caption = message.caption.strip() if message.caption else "Analise e resuma o conteúdo deste arquivo."
        
        telegram_instruction = "\n\n[INSTRUÇÃO DO SISTEMA: Você está respondendo via Telegram. Formate sua resposta com listas limpas, quebras de linhas duplas e emojis, sem tabelas ASCII ou caixas unicode complexas, pois serão exibidas em uma tela estreita de celular.]"
        user_prompt = (
            f"[Arquivo Anexado: {file_name}]\n"
            f"--- Início do Conteúdo do Arquivo ---\n"
            f"{text_content}\n"
            f"--- Fim do Conteúdo do Arquivo ---\n\n"
            f"Instrução do Usuário: {caption}"
            f"{telegram_instruction}"
        )
        
        bot.reply_to(message, f"📖 Arquivo `{file_name}` processado ({len(text_content)} caracteres). Enviando para análise da inteligência artificial...")
        
        response = agent.process_agent_turn_silent(user_prompt)
        reply_formatted(message, response)
        
    except Exception as e:
        bot.reply_to(message, f"❌ Ocorreu um erro ao processar o arquivo: {e}")

@bot.message_handler(content_types=['photo'])
def handle_photo_upload(message):
    if not is_authorized(message):
        print(f"[BLOQUEADO] Imagem recebida de Chat ID não autorizado: {message.chat.id}")
        bot.reply_to(message, "Acesso negado. Este bot do agente é privado.")
        return

    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        import base64
        # Pega a foto na maior resolução disponível
        photo = message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Codifica a imagem em base64
        base64_str = base64.b64encode(downloaded_file).decode('utf-8')
        
        # Legenda/pergunta do usuário
        caption = message.caption.strip() if message.caption else "O que está nesta imagem? Descreva e analise em detalhes."
        
        telegram_instruction = "\n\n[INSTRUÇÃO DO SISTEMA: Você está respondendo via Telegram. Formate sua resposta com listas limpas, quebras de linhas duplas e emojis, sem tabelas ASCII ou caixas unicode complexas, pois serão exibidas em uma tela estreita de celular.]"
        # Monta a estrutura da mensagem multimodal
        multimodal_prompt = [
            {"type": "text", "text": f"{caption}{telegram_instruction}"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_str}"}}
        ]
        
        bot.reply_to(message, "📸 Imagem recebida e codificada. Enviando para análise visual da inteligência artificial...")
        
        response = agent.process_agent_turn_silent(multimodal_prompt)
        reply_formatted(message, response)
        
    except Exception as e:
        bot.reply_to(message, f"❌ Ocorreu um erro ao processar a imagem: {e}")

if __name__ == "__main__":
    # Inicializa banco de dados, LLM e segurança do projeto apenas quando executado diretamente
    if not initialize_components():
        print("[ERRO] Falha ao inicializar componentes críticos do agente.")
        sys.exit(1)

    print(f"===========================================================")
    print(f"Bot do Telegram do Meu Agente CLI Iniciado com Sucesso!")
    print(f"IDs Autorizados cadastrados: {authorized_ids}")
    print(f"Aguardando interações...")
    print(f"===========================================================")
    bot.infinity_polling()
