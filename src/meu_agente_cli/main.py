import os
import sys
import argparse
import asyncio
import logging
import uvicorn
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

import meu_agente_cli.db as db

console = Console()

def run_server(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    """Inicializa o banco de dados e inicia o servidor Web FastAPI + MCP Server."""
    console.print(Panel(
        f"[bold cyan]MEU FINANCEIRO - SERVIÇO & MCP SERVER[/bold cyan]\n"
        f"Interface Web Leve: [bold green]http://{host}:{port}/dashboard[/bold green]\n"
        f"Endpoint MCP Hermes: [bold yellow]http://{host}:{port}/sse[/bold yellow]\n"
        f"Usuários Iniciais: [italic]bruno[/italic] / [italic]fabiana[/italic]",
        border_style="cyan"
    ))
    
    # Inicializa / migra tabelas no banco de dados
    try:
        db.init_database()
    except Exception as e:
        console.print(f"[bold red]Aviso ao conectar/inicializar banco de dados: {e}[/bold red]")

    uvicorn.run(
        "meu_agente_cli.web_app:app",
        host=host,
        port=port,
        reload=reload,
        proxy_headers=True,
        forwarded_allow_ips="*"
    )

def main():
    parser = argparse.ArgumentParser(description="Meu Financeiro - Gestão Financeira com Web UI Leve e MCP Server")
    parser.add_argument("--host", default=os.environ.get("SERVER_HOST", "0.0.0.0"), help="Host para o servidor HTTP")
    parser.add_argument("--port", type=int, default=int(os.environ.get("SERVER_PORT", "8000")), help="Porta para o servidor HTTP")
    parser.add_argument("--reload", action="store_true", help="Ativa auto-reload para desenvolvimento")
    parser.add_argument("--init-db", action="store_true", help="Executa apenas a inicialização e migração do banco")
    parser.add_argument("--set-password", nargs=2, metavar=("USER", "PASSWORD"), help="Define nova senha para o usuário informado")
    parser.add_argument("--change-password", metavar="USER", help="Altera a senha do usuário interativamente com prompt seguro")
    parser.add_argument("--list-users", action="store_true", help="Lista os usuários cadastrados no sistema")
    parser.add_argument("--create-token", type=str, help="Gera um novo token MCP com o apelido informado")
    parser.add_argument("--list-tokens", action="store_true", help="Lista todos os tokens MCP cadastrados")
    parser.add_argument("--stdio", action="store_true", help="Executa o MCP Server no modo stdio para agentes locais")

    args = parser.parse_args()

    if args.init_db:
        console.print("[cyan]Inicializando banco de dados e migrações...[/cyan]")
        ok = db.init_database()
        if ok:
            console.print("[bold green]Banco de dados inicializado com sucesso![/bold green]")
        else:
            console.print("[bold red]Falha na inicialização do banco de dados.[/bold red]")
            sys.exit(1)
        return

    if args.create_token:
        db.init_database()
        ok, raw_token, msg = db.create_mcp_token_record(name=args.create_token, created_by="cli")
        if ok:
            console.print(f"[bold green]Token criado com sucesso para '{args.create_token}':[/bold green]")
            console.print(f"[bold yellow]{raw_token}[/bold yellow]")
            console.print("[italic text-slate-400]Guarde este token com segurança. Ele não será exibido novamente.[/italic text-slate-400]")
        else:
            console.print(f"[bold red]Erro ao criar token: {msg}[/bold red]")
        return

    if args.list_tokens:
        db.init_database()
        tokens = db.list_mcp_tokens()
        table = Table(title="Tokens MCP Cadastrados", border_style="cyan")
        table.add_column("ID", style="dim")
        table.add_column("Apelido", style="bold white")
        table.add_column("Prefixo", style="yellow")
        table.add_column("Criado Por", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Último Acesso", style="dim")
        for t in tokens:
            status_str = "[green]Ativo[/green]" if t["is_active"] else "[red]Revogado[/red]"
            last_used = t["last_used_at"].strftime("%d/%m/%Y %H:%M") if t["last_used_at"] else "Nunca"
            table.add_row(str(t["id"]), t["name"], t["prefix"], t["created_by"], status_str, last_used)
        console.print(table)
        return

    if args.list_users:
        db.init_database()
        users = db.list_users()
        table = Table(title="Usuários Cadastrados", border_style="cyan")
        table.add_column("ID", style="dim")
        table.add_column("Usuário", style="bold white")
        table.add_column("Nome de Exibição", style="cyan")
        table.add_column("Perfil / Role", style="green")
        table.add_column("Criado Em", style="dim")
        for u in users:
            created = u["created_at"].strftime("%d/%m/%Y %H:%M") if u.get("created_at") else "-"
            table.add_row(str(u["user_id"]), u["user_name"], u["display_name"], u["role"], str(created))
        console.print(table)
        return

    if args.set_password:
        target_user, new_pwd = args.set_password
        if len(new_pwd) < 6:
            console.print("[bold red]Erro: A nova senha deve ter no mínimo 6 caracteres.[/bold red]")
            sys.exit(1)
        db.init_database()
        ok = db.update_user_password(target_user, new_pwd)
        if ok:
            console.print(f"[bold green]Senha do usuário '{target_user}' atualizada com sucesso![/bold green]")
        else:
            console.print(f"[bold red]Erro: Usuário '{target_user}' não encontrado ou falha ao atualizar.[/bold red]")
            sys.exit(1)
        return

    if args.change_password:
        import getpass
        target_user = args.change_password.strip().lower()
        db.init_database()
        user_data = db.get_user(target_user)
        if not user_data:
            console.print(f"[bold red]Erro: Usuário '{target_user}' não encontrado no banco.[/bold red]")
            sys.exit(1)
            
        console.print(f"[cyan]Alterando senha para o usuário [bold white]{user_data['display_name']} ({target_user})[/bold white]:[/cyan]")
        p1 = getpass.getpass("Nova senha (mínimo 6 caracteres): ")
        if len(p1) < 6:
            console.print("[bold red]Erro: A senha deve ter no mínimo 6 caracteres.[/bold red]")
            sys.exit(1)
        p2 = getpass.getpass("Confirme a nova senha: ")
        if p1 != p2:
            console.print("[bold red]Erro: As senhas digitadas não conferem.[/bold red]")
            sys.exit(1)
            
        ok = db.update_user_password(target_user, p1)
        if ok:
            console.print(f"[bold green]Senha de '{target_user}' alterada com sucesso![/bold green]")
        else:
            console.print("[bold red]Erro ao atualizar senha no banco de dados.[/bold red]")
            sys.exit(1)
        return

    if args.stdio:
        from meu_agente_cli.mcp_server import mcp_server
        console.print("[cyan]Iniciando MCP Server via stdio...[/cyan]")
        asyncio.run(mcp_server.run_stdio_async())
        return

    # Inicia o servidor HTTP unificado (Web UI + MCP SSE)
    run_server(host=args.host, port=args.port, reload=args.reload)

if __name__ == "__main__":
    main()
