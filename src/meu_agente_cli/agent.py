import getpass
import json
from typing import Any
from rich.console import Console, Group
from rich.panel import Panel
from rich.markdown import Markdown
from rich.live import Live
from rich.text import Text
from rich.prompt import Confirm, Prompt
import meu_agente_cli.db as db
import meu_agente_cli.llm as llm
import meu_agente_cli.tools as tools
import meu_agente_cli.security as security

def render_agent_response(content: str) -> Group:
    """Combina o cabeçalho 'Agente IA: 🤖' em azul com o conteúdo em Markdown."""
    prefix = Text("Agente IA: 🤖\n", style="bold blue")
    return Group(prefix, Markdown(content))

def process_stream_speculative(stream_generator, console: Console):
    """
    Consome o gerador de streaming de forma especulativa.
    - Se a resposta começar com '{' ou '```', assume que é uma chamada de ferramenta
      e consome todo o fluxo silenciosamente para processá-la.
    - Caso contrário, faz o streaming do texto normal renderizando em Markdown no terminal via Live display.
    Retorna (is_tool_call, full_content).
    """
    buffer = ""
    is_tool = False
    
    # Consome pequenos pedaços para inspecionar o início da resposta
    for chunk in stream_generator:
        buffer += chunk
        stripped = buffer.lstrip()
        if len(stripped) >= 3:
            if stripped.startswith("{") or stripped.startswith("```"):
                is_tool = True
                # É uma ferramenta: consome todo o resto silenciosamente
                for rest in stream_generator:
                    buffer += rest
                break
            else:
                # É texto normal: sai do loop de detecção mantendo o buffer released
                buffer_released = buffer
                break
    else:
        # O fluxo terminou antes de acumular 3 caracteres
        stripped = buffer.lstrip()
        if stripped.startswith("{") or stripped.startswith("```"):
            is_tool = True
            
    if is_tool:
        return True, buffer
    else:
        # Streaming normal do restante do fluxo usando Live Markdown Rendering
        full_content = buffer_released if 'buffer_released' in locals() else buffer
        
        with Live(render_agent_response(full_content), console=console, refresh_per_second=8, auto_refresh=False) as live:
            # Primeiro atualiza o display com o buffer inicial
            live.update(render_agent_response(full_content))
            live.refresh()
            
            for chunk in stream_generator:
                full_content += chunk
                live.update(render_agent_response(full_content))
                live.refresh()
                
        return False, full_content

def handle_cli_tool_execution(command: str, console: Console) -> str:
    """
    Trata a execução de um comando do sistema, lidando com confirmações
    e transição para o modo não-seguro com verificação de senha.
    """
    # Verifica a segurança do comando
    if security.is_command_safe(command):
        console.print(f"[bold green][Seguro][/bold green] Executando comando: [cyan]{command}[/cyan]")
        code, out, err = security.run_bash_command(command)
        return f"Código de saída: {code}\nStdout: {out}\nStderr: {err}"
        
    # Se for comando inseguro
    console.print(Panel(
        f"[bold yellow]AVISO DE SEGURANÇA:[/bold yellow]\n"
        f"O LLM solicitou a execução de um comando não listado como seguro:\n"
        f"[cyan]{command}[/cyan]",
        title="Verificação de Comando",
        border_style="yellow"
    ))
    
    # Se está em Modo Seguro, exige senha para ir para Não-Seguro
    if security.is_safe_mode():
        console.print("[yellow]O agente está em Modo Seguro. Digite a senha de segurança para executar comandos não-seguros.[/yellow]")
        password = getpass.getpass("Senha de Segurança: ")
        if not security.verify_password(password):
            console.print("[red]Erro: Senha incorreta! Execução bloqueada.[/red]")
            return "Erro: Senha de segurança incorreta. O comando foi bloqueado."
            
        # Entra temporariamente no modo não-seguro para executar
        console.print("[green]Senha validada. Entrando em Modo Não-Seguro para este comando.[/green]")
        security.set_safe_mode(False)
        
    # Pergunta pela confirmação final do comando
    confirm = Confirm.ask(f"Deseja realmente executar o comando '[cyan]{command}[/cyan]'?", default=False)
    if not confirm:
        console.print("[red]Execução cancelada pelo usuário.[/red]")
        return "Erro: O usuário cancelou a execução do comando."
        
    # Executa o comando
    console.print(f"[bold red][Não-Seguro][/bold red] Executando: [cyan]{command}[/cyan]")
    code, out, err = security.run_bash_command(command)
    return f"Código de saída: {code}\nStdout: {out}\nStderr: {err}"

def process_agent_turn(user_input: str, console: Console) -> None:
    """
    Executa um turno completo de pensamento do agente.
    Monta histórico, interage com LLM, trata ferramentas e exibe streaming.
    """
    model = db.get_setting("active_model", "google/gemma-4-31b-qat")
    
    # Carrega histórico recente usando o limite configurado (padrão: 4)
    history_limit = db.get_chat_history_limit()
    history = db.get_chat_history(limit=history_limit)
    
    # Monta a estrutura de mensagens para o LLM
    messages = []
    messages.append({"role": "system", "content": llm.SYSTEM_PROMPT})
    for sender, msg in history:
        messages.append({"role": sender, "content": msg})
    messages.append({"role": "user", "content": user_input})
    
    max_turns = 5
    for turn in range(max_turns):
        # Exibe spinner discreto enquanto aguarda tokens iniciais
        with console.status("[bold blue]Pensando...", spinner="dots"):
            stream_gen = llm.chat_completion(model, messages, stream=True)
            
        # Consome de forma especulativa (stream se texto, consome se tool)
        is_tool, response_text = process_stream_speculative(stream_gen, console)
        
        # Tenta extrair qualquer chamada de ferramenta contida na resposta (mesmo com texto introdutório)
        tool_call = llm.parse_tool_call(response_text)
        
        if not is_tool and not tool_call:
            # Resposta direta ao usuário concluída (sem ferramenta encontrada)
            db.save_chat_message("user", user_input)
            db.save_chat_message("assistant", response_text)
            console.print()  # Quebra de linha final
            check_and_trigger_memory_extraction()
            break
            
        # Trata chamada de ferramenta
        if not tool_call:
            # Se falhou no parser de JSON mas parecia tool, exibe como texto
            console.print(response_text)
            db.save_chat_message("user", user_input)
            db.save_chat_message("assistant", response_text)
            console.print()
            check_and_trigger_memory_extraction()
            break
            
        tool_name = tool_call.get("tool")
        args = tool_call.get("args", {})
        
        # Exibe progresso da ferramenta
        console.print(f"\n[bold blue][Tool][/bold blue] Chamando: [yellow]{tool_name}[/yellow]...")
        
        # Executa a ferramenta
        tool_result = execute_tool_by_name(tool_name, args, console, allow_interactive=True)
            
        console.print(f"[bold blue][Tool Resultado][/bold blue] Finalizado.")
        
        # Alimenta o histórico do LLM para a próxima rodada
        messages.append({"role": "assistant", "content": response_text})
        messages.append({"role": "user", "content": f"Resultado da ferramenta {tool_name}:\n{tool_result}"})
    else:
        console.print("[red]Erro: Limite de iterações excedido pelo agente (loop de ferramentas).[/red]")

def execute_tool_by_name(tool_name: str, args: dict, console: Console, allow_interactive: bool = True) -> str:
    """Executa a ferramenta solicitada e retorna o resultado formatado em texto."""
    import json
    
    # Normaliza aliases de ferramentas customizadas para evitar alucinações
    if tool_name == "get_freelance_tip":
        tool_name = "daily_tips_tool"
        
    try:
        if tool_name == "get_weather":
            return tools.get_weather(**args)
        elif tool_name == "get_financial_quote":
            return tools.get_financial_quote(**args)
        elif tool_name == "get_news":
            return tools.get_news(**args)
        elif tool_name == "finance_tool":
            return tools.finance_tool(**args)
        elif tool_name == "notes_tool":
            return tools.notes_tool(**args)
        elif tool_name == "execute_cli_command":
            cmd = args.get("command", "")
            if allow_interactive:
                return handle_cli_tool_execution(cmd, console)
            else:
                # No modo não interativo (como Telegram), executamos apenas se for seguro!
                if security.is_command_safe(cmd):
                    code, out, err = security.run_bash_command(cmd)
                    return f"Código de saída: {code}\nStdout: {out}\nStderr: {err}"
                else:
                    return f"Erro: O comando '{cmd}' não está na whitelist e a execução remota de comandos não-seguros foi bloqueada."
        elif tool_name == "calculator_tool":
            return tools.calculator_tool(**args)
        elif tool_name == "invest_tool":
            return tools.invest_tool(**args)
        else:
            # Tenta carregar a ferramenta dinamicamente do custom_tools.json
            from pathlib import Path
            import importlib
            
            custom_config_path = Path(__file__).parent.resolve() / "custom_tools" / "custom_tools.json"
            loaded = False
            if custom_config_path.exists():
                try:
                    with open(custom_config_path, "r", encoding="utf-8") as f_custom:
                        custom_data = json.load(f_custom)
                    custom_tools = custom_data.get("tools", {})
                    if tool_name in custom_tools:
                        tool_info = custom_tools[tool_name]
                        python_module = tool_info.get("python_module")
                        function_name = tool_info.get("function_name", "run")
                        
                        # Importa o módulo dinamicamente e executa a função
                        module = importlib.import_module(python_module)
                        importlib.reload(module)  # Garante que as atualizações do arquivo físico sejam lidas
                        func = getattr(module, function_name)
                        result = func(**args)
                        return result
                except Exception as ex:
                    return f"Erro ao tentar importar/executar ferramenta customizada '{tool_name}': {ex}"
            
            return f"Erro: Ferramenta '{tool_name}' não suportada."
    except Exception as e:
        return f"Erro na execução da ferramenta '{tool_name}': {str(e)}"

def process_agent_turn_silent(user_input: Any, use_sys_prompt: bool = True, use_history:bool=True) -> str:
    """
    Executa um turno completo de pensamento do agente de forma silenciosa.
    Ideal para integrações como Telegram, onde não queremos poluir o console do sistema.
    """
    model = db.get_setting("active_model", "google/gemma-4-31b-qat")
    history_limit = db.get_chat_history_limit()
    history = db.get_chat_history(limit=history_limit)
    
    # Cria uma instância de Console isolada para passar aos métodos que exigem assinatura
    silent_console = Console(color_system=None, force_terminal=False)
    
    messages = []
    if use_sys_prompt:
        messages.append({"role": "system", "content": llm.SYSTEM_PROMPT})
    else:
        messages.append({"role": "system", "content": "Você é um assistente de engenharia clínica que analisa questões e responde de forma objetiva"})
    if use_history:
        for sender, msg in history:
            messages.append({"role": sender, "content": msg})
    messages.append({"role": "user", "content": user_input})
    
    max_turns = 15
    for turn in range(max_turns):
        # Conversa síncrona com o LLM (sem streaming)
        response_text = llm.chat_completion(model, messages, stream=False)
        tool_call = llm.parse_tool_call(response_text)
        
        if not tool_call:
            # Resposta final do agente
            db.save_chat_message("user", user_input)
            db.save_chat_message("assistant", response_text)
            check_and_trigger_memory_extraction()
            return response_text
            
        tool_name = tool_call.get("tool")
        args = tool_call.get("args", {})
        
        # Executa a ferramenta de forma não-interativa (allow_interactive=False)
        tool_result = execute_tool_by_name(tool_name, args, silent_console, allow_interactive=False)
        
        # Alimenta o contexto com a execução e o resultado para a próxima iteração do modelo
        messages.append({"role": "assistant", "content": response_text})
        messages.append({"role": "user", "content": f"Resultado da ferramenta {tool_name}:\n{tool_result}"})
        
    return "Erro: O agente excedeu o limite de pensamentos (loop de ferramentas)."

def run_silent_memory_extraction(user_name: str) -> None:
    """Analisa o histórico recente e atualiza o perfil do usuário em segundo plano."""
    try:
        import json
        import logging
        from meu_agente_cli import db, llm
        
        # 1. Carrega as últimas 20 mensagens (10 turnos)
        history = db.get_chat_history(limit=20)
        if not history:
            return
            
        # Formata o histórico para o prompt
        history_text = []
        # get_chat_history retorna em ordem DESC (mais recente primeiro).
        # Vamos inverter para apresentar na ordem correta da conversa
        for sender, msg in reversed(history):
            history_text.append(f"{sender.capitalize()}: {msg}")
        history_formatted = "\n".join(history_text)
        
        # 2. Carrega o perfil atual
        profile_data = db.get_user_profile(user_name=user_name)
        profile_formatted = json.dumps(profile_data, indent=2, ensure_ascii=False) if profile_data else "Vazio"
        
        # 3. Monta as mensagens para o LLM
        system_prompt = (
            "Você é um analisador de perfil silencioso em segundo plano para o assistente 'Meu Agente'.\n"
            "Seu objetivo é analisar o histórico de conversas e extrair informações novas ou atualizar as existentes sobre o usuário.\n"
            "Categorias válidas: 'familiar', 'profissional', 'academico', 'preferencias', 'rotina', 'desejos', 'politica'.\n\n"
            "Instruções cruciais:\n"
            "1. Para cada categoria onde houver novidades, retorne a descrição completa consolidada (mesclando fatos antigos com os novos, mantendo o texto coeso).\n"
            "2. Não altere categorias que não foram mencionadas ou atualizadas na conversa recente.\n"
            "3. Responda APENAS com um objeto JSON válido no formato {\"categoria\": \"novo conteúdo consolidado\"}.\n"
            "4. Se nenhuma categoria precisar de atualização ou nenhuma informação relevante for encontrada, responda EXCLUSIVAMENTE com um JSON vazio: {}.\n"
            "5. Não adicione nenhuma introdução, explicação ou bloco markdown de código (como ```json). Apenas o JSON puro."
        )
        
        user_prompt = (
            f"=== PERFIL ATUAL DO USUÁRIO ===\n{profile_formatted}\n\n"
            f"=== CONVERSA RECENTE ===\n{history_formatted}\n\n"
            f"Analise a conversa recente e forneça o JSON de atualização:"
        )
        
        model = db.get_setting("active_model", "google/gemma-4-31b-qat")
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # Chamada síncrona com o LLM
        response = llm.chat_completion(model, messages, stream=False)
        
        # Tenta parsear a resposta do LLM
        response_clean = response.strip()
        if response_clean.startswith("```"):
            lines = response_clean.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            response_clean = "\n".join(lines).strip()
            
        if response_clean.startswith("json"):
            response_clean = response_clean[4:].strip()
            
        if not response_clean or response_clean == "{}":
            return
            
        updates = json.loads(response_clean)
        if isinstance(updates, dict):
            valid_categories = {'familiar', 'profissional', 'academico', 'preferencias', 'rotina', 'desejos', 'politica'}
            for cat, content in updates.items():
                cat_lower = cat.lower().strip()
                if cat_lower in valid_categories and content:
                    db.save_user_profile(cat_lower, str(content), user_name)
                    logging.info(f"[MEMÓRIA SILENCIOSA] Categoria '{cat_lower}' atualizada para o usuário '{user_name}' em segundo plano.")
                    
    except Exception as e:
        import logging
        logging.error(f"[ERROR] Falha na extração silenciosa de memória: {e}")

def check_and_trigger_memory_extraction() -> None:
    """Verifica a contagem de mensagens do usuário e dispara a extração em segundo plano se for múltiplo de 5."""
    import threading
    try:
        logged_user = db.get_logged_in_user()
        if not logged_user:
            return
            
        # Conta mensagens do tipo 'user'
        conn = db.get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM chat_history WHERE sender = 'user'")
            user_msg_count = cur.fetchone()[0]
        conn.close()
        
        # Dispara a cada 5 interações
        if user_msg_count > 0 and user_msg_count % 5 == 0:
            threading.Thread(
                target=run_silent_memory_extraction,
                args=(logged_user,),
                daemon=True
            ).start()
    except Exception as e:
        import logging
        logging.error(f"Erro ao verificar gatilho de memória silenciosa: {e}")
