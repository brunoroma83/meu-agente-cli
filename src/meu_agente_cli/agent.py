import getpass
import json
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
            break
            
        # Trata chamada de ferramenta
        if not tool_call:
            # Se falhou no parser de JSON mas parecia tool, exibe como texto
            console.print(response_text)
            db.save_chat_message("user", user_input)
            db.save_chat_message("assistant", response_text)
            console.print()
            break
            
        tool_name = tool_call.get("tool")
        args = tool_call.get("args", {})
        
        # Exibe progresso da ferramenta
        console.print(f"\n[bold blue][Tool][/bold blue] Chamando: [yellow]{tool_name}[/yellow]...")
        
        # Executa a ferramenta
        tool_result = ""
        try:
            if tool_name == "get_weather":
                tool_result = tools.get_weather(**args)
            elif tool_name == "get_financial_quote":
                tool_result = tools.get_financial_quote(**args)
            elif tool_name == "get_news":
                tool_result = tools.get_news(**args)
            elif tool_name == "finance_tool":
                tool_result = tools.finance_tool(**args)
            elif tool_name == "notes_tool":
                tool_result = tools.notes_tool(**args)
            elif tool_name == "execute_cli_command":
                cmd = args.get("command", "")
                tool_result = handle_cli_tool_execution(cmd, console)
            elif tool_name == "calculator_tool":
                tool_result = tools.calculator_tool(**args)
            else:
                tool_result = f"Erro: Ferramenta '{tool_name}' não suportada."
        except Exception as e:
            tool_result = f"Erro na execução da ferramenta '{tool_name}': {str(e)}"
            
        console.print(f"[bold blue][Tool Resultado][/bold blue] Finalizado.")
        
        # Alimenta o histórico do LLM para a próxima rodada
        messages.append({"role": "assistant", "content": response_text})
        messages.append({"role": "user", "content": f"Resultado da ferramenta {tool_name}:\n{tool_result}"})
    else:
        console.print("[red]Erro: Limite de iterações excedido pelo agente (loop de ferramentas).[/red]")
