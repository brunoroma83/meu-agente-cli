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
import logging
import meu_agente_cli.logger as logger

console = Console()

def print_banner():
    banner_text = Text()
    banner_text.append("MEU AGENTE CLI - Assistente Pessoal & Financeiro Inteligente\n", style="bold cyan")
    banner_text.append("Seu assistente pessoal de produtividade e finanças no WSL/Docker\n\n", style="italic gray")
    banner_text.append("Versão: v1.1.0\n", style="bold white")
    banner_text.append("Última Atualização: 08/08/2026\n\n", style="bold white")
    banner_text.append("Principais Funcionalidades:\n", style="bold yellow")
    banner_text.append(" - 🛡️ Whitelist de Segurança Criptográfica com Assinatura RSA\n", style="green")
    banner_text.append(" - 💰 Finanças Pessoais com Soft Delete, Histórico e Projeções\n", style="green")
    banner_text.append(" - 📥 Importador de CSV Financeiro com Mapeamento Inteligente\n", style="green")
    banner_text.append(" - 🔐 Backup e Restauração Criptografados (AES-256 e Gzip)\n", style="green")
    banner_text.append(" - 📝 Notas Persistentes de Longo Prazo no PostgreSQL\n", style="green")
    banner_text.append(" - 🤖 Agendador de Subagentes em Segundo Plano (Cron Jobs)\n", style="green")
    banner_text.append(" - 🧮 Calculadora Avançada em Lote e Hot reload de Plugins\n", style="green")
    banner_text.append(" - 🌤️ Integrações Nativas de Clima, Ações Financeiras e Notícias RSS\n", style="green")
    banner_text.append("---------------------------------------------------------------", style="blue")
    
    console.print(Panel(
        banner_text,
        border_style="cyan",
        title="[bold green]MEU AGENTE CLI[/bold green]",
        expand=False
    ))

def initialize_components() -> bool:
    """Inicializa banco de dados, testa LM Studio e configura senha."""
    # Inicializa logging
    logger.setup_logging()
    logging.info("Inicializando componentes do Meu Agente CLI...")

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

def handle_finance_csv_import(console: Console) -> None:
    """Importa receitas e despesas a partir de uploads/finance.csv com mapeamento dinâmico."""
    import os
    import csv
    import shutil
    import unicodedata
    from datetime import datetime
    from typing import Optional
    
    # Define caminhos
    base_dir = os.getcwd()
    uploads_dir = os.path.join(base_dir, "uploads")
    archive_dir = os.path.join(uploads_dir, "archive")
    csv_file = os.path.join(uploads_dir, "finance.csv")
    
    # Garante existência dos diretórios
    os.makedirs(archive_dir, exist_ok=True)
    
    # 1. Se o arquivo não existir, exibe guia de formatação
    if not os.path.exists(csv_file):
        from rich.panel import Panel
        from rich.table import Table
        
        guide_table = Table(title="Colunas Aceitas e Mapeamento Automático", expand=True)
        guide_table.add_column("Campo no BD", style="cyan", width=15)
        guide_table.add_column("Colunas Mapeadas (Qualquer uma)", style="magenta")
        guide_table.add_column("Tipo/Regras", style="white")
        
        guide_table.add_row("type", "tipo, type, tiporegistro, transacao", "receita OU despesa (padrão: despesa)")
        guide_table.add_row("category", "categoria, category, grupo, classificacao", "Obrigatório. Ex: Alimentação, Salário")
        guide_table.add_row("amount", "valor, val, amount, preco, custo, total", "Obrigatório. Ex: 150.50 ou 1.500,00")
        guide_table.add_row("description", "descricao, description, detalhes, obs", "Opcional. Texto descritivo")
        guide_table.add_row("due_date", "vencimento, due_date, datavencimento, venc", "Opcional. Formatos: DD/MM/YYYY ou YYYY-MM-DD")
        
        console.print(Panel(
            "[bold red][ERRO][/bold red] Arquivo não encontrado em [cyan]uploads/finance.csv[/cyan]!\n\n"
            "[bold green]Para realizar a importação de lançamentos via CSV, siga este guia:[/bold green]\n"
            "1. Crie a pasta [yellow]uploads/[/yellow] na raiz do projeto (se não existir).\n"
            "2. Coloque nela o seu arquivo com o nome exato [yellow]finance.csv[/yellow].\n"
            "3. O arquivo deve ter uma linha de cabeçalho com os nomes das colunas correspondentes.\n\n"
            "O agente classificará e mapeará as colunas dinamicamente baseado nos nomes delas.\n",
            title="Importador Financeiro de CSV",
            border_style="yellow"
        ))
        console.print(guide_table)
        console.print(
            "\n[bold green]Exemplo de Conteúdo Ideal (finance.csv):[/bold green]\n"
            "[dim]"
            "tipo,categoria,valor,descricao,vencimento\n"
            "receita,Salário,5500.00,Salário Mensal,05/08/2026\n"
            "despesa,Aluguel,1200.00,Aluguel do Ap,10/08/2026\n"
            "despesa,Supermercado,450.50,,12/08/2026\n"
            "[/dim]"
        )
        return

    # 2. Se o arquivo existir, processa
    console.print(f"[info] Arquivo [cyan]uploads/finance.csv[/cyan] localizado. Iniciando análise de colunas...[/info]")
    
    # Helpers locais para normalização
    def normalize(s: str) -> str:
        s_clean = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('ASCII')
        return s_clean.strip().lower().replace("_", "").replace(" ", "").replace("-", "")
        
    def parse_amount(val_str: str) -> float:
        val_clean = val_str.replace("R$", "").replace(" ", "").strip()
        if "," in val_clean:
            if "." in val_clean:
                val_clean = val_clean.replace(".", "").replace(",", ".")
            else:
                val_clean = val_clean.replace(",", ".")
        return float(val_clean)
        
    def parse_due_date(date_str: str) -> Optional[str]:
        if not date_str.strip():
            return None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None

    # Mapeamentos esperados
    TYPE_KEYS = {"tipo", "type", "tiporegistro", "transacao", "categoriatipo"}
    CATEGORY_KEYS = {"categoria", "category", "grupo", "classificacao"}
    AMOUNT_KEYS = {"valor", "val", "amount", "preco", "custo", "total"}
    DESC_KEYS = {"descricao", "description", "detalhes", "obs", "observacao"}
    DUE_KEYS = {"vencimento", "due_date", "datavencimento", "datavenc", "venc", "due"}

    items_to_import = []
    
    try:
        with open(csv_file, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader, None)
            
            if not headers:
                console.print("[bold red][ERRO][/bold red] O arquivo CSV está vazio.")
                return
                
            # Classificação/Mapeamento das Colunas
            col_map = {}
            for idx, h in enumerate(headers):
                h_norm = normalize(h)
                if h_norm in TYPE_KEYS:
                    col_map["type"] = idx
                elif h_norm in CATEGORY_KEYS:
                    col_map["category"] = idx
                elif h_norm in AMOUNT_KEYS:
                    col_map["amount"] = idx
                elif h_norm in DESC_KEYS:
                    col_map["description"] = idx
                elif h_norm in DUE_KEYS:
                    col_map["due_date"] = idx
            
            # Valida colunas obrigatórias
            if "category" not in col_map or "amount" not in col_map:
                console.print(
                    "[bold red][ERRO][/bold red] Não foi possível mapear automaticamente as colunas obrigatórias!\n"
                    f"Colunas lidas: {headers}\n"
                    f"Mapeamento obtido: {list(col_map.keys())}\n"
                    "Certifique-se de que o cabeçalho tem pelo menos colunas equivalentes a 'categoria' e 'valor'."
                )
                return
                
            console.print(f"[bold green][OK][/bold green] Mapeamento de colunas bem-sucedido: {list(col_map.keys())}")
            
            # Processamento das linhas
            row_num = 1
            for row in reader:
                row_num += 1
                if not row or not any(cell.strip() for cell in row):
                    continue  # Pula linhas vazias
                    
                # Preenche valores
                try:
                    category = row[col_map["category"]].strip()
                    amount_raw = row[col_map["amount"]].strip()
                    if not category or not amount_raw:
                        console.print(f"[warning] Linha {row_num}: Ignorada por falta de categoria ou valor.[/warning]")
                        continue
                        
                    amount = parse_amount(amount_raw)
                    
                    # Tipo
                    r_type = "despesa"
                    if "type" in col_map:
                        type_val = normalize(row[col_map["type"]])
                        if "receita" in type_val or "income" in type_val or "entrada" in type_val:
                            r_type = "receita"
                    else:
                        # Fallback inteligente: se for valor negativo, assume despesa
                        if amount < 0:
                            r_type = "despesa"
                            amount = abs(amount)
                    
                    # Descrição
                    description = ""
                    if "description" in col_map:
                        description = row[col_map["description"]].strip()
                        
                    # Data de vencimento
                    due_date = None
                    if "due_date" in col_map:
                        due_date = parse_due_date(row[col_map["due_date"]])
                        
                    items_to_import.append({
                        "type": r_type,
                        "category": category,
                        "amount": amount,
                        "description": description,
                        "due_date": due_date
                    })
                except Exception as row_ex:
                    console.print(f"[warning] Linha {row_num}: Erro ao processar dados ({row_ex}). Ignorada.[/warning]")
                    
        # 3. Faz a inserção em lote no BD
        if not items_to_import:
            console.print("[yellow][Aviso][/yellow] Nenhum lançamento válido foi encontrado no arquivo CSV para importação.")
            return
            
        success = db.add_financial_records_bulk(items_to_import)
        if success:
            num_rec = sum(1 for x in items_to_import if x["type"] == "receita")
            num_des = sum(1 for x in items_to_import if x["type"] == "despesa")
            
            console.print(
                f"[bold green][SUCESSO][/bold green] Importação concluída!\n"
                f"- Total de lançamentos importados: [yellow]{len(items_to_import)}[/yellow]\n"
                f"- Receitas: {num_rec} | Despesas: {num_des}"
            )
            
            # 4. Move o arquivo para pasta de arquivos importados (archive)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_file = os.path.join(archive_dir, f"finance_imported_{timestamp}.csv")
            try:
                shutil.move(csv_file, archive_file)
                console.print(f"[info] Arquivo CSV original movido para [cyan]uploads/archive/finance_imported_{timestamp}.csv[/cyan].[/info]")
            except Exception as move_ex:
                logging.warning("Não foi possível mover o arquivo importado para a pasta de arquivos arquivados: %s", move_ex)
                console.print(f"[warning] Não foi possível mover o arquivo importado para a pasta de arquivos arquivados: {move_ex}[/warning]")
        else:
            logging.error("Falha ao registrar lançamentos de lote no banco de dados.")
            console.print("[bold red][ERRO][/bold red] Falha ao registrar lançamentos de lote no banco de dados.")
            
    except Exception as e:
        logging.exception("Falha crítica ao ler o arquivo CSV")
        console.print(f"[bold red][ERRO][/bold red] Falha crítica ao ler o arquivo CSV: {e}")

def handle_finance_card_command(parts: list, console: Console) -> None:
    """Processa subcomandos relacionados a cartões de crédito."""
    if len(parts) < 3:
        console.print(
            "[red]Uso correto dos comandos de cartão de crédito:\n"
            "- [green]/finance card add <nome> <dia_fechamento> <dia_vencimento>[/green]\n"
            "- [green]/finance card list[/green]\n"
            "- [green]/finance card buy <cartao> <categoria> <valor_total> <parcelas> <descricao> [data_compra][/green][/red]"
        )
        return

    subcmd = parts[2].lower()
    
    if subcmd == "add":
        if len(parts) < 6:
            console.print("[red]Uso correto: /finance card add <nome> <dia_fechamento> <dia_vencimento>[/red]")
            return
        name = parts[3].strip()
        try:
            closing_day = int(parts[4])
            due_day = int(parts[5])
            if not (1 <= closing_day <= 31) or not (1 <= due_day <= 31):
                raise ValueError("Os dias devem estar entre 1 e 31.")
        except ValueError as val_ex:
            console.print(f"[red]Dias inválidos: {val_ex}. Forneça inteiros válidos de 1 a 31.[/red]")
            return
            
        if db.save_credit_card(name, closing_day, due_day):
            console.print(f"[bold green][SUCESSO][/bold green] Cartão '[yellow]{name}[/yellow]' cadastrado com sucesso! Fechamento: dia {closing_day} | Vencimento: dia {due_day}.")
        else:
            console.print("[red]Erro ao cadastrar cartão de crédito.[/red]")
            
    elif subcmd == "list":
        cards = db.get_credit_cards().get("cartoes", {})
        if not cards:
            console.print("[yellow]Nenhum cartão de crédito cadastrado. Use [green]/finance card add[/green] para cadastrar.[/yellow]")
            return
            
        table = Table(title="Cartões de Crédito Cadastrados")
        table.add_column("Nome do Cartão", style="magenta")
        table.add_column("Dia Fechamento", justify="center", style="cyan")
        table.add_column("Dia Vencimento", justify="center", style="cyan")
        
        for name, info in cards.items():
            table.add_row(name, str(info["closing_day"]), str(info["due_day"]))
            
        console.print(table)
        
    elif subcmd == "buy":
        if len(parts) < 8:
            console.print("[red]Uso correto: /finance card buy <cartao> <categoria> <valor_total> <parcelas> <descricao> [data_compra][/red]")
            return
            
        card_name = parts[3].strip()
        cards_config = db.get_credit_cards().get("cartoes", {})
        
        # Procura o cartão de forma case-insensitive
        card_info = None
        matched_card_name = ""
        for name, info in cards_config.items():
            if name.lower() == card_name.lower():
                card_info = info
                matched_card_name = name
                break
                
        if not card_info:
            console.print(f"[red]Cartão '{card_name}' não cadastrado! Use [green]/finance card list[/green] para ver os disponíveis.[/red]")
            return
            
        category = parts[4].strip()
        
        try:
            # Limpa valor total (ex: R$ 150,50 -> 150.50)
            def parse_amount(val_str: str) -> float:
                val_clean = val_str.replace("R$", "").replace(" ", "").strip()
                if "," in val_clean:
                    if "." in val_clean:
                        val_clean = val_clean.replace(".", "").replace(",", ".")
                    else:
                        val_clean = val_clean.replace(",", ".")
                return float(val_clean)
                
            total_amount = parse_amount(parts[5])
            installments = int(parts[6])
            if total_amount <= 0 or installments <= 0:
                raise ValueError("Valor e parcelas devem ser maiores que zero.")
        except ValueError as parse_ex:
            console.print(f"[red]Erro de validação: {parse_ex}. Verifique o valor e parcelas.[/red]")
            return
            
        # Pega descrição e data de compra opcional no final
        remaining = parts[7:]
        buy_date_str = None
        from datetime import datetime
        
        if len(remaining) > 1:
            possible_date = remaining[-1]
            for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
                try:
                    datetime.strptime(possible_date, fmt)
                    buy_date_str = possible_date
                    remaining = remaining[:-1]
                    break
                except ValueError:
                    continue
                    
        description = " ".join(remaining).strip()
        if not description:
            description = f"Compra no cartão {matched_card_name}"
            
        buy_date = datetime.now()
        if buy_date_str:
            for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
                try:
                    buy_date = datetime.strptime(buy_date_str, fmt)
                    break
                except ValueError:
                    pass
                    
        closing_day = card_info["closing_day"]
        due_day = card_info["due_day"]

        # 1. Determina o mês e ano do fechamento da fatura (Closing Month/Year)
        if buy_date.day >= closing_day:
            if buy_date.month == 12:
                closing_month = 1
                closing_year = buy_date.year + 1
            else:
                closing_month = buy_date.month + 1
                closing_year = buy_date.year
        else:
            closing_month = buy_date.month
            closing_year = buy_date.year

        # 2. Determina o vencimento da primeira parcela
        if due_day <= closing_day:
            if closing_month == 12:
                first_due_month = 1
                first_due_year = closing_year + 1
            else:
                first_due_month = closing_month + 1
                first_due_year = closing_year
        else:
            first_due_month = closing_month
            first_due_year = closing_year

        base_inst_val = round(total_amount / installments, 2)
        diff = round(total_amount - (base_inst_val * installments), 2)
        
        records_to_insert = []
        
        for i in range(1, installments + 1):
            inst_amount = base_inst_val
            if i == 1:
                inst_amount = round(base_inst_val + diff, 2)
                
            offset_months = i - 1
            due_month = first_due_month + offset_months
            due_year = first_due_year
            
            while due_month > 12:
                due_month -= 12
                due_year += 1
                
            import calendar
            max_days = calendar.monthrange(due_year, due_month)[1]
            adjusted_due_day = min(due_day, max_days)
            
            due_date = datetime(due_year, due_month, adjusted_due_day).date()
            inst_desc = f"[{matched_card_name} {i}/{installments}] {description}"
            
            records_to_insert.append({
                "type": "despesa",
                "category": category,
                "amount": inst_amount,
                "description": inst_desc,
                "due_date": due_date.strftime("%Y-%m-%d")
            })
            
        success = db.add_financial_records_bulk(records_to_insert)
        if success:
            console.print(
                f"[bold green][SUCESSO][/bold green] Lançamento de cartão registrado!\n"
                f"- Cartão: [yellow]{matched_card_name}[/yellow] | Total: [green]R$ {total_amount:.2f}[/green]\n"
                f"- Parcelamento: [cyan]{installments}x[/cyan]\n"
                f"- Vencimento inicial: [white]{records_to_insert[0]['due_date']}[/white]"
            )
        else:
            console.print("[red]Erro ao salvar lançamentos parcelados no banco de dados.[/red]")
            
    else:
        console.print(f"[red]Subcomando de cartão inválido: '{subcmd}'. Opções: add, list, buy.[/red]")

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
            "- [green]/finance[/green]: Mostra finanças do mês atual. Filtros: [green]/finance next[/green], [green]/finance deleted[/green], [green]/finance restore <ID>[/green], [green]/finance all[/green], [green]/finance mes=MM-YYYY[/green], [green]/finance q=busca[/green], [green]/finance delete <ID>[/green], [green]/finance import[/green], [green]/finance card[/green] (cartão de crédito).\n"
            "- [green]/cron[/green]: Gerencia cronjobs (lista tudo). Use [green]/cron add <nome> <cron_expr> <prompt>[/green] para agendar.\n"
            "- [green]/backup[/green]: Cria uma cópia de segurança criptografada com senha do banco de dados.\n"
            "- [green]/restore [caminho][/green]: Restaura um backup criptografado (permite escolher de uma lista se o caminho for omitido).\n"
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
        llm_provider = db.get_setting("llm_provider", "lm_studio")
        active_model = db.get_setting("active_model", "Nenhum")
        
        console.print(f"[bold cyan]Status do Sistema:[/bold cyan]")
        console.print(f"- Modo de Segurança: {safe_str}")
        console.print(f"- Provedor Ativo: [yellow]{llm_provider.upper()}[/yellow]")
        console.print(f"- Modelo Ativo: [yellow]{active_model}[/yellow]")
        
        # Teste rápido de conexão
        conn = llm.test_provider_connection()
        conn_str = "[bold green]Conectado[/bold green]" if conn else "[bold red]Desconectado[/bold red]"
        console.print(f"- Status do Provedor: {conn_str}")
        
    elif command == "/models":
        # 1. Menu de Provedores
        console.print(Panel(
            "[bold cyan]Escolha o Provedor de LLM:[/bold cyan]\n\n"
            "1. [green]LM Studio[/green] (Local)\n"
            "2. [green]OpenAI[/green]\n"
            "3. [green]Google Gemini[/green]\n"
            "4. [green]Anthropic Claude[/green]\n"
            "5. [green]DeepSeek[/green]\n"
            "6. [green]Alibaba Qwen[/green]\n"
            "7. [green]Moonshot Kimi[/green]\n"
            "8. [green]Personalizado[/green] (OpenAI-Compatible)",
            title="Configuração de Modelos"
        ))
        
        current_provider = db.get_setting("llm_provider", "lm_studio")
        active_model = db.get_setting("active_model", "Nenhum")
        console.print(f"Provedor atual: [yellow]{current_provider}[/yellow] | Modelo atual: [yellow]{active_model}[/yellow]")
        
        provider_sel = Prompt.ask("Digite o número do provedor desejado (ou Enter para manter o atual)", default="")
        
        # Mapeia seleção
        providers_map = {
            "1": "lm_studio",
            "2": "openai",
            "3": "gemini",
            "4": "claude",
            "5": "deepseek",
            "6": "qwen",
            "7": "kimi",
            "8": "custom"
        }
        
        provider = providers_map.get(provider_sel, current_provider)
        
        if provider == "lm_studio":
            # Mantém fluxo original do LM Studio
            models = llm.get_available_models()
            if not models:
                console.print("[red]Nenhum modelo detectado no LM Studio. Certifique-se de que o LM Studio está rodando e com o modelo carregado.[/red]")
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
                    db.set_setting("llm_provider", "lm_studio")
                    db.set_setting("active_model", models[idx])
                    console.print(f"[bold green]Provedor alterado para LM Studio e modelo ativo para:[/bold green] {models[idx]}")
                else:
                    console.print("[red]Índice inválido.[/red]")
            return True
            
        # Configurações para Provedores Externos
        db.set_setting("llm_provider", provider)
        
        # 2. Chave de API
        current_key = db.get_setting("provider_api_key", "")
        key_masked = f"{current_key[:4]}...{current_key[-4:]}" if len(current_key) > 8 else "Não configurada"
        
        api_key = Prompt.ask(
            f"Digite a API KEY para o provedor {provider} (Atual: {key_masked}, Enter para manter)", 
            password=True, 
            default=current_key
        )
        if api_key:
            db.set_setting("provider_api_key", api_key)
            
        # 3. Base URL (para o caso Customizado)
        if provider == "custom":
            current_url = db.get_setting("provider_base_url", "")
            base_url = Prompt.ask(
                f"Digite a URL base do provedor customizado (ex: http://localhost:8000/v1)", 
                default=current_url
            )
            if base_url:
                db.set_setting("provider_base_url", base_url)
                
        # 4. Seleção de Modelos Sugeridos
        sugestoes = {
            "openai": ["gpt-4o", "gpt-4o-mini", "o1-mini", "o1-preview"],
            "gemini": ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash-exp"],
            "claude": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"],
            "deepseek": ["deepseek-chat", "deepseek-coder"],
            "qwen": ["qwen-turbo", "qwen-plus", "qwen-max"],
            "kimi": ["moonshot-v1-8k", "moonshot-v1-32k"],
            "custom": []
        }
        
        modelos_sugeridos = sugestoes.get(provider, [])
        
        if modelos_sugeridos:
            table = Table(title=f"Modelos Recomendados para {provider.upper()}")
            table.add_column("Índice", justify="center", style="cyan")
            table.add_column("Identificador do Modelo", style="magenta")
            
            for idx, mod in enumerate(modelos_sugeridos, 1):
                table.add_row(str(idx), mod)
            table.add_row(str(len(modelos_sugeridos) + 1), "Outro / Digitar modelo personalizado")
            
            console.print(table)
            
            model_sel = Prompt.ask("Escolha o índice do modelo desejado", default="1")
            
            if model_sel.isdigit():
                idx_sel = int(model_sel) - 1
                if 0 <= idx_sel < len(modelos_sugeridos):
                    chosen_model = modelos_sugeridos[idx_sel]
                else:
                    chosen_model = Prompt.ask("Digite o identificador do modelo completo (ex: gpt-3.5-turbo)")
            else:
                chosen_model = Prompt.ask("Digite o identificador do modelo completo (ex: gpt-3.5-turbo)")
        else:
            # Custom ou outro sem sugestões
            chosen_model = Prompt.ask("Digite o identificador do modelo a ser utilizado")
            
        if chosen_model:
            db.set_setting("active_model", chosen_model)
            console.print(f"[bold green][SUCESSO][/bold green] Provedor alterado para [yellow]{provider}[/yellow] e modelo ativo para [yellow]{chosen_model}[/yellow].")
                
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
        # Permite gerenciamento de cartões de crédito: /finance card
        if len(parts) > 1 and parts[1].lower() in ["card", "cartao"]:
            handle_finance_card_command(parts, console)
            return True

        # Permite importação via arquivo CSV: /finance import ou /finance csv
        if len(parts) > 1 and parts[1].lower() in ["import", "csv"]:
            handle_finance_csv_import(console)
            return True

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
            
    elif command == "/backup":
        if len(parts) < 2:
            password = getpass.getpass("Defina uma senha para criptografar o backup: ")
        else:
            password = " ".join(parts[1:])
        if not password.strip():
            console.print("[red]Senha de criptografia não pode ser vazia![/red]")
            return True
            
        console.print("[info]Gerando dump do banco de dados...[/info]")
        try:
            sql_dump = db.generate_sql_dump()
            encrypted_bytes = security.encrypt_data(sql_dump, password)
            
            import os
            from datetime import datetime
            backups_dir = os.path.join(os.getcwd(), "backups")
            os.makedirs(backups_dir, exist_ok=True)
            
            filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.enc"
            filepath = os.path.join(backups_dir, filename)
            
            with open(filepath, "wb") as f:
                f.write(encrypted_bytes)
                
            console.print(f"[bold green][SUCESSO][/bold green] Backup criptografado gerado em: [yellow]backups/{filename}[/yellow]")
        except Exception as e:
            logging.exception("Falha ao gerar backup")
            console.print(f"[bold red][ERRO][/bold red] Falha ao gerar backup: {e}")
            
    elif command == "/restore":
        import os
        backups_dir = os.path.join(os.getcwd(), "backups")
        
        # Determina o arquivo de backup
        filepath = None
        if len(parts) > 1:
            # Caminho explícito fornecido
            filepath = os.path.abspath(parts[1])
        else:
            # Lista os backups disponíveis na pasta
            if not os.path.exists(backups_dir):
                console.print("[yellow]Nenhum backup encontrado na pasta backups/[/yellow]")
                return True
                
            files = [f for f in os.listdir(backups_dir) if f.endswith(".enc")]
            if not files:
                console.print("[yellow]Nenhum arquivo .enc encontrado na pasta backups/[/yellow]")
                return True
                
            # Ordena os backups por data (mais recente primeiro)
            files.sort(reverse=True)
            
            console.print("[bold cyan]Backups Disponíveis:[/bold cyan]")
            for idx, f in enumerate(files, 1):
                console.print(f" {idx}. [yellow]{f}[/yellow]")
                
            selection = Prompt.ask("Selecione o número do backup que deseja restaurar", default="")
            if selection.isdigit():
                sel_idx = int(selection) - 1
                if 0 <= sel_idx < len(files):
                    filepath = os.path.join(backups_dir, files[sel_idx])
                else:
                    console.print("[red]Seleção inválida.[/red]")
                    return True
            else:
                console.print("[red]Restauração cancelada.[/red]")
                return True
                
        if not filepath or not os.path.exists(filepath):
            console.print(f"[red]Arquivo de backup não encontrado: {filepath}[/red]")
            return True
            
        password = getpass.getpass("Digite a senha de descriptografia do backup: ")
        
        console.print(f"[info]Lendo e descriptografando backup: {os.path.basename(filepath)}...[/info]")
        try:
            with open(filepath, "rb") as f:
                encrypted_bytes = f.read()
                
            sql_content = security.decrypt_data(encrypted_bytes, password)
            
            # Avisa antes de realizar a substituição
            confirm = Confirm.ask(
                "[bold red][AVISO][/bold red] A restauração irá apagar todos os dados atuais das tabelas "
                "para aplicar o backup. Deseja prosseguir?", default=False
            )
            
            if confirm:
                if db.restore_sql_dump(sql_content):
                    console.print("[bold green][SUCESSO][/bold green] Banco de dados restaurado com sucesso!")
                else:
                    console.print("[bold red][ERRO][/bold red] Falha na restauração do dump SQL no banco de dados.")
            else:
                console.print("[yellow]Restauração cancelada pelo usuário.[/yellow]")
        except Exception as e:
            logging.exception("Falha ao restaurar backup")
            console.print(f"[bold red][ERRO][/bold red] Senha incorreta ou arquivo de backup inválido/corrompido: {e}")
            
    else:
        console.print(f"[bold red]Comando inválido:[/bold red] {command}. Digite [green]/help[/green] para ver comandos válidos.")
        
    return True

def main():
    console.clear()
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
            logging.exception("Erro crítico no loop de chat")
            console.print(f"\n[bold red]Erro crítico no loop de chat: {e}[/bold red]")

if __name__ == "__main__":
    main()
