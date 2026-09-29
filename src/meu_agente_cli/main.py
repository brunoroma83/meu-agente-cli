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
        
    if not connected_lm and not sys.stdin.isatty():
        console.print("[yellow]Aviso: LM Studio inacessível e ambiente não-interativo detectado. Prosseguindo inicialização com provedor configurado / backup LLM.[/yellow]")
    else:
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

def handle_agent_command(parts: list, console: Console):
    """Gerencia o comando /agent e seus subcomandos (list, use, info, create, improve, delete)."""
    subcmd = parts[1].lower() if len(parts) > 1 else "list"

    if subcmd in ["list", "ls"]:
        agents = db.list_agents()
        active_slug = db.get_active_agent_slug()
        
        table = Table(title="🤖 Central de Agentes (Agent Hub)", border_style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Ícone", justify="center")
        table.add_column("Slug", style="cyan")
        table.add_column("Nome", style="bold white")
        table.add_column("Descrição", style="dim")
        table.add_column("Tipo", style="blue")

        for a in agents:
            is_active = a["slug"] == active_slug
            status_display = "[bold green]ATIVO ⭐[/bold green]" if is_active else ""
            type_display = "Sistema" if a["is_default"] else "Customizado"
            table.add_row(
                status_display,
                a["icon"],
                a["slug"],
                a["name"],
                a["description"] or "",
                type_display
            )

        console.print(table)
        console.print("[dim]Comandos: /agent use <slug> | /agent info <slug> | /agent create | /agent improve <slug> <instrução> | /agent delete <slug>[/dim]\n")

    elif subcmd in ["use", "switch", "ativar"]:
        if len(parts) < 3:
            console.print("[red]Uso correto: /agent use <slug> (ex: /agent use estudo ou /agent use geral)[/red]")
            return

        target_slug = parts[2].lower().strip()
        target_agent = db.get_agent(target_slug)
        if not target_agent:
            console.print(f"[bold red]Erro:[/bold red] Agente '{target_slug}' não encontrado. Use [green]/agent list[/green] para ver os disponíveis.")
            return

        if db.set_active_agent_slug(target_slug):
            console.print(Panel(
                f"[bold green]Agente ativo alterado com sucesso![/bold green]\n\n"
                f"• Nome: {target_agent['icon']} [bold cyan]{target_agent['name']}[/bold cyan] (`{target_agent['slug']}`)\n"
                f"• Descrição: {target_agent['description']}\n\n"
                f"[dim]Suas próximas mensagens serão processadas pelas diretrizes deste agente.[/dim]",
                title="Troca de Agente",
                border_style="green"
            ))
        else:
            console.print("[bold red]Erro ao salvar configuração de agente ativo no banco.[/bold red]")

    elif subcmd in ["info", "show"]:
        if len(parts) < 3:
            console.print("[red]Uso correto: /agent info <slug> (ex: /agent info estudo)[/red]")
            return

        target_slug = parts[2].lower().strip()
        target_agent = db.get_agent(target_slug)
        if not target_agent:
            console.print(f"[bold red]Erro:[/bold red] Agente '{target_slug}' não encontrado.")
            return

        active_slug = db.get_active_agent_slug()
        is_active = target_agent["slug"] == active_slug
        status_tag = " [bold green](ATIVO ATUALMENTE)[/bold green]" if is_active else ""

        info_text = (
            f"[bold cyan]Ficha Técnica do Agente:[/bold cyan]{status_tag}\n\n"
            f"• [bold]Slug:[/bold] `{target_agent['slug']}`\n"
            f"• [bold]Nome:[/bold] {target_agent['icon']} {target_agent['name']}\n"
            f"• [bold]Tipo:[/bold] {'Agente Padrão do Sistema' if target_agent['is_default'] else 'Agente Customizado'}\n"
            f"• [bold]Descrição:[/bold] {target_agent['description']}\n\n"
            f"[bold yellow]System Prompt:[/bold yellow]\n{target_agent['system_prompt']}"
        )
        console.print(Panel(info_text, title=f"Detalhes: {target_agent['name']}", border_style="cyan"))

    elif subcmd in ["create", "novo"]:
        console.print(Panel(
            "[bold cyan]Assistente de Criação de Novo Agente Especialista[/bold cyan]\n"
            "Preencha as informações do novo agente abaixo:",
            border_style="cyan"
        ))
        slug = Prompt.ask("Slug / Identificador (ex: revisor, dev, tutor)").strip().lower()
        if not slug:
            console.print("[red]Criação cancelada: slug não pode ser vazio.[/red]")
            return

        if db.get_agent(slug):
            console.print(f"[red]Já existe um agente com o slug '{slug}'. Escolha outro identificador.[/red]")
            return

        name = Prompt.ask("Nome de exibição (ex: Revisor de Artigos Científicos)").strip()
        icon = Prompt.ask("Ícone / Emoji", default="🤖").strip()
        description = Prompt.ask("Breve descrição da especialidade").strip()
        
        console.print("[yellow]Digite as diretrizes e o prompt de sistema do agente:[/yellow]")
        system_prompt = Prompt.ask("System Prompt").strip()

        if db.create_or_update_agent(slug, name, icon, description, system_prompt, is_default=False):
            console.print(f"[bold green][SUCESSO][/bold green] Agente {icon} [bold cyan]{name}[/bold cyan] criado com sucesso!")
            if Confirm.ask("Deseja ativar este agente agora?", default=True):
                db.set_active_agent_slug(slug)
                console.print(f"[bold green]Agente ativo alterado para '{name}'.[/bold green]")
        else:
            console.print("[bold red]Erro ao salvar agente no banco de dados.[/bold red]")

    elif subcmd in ["improve", "melhorar", "refinar"]:
        if len(parts) < 4:
            console.print("[red]Uso correto: /agent improve <slug> <instrução de melhoria>[/red]\nEx: /agent improve estudo Sempre sugira exercícios práticos ao final")
            return

        slug = parts[2].lower().strip()
        instruction = " ".join(parts[3:]).strip()

        agent_data = db.get_agent(slug)
        if not agent_data:
            console.print(f"[bold red]Erro:[/bold red] Agente '{slug}' não encontrado.")
            return

        with console.status(f"[bold blue]Aprimorando prompt do agente '{agent_data['name']}' com IA...", spinner="dots"):
            import meu_agente_cli.tools as cli_tools
            res = cli_tools.manage_agents_tool(action="improve", slug=slug, instruction=instruction)

        console.print(Panel(res, title=f"Melhoria do Agente: {agent_data['name']}", border_style="green"))

    elif subcmd in ["delete", "del", "remover"]:
        if len(parts) < 3:
            console.print("[red]Uso correto: /agent delete <slug>[/red]")
            return

        slug = parts[2].lower().strip()
        agent_data = db.get_agent(slug)
        if not agent_data:
            console.print(f"[bold red]Erro:[/bold red] Agente '{slug}' não encontrado.")
            return

        if agent_data["is_default"]:
            console.print("[bold red]Não é permitido excluir agentes padrão do sistema.[/bold red]")
            return

        if Confirm.ask(f"Deseja realmente excluir o agente '{agent_data['name']}' (`{slug}`)?", default=False):
            if db.delete_agent(slug):
                console.print(f"[bold green]Agente '{slug}' excluído com sucesso.[/bold green]")
            else:
                console.print("[bold red]Erro ao excluir agente.[/bold red]")
        else:
            console.print("[yellow]Exclusão cancelada.[/yellow]")

    else:
        # Se digitou /agent <slug_direto>, tenta alternar diretamente
        target_agent = db.get_agent(subcmd)
        if target_agent:
            db.set_active_agent_slug(subcmd)
            console.print(f"[bold green]Agente ativo alterado para:[/bold green] {target_agent['icon']} [bold cyan]{target_agent['name']}[/bold cyan] (`{subcmd}`)")
        else:
            console.print(f"[red]Subcomando ou agente desconhecido: '{subcmd}'. Digite [green]/agent list[/green] para ver as opções.[/red]")

def handle_mcp_command(parts: list, console: Console):
    """Gerencia o comando /mcp e seus subcomandos (list, add, test, toggle, delete, sync)."""
    from meu_agente_cli import mcp_client
    import meu_agente_cli.tools as cli_tools

    subcmd = parts[1].lower() if len(parts) > 1 else "list"

    if subcmd in ["list", "ls"]:
        servers = db.list_mcp_servers()
        if not servers:
            console.print("[yellow]Nenhum servidor MCP cadastrado. Use [green]/mcp add <nome> <url> [api_key][/green] para cadastrar.[/yellow]")
            return

        table = Table(title="🔌 Servidores MCP Conectados (Model Context Protocol)", border_style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Nome", style="bold cyan")
        table.add_column("URL", style="white")
        table.add_column("Chave API", justify="center")
        table.add_column("Transporte", style="magenta")
        table.add_column("Ferramentas", justify="center", style="green")

        for s in servers:
            is_act = s["is_active"]
            status_disp = "[bold green]ATIVO 🟢[/bold green]" if is_act else "[dim red]INATIVO 🔴[/dim red]"
            has_key = "[green]Configurada[/green]" if s.get("api_key") else "[dim]Nenhuma[/dim]"
            
            cached = mcp_client._MCP_CACHE.get(s["name"], {}).get("tools", [])
            tools_disp = f"{len(cached)} tools" if cached else "Pendente (/mcp test)"
            
            table.add_row(
                status_disp,
                s["name"],
                s["url"],
                has_key,
                s.get("transport", "sse"),
                tools_disp
            )

        console.print(table)
        console.print("[dim]Comandos: /mcp add <nome> <url> [api_key] | /mcp test <nome> | /mcp toggle <nome> | /mcp sync | /mcp delete <nome>[/dim]\n")

    elif subcmd in ["add", "save", "set"]:
        if len(parts) >= 4:
            name = parts[2].lower().strip()
            url = parts[3].strip()
            api_key = parts[4].strip() if len(parts) > 4 else None
        elif len(parts) == 3:
            name = parts[2].lower().strip()
            url = Prompt.ask("URL do servidor MCP (ex: http://host.docker.internal:8000/sse)").strip()
            api_key = Prompt.ask("API Key ou Token de Autenticação (deixe em branco se não houver)", default="").strip() or None
        else:
            name = Prompt.ask("Nome amigável do servidor MCP (ex: crm)").strip().lower()
            url = Prompt.ask("URL do servidor MCP (ex: http://host.docker.internal:8000/sse)").strip()
            api_key = Prompt.ask("API Key ou Token de Autenticação (deixe em branco se não houver)", default="").strip() or None

        if not name or not url:
            console.print("[red]Erro: Nome e URL são obrigatórios para registrar o servidor MCP.[/red]")
            return

        with console.status(f"[bold blue]Conectando e testando servidor MCP '{name}'...", spinner="dots"):
            res = cli_tools.manage_mcp_tool(action="save", name=name, url=url, api_key=api_key)

        console.print(Panel(res, title=f"Configuração MCP: {name}", border_style="cyan"))

    elif subcmd == "test":
        if len(parts) < 3:
            console.print("[red]Uso: /mcp test <nome>[/red]")
            return
        name = parts[2].lower().strip()
        server_data = db.get_mcp_server(name)
        if not server_data:
            console.print(f"[bold red]Erro:[/bold red] Servidor MCP '{name}' não encontrado.")
            return

        with console.status(f"[bold blue]Testando conexão e descobrindo ferramentas de '{name}'...", spinner="dots"):
            success, msg, tools = mcp_client.test_mcp_connection(
                server_data["url"], server_data.get("api_key"), server_data.get("transport", "sse"), server_data.get("headers")
            )

        if success:
            console.print(f"[bold green]Sucesso:[/bold green] {msg}\n")
            if tools:
                tools_table = Table(title=f"Ferramentas Disponíveis no Servidor '{name}'", border_style="green")
                tools_table.add_column("Nome da Ferramenta", style="cyan")
                tools_table.add_column("Descrição", style="white")
                tools_table.add_column("Parâmetros Obrigatórios", style="yellow")
                
                for t in tools:
                    schema = t.get("input_schema", {})
                    req = schema.get("required", [])
                    tools_table.add_row(
                        f"mcp_{name}_{t['name']}",
                        t.get("description", "-"),
                        ", ".join(req) if req else "[dim]Nenhum[/dim]"
                    )
                console.print(tools_table)
            else:
                console.print("[yellow]O servidor conectou com sucesso, mas não expôs nenhuma ferramenta.[/yellow]")
        else:
            console.print(f"[bold red]Falha na conexão com '{name}':[/bold red] {msg}")

    elif subcmd in ["toggle", "ativar", "desativar"]:
        if len(parts) < 3:
            console.print("[red]Uso: /mcp toggle <nome>[/red]")
            return
        name = parts[2].lower().strip()
        if db.toggle_mcp_server(name):
            s = db.get_mcp_server(name)
            state_str = "[bold green]ATIVADO[/bold green]" if s["is_active"] else "[bold red]DESATIVADO[/bold red]"
            console.print(f"Status do servidor MCP '{name}' alterado para: {state_str}")
        else:
            console.print(f"[bold red]Erro:[/bold red] Servidor MCP '{name}' não encontrado.")

    elif subcmd in ["delete", "del", "remove"]:
        if len(parts) < 3:
            console.print("[red]Uso: /mcp delete <nome>[/red]")
            return
        name = parts[2].lower().strip()
        if Confirm.ask(f"Deseja realmente remover o servidor MCP '{name}'?", default=False):
            if db.delete_mcp_server(name):
                console.print(f"[bold green]Servidor MCP '{name}' removido com sucesso.[/bold green]")
            else:
                console.print(f"[bold red]Erro ao remover servidor MCP '{name}'.[/bold red]")
        else:
            console.print("[yellow]Remoção cancelada.[/yellow]")

    elif subcmd == "sync":
        with console.status("[bold blue]Sincronizando todas as ferramentas MCP ativas...", spinner="dots"):
            all_tools = mcp_client.sync_all_active_tools(force_refresh=True)
        total = sum(len(t) for t in all_tools.values())
        console.print(f"[bold green]Sincronização concluída com sucesso![/bold green] Total de {total} ferramentas ativas em {len(all_tools)} servidor(es) MCP.")

    else:
        console.print(f"[red]Subcomando MCP desconhecido: '{subcmd}'. Opções: list, add, test, toggle, sync, delete.[/red]")

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

    elif command == "/agent":
        handle_agent_command(parts, console)

    elif command == "/mcp":
        handle_mcp_command(parts, console)

    elif command == "/help":
        console.print(Panel(
            "[bold cyan]Comandos Disponíveis:[/bold cyan]\n"
            "- [green]/help[/green]: Mostra esta lista de ajuda.\n"
            "- [green]/agent[/green]: Gerencia agentes especialistas. Opções: [green]/agent[/green] (listar), [green]/agent use <slug>[/green] (alternar), [green]/agent info <slug>[/green], [green]/agent create[/green], [green]/agent improve <slug> <instrução>[/green], [green]/agent delete <slug>[/green].\n"
            "- [green]/mcp[/green]: Central MCP (Model Context Protocol). Conecta servidores MCP (ex: CRM), descobre ferramentas e gerencia credenciais. Opções: [green]/mcp[/green] (listar), [green]/mcp add <nome> <url> [api_key][/green], [green]/mcp test <nome>[/green], [green]/mcp toggle <nome>[/green], [green]/mcp sync[/green], [green]/mcp delete <nome>[/green].\n"
            "- [green]/status[/green]: Mostra conexões e estado atual de segurança.\n"
            "- [green]/clear[/green]: Limpa o histórico de conversa (reseta o contexto do agente).\n"
            "- [green]/history <limite>[/green]: Exibe ou altera a quantidade de mensagens enviadas no histórico (contexto recente) ao LLM.\n"
            "- [green]/models[/green]: Lista os modelos disponíveis no LM Studio e permite trocar.\n"
            "- [green]/safe[/green]: Ativa o Modo Seguro (execução apenas de comandos permitidos).\n"
            "- [green]/unsafe[/green]: Desativa o Modo Seguro (requer senha de segurança).\n"
            "- [green]/notes[/green]: Gerencia notas (lista tudo).\n"
            "- [green]/finance[/green]: Mostra finanças do mês atual. Filtros: [green]/finance web[/green] (dashboard Gradio), [green]/finance next[/green], [green]/finance deleted[/green], [green]/finance restore <ID>[/green], [green]/finance all[/green], [green]/finance mes=MM-YYYY[/green], [green]/finance q=busca[/green], [green]/finance delete <ID>[/green], [green]/finance import[/green], [green]/finance card[/green] (cartão de crédito).\n"
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
        active_slug = db.get_active_agent_slug()
        agent_info = db.get_agent(active_slug)
        agent_str = f"{agent_info['icon']} {agent_info['name']} ({active_slug})" if agent_info else active_slug
        
        console.print(f"[bold cyan]Status do Sistema:[/bold cyan]")
        console.print(f"- Modo de Segurança: {safe_str}")
        console.print(f"- Agente Ativo: [bold yellow]{agent_str}[/bold yellow]")
        console.print(f"- Provedor Principal: [yellow]{llm_provider.upper()}[/yellow]")
        console.print(f"- Modelo Principal: [yellow]{active_model}[/yellow]")
        
        # Teste de conexão do principal
        conn = llm.test_provider_connection("primary")
        conn_str = "[bold green]Conectado[/bold green]" if conn else "[bold red]Desconectado / Indisponível[/bold red]"
        console.print(f"- Status LLM Principal: {conn_str}")
        
        # Informações e status do Backup
        backup_cfg = db.get_backup_llm_config()
        if backup_cfg["enabled"] and backup_cfg["provider"]:
            b_prov = backup_cfg["provider"].upper()
            b_mod = backup_cfg["model"] or "padrão"
            b_conn = llm.test_backup_provider_connection()
            b_conn_str = "[bold green]Conectado[/bold green]" if b_conn else "[bold red]Desconectado / Indisponível[/bold red]"
            console.print(f"- LLM de Backup: [bold green]ATIVADO[/bold green] ({b_prov} - {b_mod})")
            console.print(f"- Status LLM Backup: {b_conn_str}")
        else:
            console.print(f"- LLM de Backup: [bold red]DESATIVADO[/bold red]")
        
    elif command == "/models":
        def _configure_provider(is_backup: bool = False):
            target_label = "LLM de BACKUP" if is_backup else "LLM PRINCIPAL"
            if is_backup:
                cfg = db.get_backup_llm_config()
                current_p = cfg.get("provider") or "Nenhum"
                current_m = cfg.get("model") or "Nenhum"
            else:
                current_p = db.get_setting("llm_provider", "lm_studio")
                current_m = db.get_setting("active_model", "Nenhum")
                
            console.print(Panel(
                f"[bold cyan]Escolha o Provedor para {target_label}:[/bold cyan]\n\n"
                "1. [green]LM Studio[/green] (Local)\n"
                "2. [green]OpenAI[/green]\n"
                "3. [green]Google Gemini[/green]\n"
                "4. [green]Anthropic Claude[/green]\n"
                "5. [green]DeepSeek[/green]\n"
                "6. [green]Alibaba Qwen[/green]\n"
                "7. [green]Moonshot Kimi[/green]\n"
                "8. [green]Personalizado[/green] (OpenAI-Compatible)\n"
                "9. [green]Cloudflare Workers AI[/green]",
                title=f"Configuração - {target_label}"
            ))
            console.print(f"Atual: Provedor [yellow]{current_p.upper()}[/yellow] | Modelo [yellow]{current_m}[/yellow]")
            provider_sel = Prompt.ask("Digite o número do provedor desejado (ou Enter para manter o atual)", default="")
            
            providers_map = {
                "1": "lm_studio",
                "2": "openai",
                "3": "gemini",
                "4": "claude",
                "5": "deepseek",
                "6": "qwen",
                "7": "kimi",
                "8": "custom",
                "9": "cloudflare"
            }
            provider = providers_map.get(provider_sel, current_p if current_p != "Nenhum" else "lm_studio")
            
            if provider == "lm_studio":
                models = llm.get_available_models()
                if not models:
                    console.print("[red]Nenhum modelo detectado no LM Studio. Certifique-se de que o LM Studio está rodando.[/red]")
                    if not is_backup:
                        return
                    models = ["local-model"]
                
                table = Table(title="Modelos Disponíveis no LM Studio")
                table.add_column("Índice", justify="center", style="cyan")
                table.add_column("Nome do Modelo", style="magenta")
                table.add_column("Status", justify="center", style="green")
                
                for idx, m in enumerate(models, 1):
                    status = "[bold green]Ativo[/bold green]" if m == current_m else ""
                    table.add_row(str(idx), m, status)
                console.print(table)
                
                sel = Prompt.ask("Digite o índice do modelo desejado", default="1")
                chosen_model = models[0]
                if sel.isdigit():
                    idx_val = int(sel) - 1
                    if 0 <= idx_val < len(models):
                        chosen_model = models[idx_val]
                        
                if is_backup:
                    db.set_backup_llm_config(enabled=True, provider="lm_studio", model=chosen_model, api_key="", base_url="")
                    console.print(f"[bold green][SUCESSO][/bold green] LLM de Backup configurado: [yellow]LM Studio[/yellow] - [yellow]{chosen_model}[/yellow].")
                else:
                    db.set_setting("llm_provider", "lm_studio")
                    db.set_setting("active_model", chosen_model)
                    console.print(f"[bold green][SUCESSO][/bold green] LLM Principal configurado: [yellow]LM Studio[/yellow] - [yellow]{chosen_model}[/yellow].")
                return

            # Provedores Externos
            saved_key = db.get_provider_api_key(provider) or (os.environ.get("CLOUDFLARE_API_TOKEN", "") if provider == "cloudflare" else "")
            key_masked = f"{saved_key[:4]}...{saved_key[-4:]}" if len(saved_key) > 8 else ("Configurada" if saved_key else "Não configurada")
            api_key = Prompt.ask(
                f"Digite a API KEY / Token para {provider.upper()} (Atual: {key_masked}, Enter para manter)",
                password=True,
                default=saved_key
            )
            if api_key:
                db.set_provider_api_key(provider, api_key)
                
            base_url = ""
            if provider == "custom":
                current_url = db.get_setting("backup_provider_base_url" if is_backup else "provider_base_url", "")
                base_url = Prompt.ask("Digite a URL base do provedor customizado", default=current_url)
            elif provider == "cloudflare":
                current_acc = db.get_setting("backup_provider_base_url" if is_backup else "provider_base_url", "") or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
                base_url = Prompt.ask("Digite o Account ID do Cloudflare", default=current_acc)
                
            sugestoes = {
                "openai": ["gpt-4o", "gpt-4o-mini", "o1-mini", "o1-preview"],
                "gemini": ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash-exp"],
                "claude": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"],
                "deepseek": ["deepseek-chat", "deepseek-coder"],
                "qwen": ["qwen-turbo", "qwen-plus", "qwen-max"],
                "kimi": ["moonshot-v1-8k", "moonshot-v1-32k"],
                "cloudflare": ["@cf/qwen/qwen3-30b-a3b-fp8", "@cf/meta/llama-3.1-8b-instruct", "@cf/meta/llama-3.1-70b-instruct"],
                "custom": []
            }
            modelos_sug = sugestoes.get(provider, [])
            if modelos_sug:
                table = Table(title=f"Modelos Recomendados para {provider.upper()}")
                table.add_column("Índice", justify="center", style="cyan")
                table.add_column("Identificador do Modelo", style="magenta")
                for idx, mod in enumerate(modelos_sug, 1):
                    table.add_row(str(idx), mod)
                table.add_row(str(len(modelos_sug) + 1), "Outro / Digitar personalizado")
                console.print(table)
                
                model_sel = Prompt.ask("Escolha o índice do modelo desejado", default="1")
                if model_sel.isdigit():
                    idx_sel = int(model_sel) - 1
                    if 0 <= idx_sel < len(modelos_sug):
                        chosen_model = modelos_sug[idx_sel]
                    else:
                        chosen_model = Prompt.ask("Digite o identificador do modelo completo")
                else:
                    chosen_model = Prompt.ask("Digite o identificador do modelo completo")
            else:
                chosen_model = Prompt.ask("Digite o identificador do modelo a ser utilizado")
                
            if chosen_model:
                if is_backup:
                    db.set_backup_llm_config(
                        enabled=True,
                        provider=provider,
                        model=chosen_model,
                        api_key=api_key or saved_key,
                        base_url=base_url
                    )
                    console.print(f"[bold green][SUCESSO][/bold green] LLM de Backup configurado e ativado: [yellow]{provider.upper()}[/yellow] - [yellow]{chosen_model}[/yellow].")
                else:
                    db.set_setting("llm_provider", provider)
                    db.set_setting("provider_api_key", api_key or saved_key)
                    if base_url:
                        db.set_setting("provider_base_url", base_url)
                    db.set_setting("active_model", chosen_model)
                    console.print(f"[bold green][SUCESSO][/bold green] LLM Principal configurado: [yellow]{provider.upper()}[/yellow] - [yellow]{chosen_model}[/yellow].")

        backup_cfg = db.get_backup_llm_config()
        b_status_str = "[bold green]ATIVO[/bold green]" if (backup_cfg["enabled"] and backup_cfg["provider"]) else "[bold red]INATIVO[/bold red]"
        b_info_str = f"({backup_cfg['provider'].upper()} - {backup_cfg['model']})" if (backup_cfg["enabled"] and backup_cfg["provider"]) else ""
        
        console.print(Panel(
            f"[bold cyan]Gerenciamento de Modelos de Linguagem (LLM):[/bold cyan]\n\n"
            f"1. [green]Configurar LLM Principal[/green] (Atual: [yellow]{db.get_setting('llm_provider', 'lm_studio').upper()}[/yellow] - [yellow]{db.get_setting('active_model', 'Nenhum')}[/yellow])\n"
            f"2. [green]Configurar LLM de Backup[/green] (Atual: {b_status_str} {b_info_str})\n"
            f"3. [green]Alternar LLM de Backup (Ligar / Desligar)[/green]\n"
            f"4. [green]Testar Conexão dos Modelos (Principal e Backup)[/green]\n"
            f"5. [green]Voltar[/green]",
            title="Configuração de Modelos e Resiliência"
        ))
        
        menu_choice = Prompt.ask("Escolha uma opção (1-5)", default="1")
        if menu_choice == "1":
            _configure_provider(is_backup=False)
        elif menu_choice == "2":
            _configure_provider(is_backup=True)
        elif menu_choice == "3":
            new_state = not backup_cfg.get("enabled", False)
            db.set_backup_llm_config(enabled=new_state)
            state_label = "[bold green]ATIVADO[/bold green]" if new_state else "[bold red]DESATIVADO[/bold red]"
            console.print(f"[bold cyan]LLM de Backup agora está:[/bold cyan] {state_label}")
        elif menu_choice == "4":
            console.print("[bold cyan]Testando conexões com os LLMs...[/bold cyan]")
            p_conn = llm.test_provider_connection("primary")
            p_str = "[bold green]Conectado com sucesso[/bold green]" if p_conn else "[bold red]Falha na conexão[/bold red]"
            console.print(f"- LLM Principal ({db.get_setting('llm_provider', 'lm_studio').upper()}): {p_str}")
            
            b_cfg = db.get_backup_llm_config()
            if b_cfg["provider"]:
                b_conn = llm.test_backup_provider_connection()
                b_str = "[bold green]Conectado com sucesso[/bold green]" if b_conn else "[bold red]Falha na conexão[/bold red]"
                console.print(f"- LLM de Backup ({b_cfg['provider'].upper()}): {b_str}")
            else:
                console.print("- LLM de Backup: [yellow]Nenhum provedor configurado como backup.[/yellow]")
                
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
        # Permite abrir/visualizar link do dashboard web Gradio: /finance web ou /finance dashboard
        if len(parts) > 1 and parts[1].lower() in ["web", "dashboard", "gradio"]:
            console.print(Panel(
                "[bold green]🌐 Dashboard Financeiro Gradio[/bold green]\n\n"
                "Acesse em seu navegador pelo link:\n"
                "[bold cyan]👉 http://localhost:7860[/bold cyan]\n\n"
                "[dim]Se o serviço ainda não estiver rodando no Docker, inicialize com:\n"
                "docker compose up -d dashboard[/dim]",
                title="Painel Web Gradio",
                border_style="green"
            ))
            return True

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
            
        # Permite gerar projeção anual de gastos fixos: /finance project [ano]
        if len(parts) > 1 and parts[1].lower() in ["project", "projetar", "previsao"]:
            from datetime import datetime
            target_year = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else datetime.now().year
            res = db.project_annual_fixed_expenses(year=target_year, start_month=1)
            gerados = res.get("gerados", 0)
            tot_val = res.get("total_valor", 0.0)
            meses = res.get("meses_afetados", [])
            if gerados > 0:
                meses_str = ", ".join(str(m) for m in meses)
                console.print(f"[bold green]🔮 Previsão Anual de Gastos Fixos Gerada com Sucesso![/bold green]")
                console.print(f"- Ano: [cyan]{target_year}[/cyan] (Meses afetados: [yellow]{meses_str}[/yellow])")
                console.print(f"- Total de lançamentos criados: [bold green]{gerados}[/bold green]")
                console.print(f"- Valor total projetado: [bold green]R$ {tot_val:.2f}[/bold green]")
            else:
                console.print(f"[yellow]ℹ️ Todas as contas fixas do ano {target_year} já estão projetadas e em dia (nenhuma duplicação gerada).[/yellow]")
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
        due_date = None
        start_due_date = None
        end_due_date = None
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
            elif arg.lower().startswith("venc="):
                val = arg.split("=")[1]
                if ":" in val:
                    parts_d = val.split(":", 1)
                    start_due_date = parts_d[0].strip()
                    end_due_date = parts_d[1].strip()
                    table_title = f"Transações com Vencimento entre {start_due_date} e {end_due_date}"
                else:
                    due_date = val.strip()
                    table_title = f"Transações com Vencimento em {due_date}"
            elif arg.lower().startswith("q="):
                query = arg.split("=")[1]
                table_title = f"Busca de Transações por '{query}'"
            else:
                console.print("[red]Filtro inválido. Use /finance, /finance next, /finance deleted, /finance restore <ID>, /finance all, /finance mes=MM-YYYY, /finance venc=YYYY-MM-DD[:YYYY-MM-DD] ou /finance q=busca[/red]")
                return True
                
        if show_deleted:
            records = db.get_deleted_financial_records()
        else:
            records = db.search_financial_records(
                limit=limit, 
                month_year=month_year, 
                query=query, 
                due_date=due_date, 
                start_due_date=start_due_date, 
                end_due_date=end_due_date
            )
        
        # Calcula somatório dinâmico com base apenas nos registros filtrados/exibidos
        sum_receitas = 0.0
        sum_despesas = 0.0
        for _, rtype, _, val, _, _, _ in records:
            if rtype == "receita":
                sum_receitas += val
            elif rtype == "despesa":
                sum_despesas += val
        sum_saldo = sum_receitas - sum_despesas
        
        # Se for consulta com mês especificado ou padrão, exibe o painel de orçamento e teto diário
        if month_year:
            budget = db.get_daily_budget_summary(month_year)
            status_style = "bold green" if budget["status_hoje"] == "ok" else "bold red"
            status_text = "Dentro da meta diária!" if budget["status_hoje"] == "ok" else "Atenção: ultrapassou o teto diário!"
            console.print(Panel(
                f"[bold green]Receitas:[/bold green] R$ {budget['receitas_mes']:.2f}  |  "
                f"[bold red]Custos Fixos & Faturas:[/bold red] R$ {budget['custos_fixos_mes']:.2f}  |  "
                f"[bold yellow]Gastos Diários:[/bold yellow] R$ {budget['gastos_diarios_mes']:.2f}\n"
                f"[bold cyan]Saldo Livre Restante:[/bold cyan] R$ {budget['saldo_livre_restante']:.2f}  |  "
                f"[bold magenta]Teto Diário Recomendado:[/bold magenta] R$ {budget['teto_diario']:.2f}/dia (restam {budget['dias_restantes']} dias)\n"
                f"[{status_style}]Gasto Hoje: R$ {budget['gasto_hoje']:.2f} ({status_text})[/{status_style}]",
                title=f"🎯 Orçamento & Teto Diário ({month_year})",
                expand=False
            ))
        else:
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
            active_slug = db.get_active_agent_slug()
            agent_info = db.get_agent(active_slug)
            agent_tag = f" [cyan]({agent_info['icon']} {agent_info['slug']})[/cyan]" if agent_info else ""
            user_input = Prompt.ask(f"\n[bold green]Você[/bold green]{agent_tag}").strip()
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
