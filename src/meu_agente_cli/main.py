import sys
import getpass
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt, Confirm

import meu_agente_cli.db as db
import meu_agente_cli.llm as llm
import meu_agente_cli.config as config
import meu_agente_cli.security as security
import meu_agente_cli.scheduler as scheduler
import meu_agente_cli.agent as agent

console = Console()

def print_banner():
    banner_text = Text()
    banner_text.append("=== MEU AGENTE CLI ===\n", style="bold cyan")
    banner_text.append("Seu assistente pessoal e financeiro inteligente no WSL\n", style="italic gray")
    banner_text.append("-----------------------------------------------------", style="blue")
    
    console.print(Panel(
        banner_text,
        border_style="cyan",
        title="[bold green]Inicializando...[/bold green]",
        expand=False
    ))

def initialize_components() -> bool:
    """Inicializa banco de dados, testa LM Studio e configura senha."""
    # 1. Inicializa banco de dados
    if not db.init_database():
        console.print("[bold red][FALHA][/bold red] Não foi possível conectar ou configurar o PostgreSQL no WSL.")
        return False
        
    # 2. Configura senha de segurança se não existir
    if not security.has_password_configured():
        console.print(Panel(
            "[bold yellow]Configuração de Segurança Inicial:[/bold yellow]\n"
            "Como o agente pode executar comandos de terminal, você deve configurar uma senha de segurança.\n"
            "Essa senha será solicitada para rodar comandos não-seguros ou entrar em Modo Não-Seguro.",
            border_style="yellow"
        ))
        while True:
            p1 = getpass.getpass("Digite a nova Senha de Segurança: ")
            if not p1:
                console.print("[red]A senha não pode ser vazia.[/red]")
                continue
            p2 = getpass.getpass("Confirme a Senha de Segurança: ")
            if p1 == p2:
                if security.configure_password(p1):
                    console.print("[bold green][SUCESSO][/bold green] Senha de segurança configurada!")
                    break
                else:
                    console.print("[red]Erro ao salvar senha no banco de dados. Tentando novamente...[/red]")
            else:
                console.print("[red]As senhas não coincidem. Tente novamente.[/red]")

    # 3. Verifica conexão com LM Studio
    console.print("[blue]Testando conexão com o LM Studio...[/blue]")
    connected_lm = llm.test_lm_studio_connection()
    
    if not connected_lm:
        # Tenta re-detectar IP do WSL
        host_ip = config.get_wsl_host_ip()
        console.print(f"[yellow]Conexão padrão falhou. Tentando auto-detectar Host Windows (IP: {host_ip})...[/yellow]")
        
        # Salva o IP detectado temporariamente e tenta de novo
        current_config = config.load_bootstrap_config()
        current_config["lm_studio_host"] = host_ip
        config.save_bootstrap_config(current_config)
        connected_lm = llm.test_lm_studio_connection()
        
    while not connected_lm:
        console.print(Panel(
            f"[bold red]Erro de Conexão com LM Studio:[/bold red]\n"
            f"Não foi possível conectar ao LM Studio em [cyan]{config.get_lm_studio_url()}[/cyan].\n\n"
            f"Certifique-se de que:\n"
            f"1. O LM Studio está rodando no Windows host.\n"
            f"2. O servidor do LM Studio está INICIADO na porta 1234.\n"
            f"3. O CORS está habilitado nas configurações do LM Studio.\n"
            f"4. A firewall do Windows permite conexões na porta do LM Studio.",
            title="Erro de LM Studio",
            border_style="red"
        ))
        
        # Pergunta se o usuário deseja configurar manualmente ou continuar offline
        option = Prompt.ask(
            "O que deseja fazer?\n"
            "[green][1][/green] Digitar o IP/Host do LM Studio manualmente\n"
            "[yellow][2][/yellow] Tentar reconectar com o IP atual\n"
            "[red][3][/red] Continuar offline (sem LLM)\n"
            "Escolha",
            choices=["1", "2", "3"],
            default="1"
        )
        
        if option == "1":
            custom_host = Prompt.ask("Digite o IP/Host do LM Studio (ex: 192.168.1.5 ou localhost)").strip()
            custom_port_str = Prompt.ask("Digite a porta do LM Studio", default="1234").strip()
            custom_port = int(custom_port_str) if custom_port_str.isdigit() else 1234
            
            # Atualiza o arquivo de configuração
            current_config = config.load_bootstrap_config()
            current_config["lm_studio_host"] = custom_host
            current_config["lm_studio_port"] = custom_port
            config.save_bootstrap_config(current_config)
            
            console.print(f"[blue]Testando conexão em {config.get_lm_studio_url()}...[/blue]")
            connected_lm = llm.test_lm_studio_connection()
            if connected_lm:
                console.print(f"[bold green][SUCESSO][/bold green] Conectado e configurado com sucesso!")
                break
        elif option == "2":
            console.print(f"[blue]Testando conexão novamente em {config.get_lm_studio_url()}...[/blue]")
            connected_lm = llm.test_lm_studio_connection()
        else: # Option "3"
            console.print("[yellow]Continuando em modo offline. Algumas ferramentas de chat inteligente não funcionarão.[/yellow]")
            break
    else:
        console.print(f"[bold green][SUCESSO][/bold green] Conectado ao LM Studio em [cyan]{config.get_lm_studio_url()}[/cyan]!")

    # 4. Configura modelo padrão caso não exista
    active_model = db.get_setting("active_model")
    if not active_model:
        models = llm.get_available_models()
        if models:
            db.set_setting("active_model", models[0])
            console.print(f"[info] Modelo ativo padrão definido para: [bold cyan]{models[0]}[/bold cyan]")
        else:
            db.set_setting("active_model", "google/gemma-4-31b-qat")
            
    return True

def handle_slash_command(cmd_input: str) -> bool:
    """
    Processa os comandos com barra. Retorna True se o loop principal deve continuar,
    ou False se o agente deve encerrar.
    """
    parts = cmd_input.split()
    command = parts[0].lower()
    
    if command in ("/exit", "/quit"):
        console.print("[bold yellow]Parando subagentes e encerrando o Meu Agente CLI. Até mais![/bold yellow]")
        scheduler.stop_scheduler()
        return False
        
    elif command == "/help":
        console.print(Panel(
            "[bold cyan]Comandos Disponíveis:[/bold cyan]\n"
            "- [green]/help[/green]: Mostra esta lista de ajuda.\n"
            "- [green]/status[/green]: Mostra conexões e estado atual de segurança.\n"
            "- [green]/clear[/green]: Limpa o histórico de conversa (reseta o contexto do agente).\n"
            "- [green]/history <limite>[/green]: Exibe ou altera a quantidade de mensagens enviadas no histórico (contexto recente) ao LLM.\n"
            "- [green]/models[/green]: Lista os modelos disponíveis no LM Studio e permite trocar.\n"
            "- [green]/safe[/green]: Ativa o Modo Seguro (execução apenas de comandos permitidos).\n"
            "- [green]/unsafe[/green]: Desativa o Modo Seguro (requer senha de segurança).\n"
            "- [green]/notes[/green]: Gerencia notas (lista tudo).\n"
            "- [green]/finance[/green]: Mostra finanças do mês atual. Filtros: [green]/finance next[/green], [green]/finance deleted[/green] (inativos), [green]/finance restore <ID>[/green] (reativar), [green]/finance all[/green], [green]/finance mes=MM-YYYY[/green], [green]/finance q=busca[/green], [green]/finance delete <ID>[/green].\n"
            "- [green]/cron[/green]: Gerencia cronjobs (lista tudo). Use [green]/cron add <nome> <cron_expr> <prompt>[/green] para agendar.\n"
            "- [green]/exit[/green] ou [green]/quit[/green]: Encerra o assistente.",
            title="Ajuda do Meu Agente CLI"
        ))
        
    elif command == "/clear":
        if db.clear_chat_history():
            console.print("[bold green]Histórico de conversa limpo com sucesso![/bold green] O contexto do agente foi resetado.")
        else:
            console.print("[bold red]Erro ao tentar limpar o histórico de conversa do banco de dados.[/bold red]")
            
    elif command == "/history":
        if len(parts) > 1:
            val_str = parts[1]
            if val_str.isdigit():
                val = int(val_str)
                if val >= 1:
                    db.set_chat_history_limit(val)
                    console.print(f"[bold green][SUCESSO][/bold green] Limite do histórico de chat configurado para [yellow]{val}[/yellow] mensagens.")
                else:
                    console.print("[red]O limite deve ser de pelo menos 1 mensagem.[/red]")
            else:
                console.print("[red]Uso correto: /history <limite_inteiro> (ex: /history 4)[/red]")
        else:
            current_limit = db.get_chat_history_limit()
            console.print(f"O limite atual de histórico enviado ao LLM é de [yellow]{current_limit}[/yellow] mensagens.")
        
    elif command == "/status":
        safe_str = "[bold green]SEGURO[/bold green]" if security.is_safe_mode() else "[bold red]NÃO-SEGURO[/bold red]"
        lm_url = config.get_lm_studio_url()
        active_model = db.get_setting("active_model", "Nenhum")
        
        console.print(f"[bold cyan]Status do Sistema:[/bold cyan]")
        console.print(f"- Modo de Segurança: {safe_str}")
        console.print(f"- LM Studio URL: [cyan]{lm_url}[/cyan]")
        console.print(f"- Modelo Ativo: [yellow]{active_model}[/yellow]")
        
        # Teste rápido de conexão
        conn = llm.test_lm_studio_connection()
        conn_str = "[bold green]Conectado[/bold green]" if conn else "[bold red]Desconectado[/bold red]"
        console.print(f"- LM Studio Status: {conn_str}")
        
    elif command == "/models":
        models = llm.get_available_models()
        if not models:
            console.print("[red]Nenhum modelo detectado no LM Studio. Certifique-se de que carregou um modelo na interface do LM Studio.[/red]")
            return True
            
        active = db.get_setting("active_model")
        
        table = Table(title="Modelos Disponíveis no LM Studio")
        table.add_column("Índice", justify="center", style="cyan")
        table.add_column("Nome do Modelo", style="magenta")
        table.add_column("Status", justify="center", style="green")
        
        for idx, m in enumerate(models, 1):
            status = "[bold green]Ativo[/bold green]" if m == active else ""
            table.add_row(str(idx), m, status)
            
        console.print(table)
        
        selection = Prompt.ask("Digite o índice do modelo que deseja ativar (ou pressione Enter para manter o atual)", default="")
        if selection.isdigit():
            idx = int(selection) - 1
            if 0 <= idx < len(models):
                db.set_setting("active_model", models[idx])
                console.print(f"[bold green]Modelo ativo alterado para:[/bold green] {models[idx]}")
            else:
                console.print("[red]Índice inválido.[/red]")
                
    elif command == "/safe":
        security.set_safe_mode(True)
        console.print("[bold green]Modo Seguro ATIVADO.[/bold green] Comandos CLI não autorizados serão bloqueados.")
        
    elif command == "/unsafe":
        if len(parts) > 1 and parts[1].lower() == "sign":
            password = getpass.getpass("Digite sua Senha de Segurança para assinar safe_commands.json: ")
            if security.sign_safe_commands_file(password):
                console.print("[bold green][SUCESSO][/bold green] Arquivo [yellow]safe_commands.json[/yellow] assinado com sucesso! Whitelist criptografada atualizada.")
            else:
                console.print("[bold red][ERRO][/bold red] Falha ao assinar. Senha incorreta ou chaves inválidas.")
            return True
            
        if not security.is_safe_mode():
            console.print("[yellow]O agente já está em Modo Não-Seguro.[/yellow]")
            return True
            
        password = getpass.getpass("Digite a Senha de Segurança: ")
        if security.verify_password(password):
            security.set_safe_mode(False)
            console.print("[bold red]Modo Seguro DESATIVADO.[/bold red] Atenção: comandos de terminal agora podem ser executados com sua aprovação.")
        else:
            console.print("[bold red]Senha incorreta! Permissão negada.[/bold red]")
            
    elif command == "/notes":
        notes = db.list_all_user_notes()
        if not notes:
            console.print("[yellow]Nenhuma anotação salva.[/yellow]")
            return True
            
        table = Table(title="Notas Salvas (Memória de Longo Prazo)")
        table.add_column("ID", justify="center", style="cyan")
        table.add_column("Criado em", style="blue")
        table.add_column("Conteúdo", style="white")
        
        for nid, content, dt in notes:
            # Limita tamanho para exibição na tabela
            display_content = content[:80] + "..." if len(content) > 80 else content
            table.add_row(str(nid), dt.strftime("%d/%m/%Y %H:%M"), display_content)
            
        console.print(table)
        console.print("[dim]Use o chat normal para perguntar sobre suas notas ou pesquisar por elas.[/dim]")
        
    elif command == "/finance":
        # Permite deleção via comando: /finance delete <id>
        if len(parts) > 1 and parts[1].lower() in ["delete", "remove", "del", "rm"]:
            if len(parts) < 3 or not parts[2].isdigit():
                console.print("[red]Uso correto: /finance delete <ID>[/red]")
                return True
            rid = int(parts[2])
            if db.delete_financial_record(rid):
                console.print(f"[bold green]Lançamento #{rid} inativado com sucesso![/bold green]")
            else:
                console.print(f"[bold red]Falha ao inativar lançamento #{rid}. Verifique se o ID existe.[/bold red]")
            return True
            
        # Permite restauração via comando: /finance restore <id>
        if len(parts) > 1 and parts[1].lower() in ["restore", "reactivate", "undel"]:
            if len(parts) < 3 or not parts[2].isdigit():
                console.print("[red]Uso correto: /finance restore <ID>[/red]")
                return True
            rid = int(parts[2])
            if db.restore_financial_record(rid):
                console.print(f"[bold green]Lançamento #{rid} restaurado e ativo novamente![/bold green]")
            else:
                console.print(f"[bold red]Falha ao restaurar lançamento #{rid}. Verifique se o ID existe.[/bold red]")
            return True
            
        # Filtros e visualização
        from datetime import datetime
        now = datetime.now()
        current_month_str = now.strftime("%m-%Y")
        
        # Calcula o próximo mês
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
        
        # Se não houver filtros, assume o mês atual
        if len(parts) == 1:
            month_year = current_month_str
            table_title = f"Transações do Mês Atual ({current_month_str})"
        else:
            arg = parts[1]
            if arg.lower() == "all":
                table_title = "Todas as Transações"
            elif arg.lower() == "next":
                month_year = next_month_str
                table_title = f"Transações do Próximo Mês ({next_month_str})"
            elif arg.lower() == "deleted":
                show_deleted = True
                table_title = "Transações Inativas (Deletadas Logicamente)"
            elif arg.lower().startswith("mes="):
                month_year = arg.split("=")[1]
                table_title = f"Transações com Vencimento em {month_year}"
            elif arg.lower().startswith("q="):
                query = arg.split("=")[1]
                table_title = f"Busca de Transações por '{query}'"
            else:
                console.print("[red]Filtro inválido. Use /finance, /finance next, /finance deleted, /finance restore <ID>, /finance all, /finance mes=MM-YYYY ou /finance q=busca[/red]")
                return True
                
        if show_deleted:
            records = db.get_deleted_financial_records()
        else:
            records = db.search_financial_records(limit=limit, month_year=month_year, query=query)
        
        # Calcula somatório dinâmico com base apenas nos registros filtrados/exibidos
        sum_receitas = 0.0
        sum_despesas = 0.0
        for _, rtype, _, val, _, _, _ in records:
            if rtype == "receita":
                sum_receitas += val
            elif rtype == "despesa":
                sum_despesas += val
        sum_saldo = sum_receitas - sum_despesas
        
        # Mostra o resumo geral dos dados filtrados
        console.print(Panel(
            f"[bold green]Receitas:[/bold green] R$ {sum_receitas:.2f}  |  "
            f"[bold red]Despesas:[/bold red] R$ {sum_despesas:.2f}  |  "
            f"[bold cyan]Saldo do Filtro:[/bold cyan] R$ {sum_saldo:.2f}",
            title="Resumo Financeiro (Filtrado)",
            expand=False
        ))
        
        if records:
            table = Table(title=table_title)
            table.add_column("ID", justify="center", style="cyan")
            table.add_column("Lançamento", style="blue")
            table.add_column("Vencimento", style="yellow")
            table.add_column("Tipo", style="magenta")
            table.add_column("Categoria", style="yellow")
            table.add_column("Valor", justify="right", style="green")
            table.add_column("Descrição", style="white")
            
            for rid, rtype, cat, val, desc, dt, due_dt in records:
                type_style = "[bold green]Receita[/bold green]" if rtype == "receita" else "[bold red]Despesa[/bold red]"
                val_str = f"R$ {val:.2f}"
                due_str = due_dt.strftime("%d/%m/%Y") if due_dt else "N/A"
                table.add_row(str(rid), dt.strftime("%d/%m/%Y"), due_str, type_style, cat, val_str, desc or "")
                
            console.print(table)
        else:
            console.print("[yellow]Nenhuma transação encontrada com os filtros especificados.[/yellow]")
            
    elif command == "/cron":
        # Verifica se é uma adição
        if len(parts) > 1 and parts[1].lower() == "add":
            # Formato: /cron add "nome" "cron" "prompt"
            # Precisamos juntar o resto da string e extrair parâmetros de forma inteligente ou via prompts adicionais
            name = Prompt.ask("Digite o nome da tarefa")
            cron_expr = Prompt.ask("Digite a expressão Cron (ex: '*/5 * * * *' para cada 5 min)", default="0 9 * * *")
            task_prompt = Prompt.ask("Digite a instrução para o Subagente executar")
            
            success = db.add_cron_job(name, cron_expr, task_prompt)
            if success:
                console.print(f"[bold green][SUCESSO][/bold green] Subagente '{name}' agendado com sucesso ({cron_expr})!")
            else:
                console.print("[red]Erro ao criar agendamento cronjob. Verifique a expressão cron.[/red]")
        elif len(parts) > 1 and parts[1].lower() == "delete":
            job_id = Prompt.ask("Digite o ID do cronjob que deseja excluir")
            if job_id.isdigit():
                if db.delete_cron_job(int(job_id)):
                    console.print(f"[bold green][SUCESSO][/bold green] Cronjob #{job_id} removido.")
                else:
                    console.print(f"[red]Não foi possível remover o cronjob #{job_id}.[/red]")
        else:
            jobs = db.get_active_cron_jobs()
            if not jobs:
                console.print("[yellow]Nenhuma tarefa agendada (cronjob) ativa. Use [green]/cron add[/green] para agendar.[/yellow]")
                return True
                
            table = Table(title="Subagentes Agendados (Cron Jobs)")
            table.add_column("ID", justify="center", style="cyan")
            table.add_column("Nome", style="magenta")
            table.add_column("Expressão Cron", style="yellow")
            table.add_column("Próximo Disparo", style="blue")
            table.add_column("Último Disparo", style="blue")
            table.add_column("Prompt de Instrução", style="white")
            
            for j in jobs:
                last_run_str = j["last_run"].strftime("%d/%m/%Y %H:%M:%S") if j["last_run"] else "Nunca"
                next_run_str = j["next_run"].strftime("%d/%m/%Y %H:%M:%S") if j["next_run"] else "N/A"
                # Limita prompt na tabela
                p_desc = j["task_prompt"][:40] + "..." if len(j["task_prompt"]) > 40 else j["task_prompt"]
                table.add_row(str(j["id"]), j["name"], j["cron_expression"], next_run_str, last_run_str, p_desc)
                
            console.print(table)
            console.print("[dim]Use [green]/cron add[/green] para agendar novo subagente ou [green]/cron delete[/green] para remover.[/dim]")
            
    else:
        console.print(f"[bold red]Comando inválido:[/bold red] {command}. Digite [green]/help[/green] para ver comandos válidos.")
        
    return True

def main():
    print_banner()
    
    # Inicializa todos os componentes
    if not initialize_components():
        console.print("[bold red]Falha na inicialização crítica. Encerrando.[/bold red]")
        sys.exit(1)
        
    # Inicializa o scheduler de subagentes/cronjobs em segundo plano
    scheduler.start_scheduler()
    console.print("[bold green][INFO][/bold green] Agendador de tarefas em segundo plano iniciado.")
    console.print("Pronto para uso! Digite [bold green]/help[/bold green] para listar os comandos especiais.\n")
    
    # Loop interativo
    while True:
        try:
            # Leitura do input com Rich Prompt para ficar elegante
            user_input = Prompt.ask("\n[bold green]Você[/bold green]").strip()
            user_input = config.clean_string(user_input)
            if not user_input:
                continue
                
            # Verifica se é comando de barra
            if user_input.startswith("/"):
                should_continue = handle_slash_command(user_input)
                if not should_continue:
                    break
            else:
                # Turno normal de conversa com o agente
                agent.process_agent_turn(user_input, console)
                
        except KeyboardInterrupt:
            # Captura Ctrl+C de forma amigável
            console.print("\n[bold yellow]Ctrl+C detectado. Para sair, use /exit ou Ctrl+D.[/bold yellow]")
        except EOFError:
            # Captura Ctrl+D
            console.print()
            handle_slash_command("/exit")
            break
        except Exception as e:
            console.print(f"\n[bold red]Erro crítico no loop de chat: {e}[/bold red]")

if __name__ == "__main__":
    main()
