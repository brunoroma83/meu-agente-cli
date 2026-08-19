from typing import Optional
from croniter import croniter
import meu_agente_cli.db as db

def run(**kwargs) -> str:
    """
    Gerencia tarefas agendadas em segundo plano (cron jobs).
    
    Args:
        action (str): Ação a ser executada ('add', 'list', 'delete').
        name (str, opcional): Nome descritivo da tarefa (obrigatório para action='add').
        cron_expression (str, opcional): Expressão cron ex: '*/5 * * * *' para cada 5 min (obrigatório para action='add').
        task_prompt (str, opcional): Instrução em linguagem natural que o subagente executará (obrigatório para action='add').
        job_id (int, opcional): ID da tarefa a ser removida (obrigatório para action='delete').
    """
    action = kwargs.get("action", "").strip().lower()
    
    if action == "add":
        name = kwargs.get("name")
        cron_expression = kwargs.get("cron_expression")
        task_prompt = kwargs.get("task_prompt")
        
        if not name or not cron_expression or not task_prompt:
            return "Erro: 'name', 'cron_expression' e 'task_prompt' são obrigatórios para a ação 'add'."
        
        # Validação da expressão cron
        try:
            croniter(cron_expression)
        except Exception as e:
            return f"Erro: Expressão cron '{cron_expression}' inválida. Detalhes: {e}"
            
        success = db.add_cron_job(name, cron_expression, task_prompt)
        if success:
            return f"Sucesso: Subagente '{name}' agendado com sucesso ({cron_expression})!"
        else:
            return "Erro: Falha ao adicionar tarefa cron no banco de dados."
            
    elif action == "list":
        jobs = db.get_active_cron_jobs()
        if not jobs:
            return "Nenhum subagente agendado (cron job) ativo no momento."
            
        linhas = ["Subagentes Agendados (Cron Jobs) Ativos:"]
        for j in jobs:
            last_run = j["last_run"].strftime("%d/%m/%Y %H:%M:%S") if j["last_run"] else "Nunca"
            next_run = j["next_run"].strftime("%d/%m/%Y %H:%M:%S") if j["next_run"] else "N/A"
            linhas.append(
                f"- ID: {j['id']} | Nome: {j['name']} | Cron: {j['cron_expression']} | "
                f"Próximo: {next_run} | Último: {last_run} | Instrução: {j['task_prompt']}"
            )
        return "\n".join(linhas)
        
    elif action == "delete":
        job_id = kwargs.get("job_id")
        if job_id is None:
            return "Erro: 'job_id' é obrigatório para a ação 'delete'."
        try:
            job_id_int = int(job_id)
        except ValueError:
            return "Erro: 'job_id' deve ser um número inteiro."
            
        if db.delete_cron_job(job_id_int):
            return f"Sucesso: Cronjob #{job_id_int} removido com sucesso."
        else:
            return f"Erro: Não foi possível remover o cronjob #{job_id_int}."
            
    else:
        return f"Erro: Ação '{action}' desconhecida. Use 'add', 'list' ou 'delete'."
