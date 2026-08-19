import asyncio
import logging
import threading
import sys
from datetime import datetime
from croniter import croniter
import meu_agente_cli.db as db
import meu_agente_cli.llm as llm

# Semáforo para evitar execução simultânea do agendador
_scheduler_running = False

async def run_subagent_loop(task_prompt: str) -> str:
    """
    Executa um subagente de forma não-interativa (headless).
    Recebe um prompt de tarefa, chama o LLM, executa as ferramentas solicitadas,
    e retorna o log final da execução.
    """
    model = db.get_setting("active_model", "google/gemma-4-31b-qat")
    messages = [
        {"role": "system", "content": llm.SYSTEM_PROMPT},
        {"role": "user", "content": task_prompt}
    ]
    
    log_output = [f"--- Início da execução do subagente ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---"]
    log_output.append(f"Prompt da Tarefa: {task_prompt}")
    
    # Executa até um limite de 5 rodadas de pensamento para evitar loops infinitos
    for iteration in range(5):
        # Chama a LLM de forma não-stream (para background)
        response = llm.chat_completion(model, messages, stream=False)
        log_output.append(f"[LLM Pensamento {iteration+1}]: {response}")
        
        # Analisa se é chamada de ferramenta
        tool_call = llm.parse_tool_call(response)
        if not tool_call:
            # Resposta final em texto, sem chamadas
            log_output.append(f"[Resposta Final do Subagente]: {response}")
            break
            
        tool_name = tool_call.get("tool")
        args = tool_call.get("args", {})
        
        log_output.append(f"[Executando Ferramenta]: {tool_name} com args {args}")
        
        # Executa a ferramenta correspondente
        result = ""
        try:
            from meu_agente_cli.agent import execute_tool_by_name
            from rich.console import Console
            dummy_console = Console(color_system=None, force_terminal=False)
            result = execute_tool_by_name(tool_name, args, dummy_console, allow_interactive=False)
        except Exception as e:
            result = f"Erro na execução da ferramenta '{tool_name}': {str(e)}"
            
        log_output.append(f"[Resultado da Ferramenta]: {result}")
        
        # Alimenta as mensagens com a resposta da ferramenta e o pensamento do LLM
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": f"Resultado da ferramenta {tool_name}: {result}"})
        
    log_output.append(f"--- Fim da execução ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---")
    return "\n".join(log_output)

async def check_and_run_jobs():
    """
    Verifica no banco de dados se há cron jobs vencidos e os executa.
    """
    active_jobs = db.get_active_cron_jobs()
    now = datetime.now()
    
    for job in active_jobs:
        next_run = job["next_run"]
        if now >= next_run:
            job_id = job["id"]
            name = job["name"]
            cron_expr = job["cron_expression"]
            prompt = job["task_prompt"]
            
            # Atualiza o status do cron antes de iniciar (para evitar dupla execução)
            last_run = now
            try:
                iter = croniter(cron_expr, last_run)
                new_next_run = iter.get_next(datetime)
            except Exception:
                # Fallback em caso de erro na expressão cron
                new_next_run = now
                
            db.update_cron_job_runs(job_id, last_run, new_next_run)
            
            # Roda o subagente em segundo plano
            asyncio.create_task(run_and_log_job(job_id, name, prompt))

async def run_and_log_job(job_id: int, name: str, prompt: str):
    """
    Executa a tarefa do subagente e salva o log na base de dados de anotações
    para auditoria do usuário, além de imprimir uma notificação no console.
    """
    logging.info("Cron: Iniciando execução do subagente '%s' (ID Cron: %s)", name, job_id)
    try:
        log = await run_subagent_loop(prompt)
        # Salva o log nas notas para que o usuário possa consultar depois
        db.add_user_note(f"LOG SUBAGENTE '{name}' (ID Cron: {job_id}):\n{log}")
        logging.info("Cron: Subagente '%s' (ID Cron: %s) executado com sucesso.", name, job_id)
        # Notifica discretamente no stderr (ou log do console)
        print(f"\n[CRON] Subagente '{name}' executado com sucesso! Log salvo nas notas.\n> ", end="", file=sys.stderr)
    except Exception as e:
        db.add_user_note(f"LOG SUBAGENTE '{name}' (ID Cron: {job_id}) FALHOU:\n{str(e)}")
        logging.exception("Cron: Subagente '%s' (ID Cron: %s) falhou", name, job_id)
        print(f"\n[CRON ERROR] Subagente '{name}' falhou: {e}\n> ", end="", file=sys.stderr)

async def scheduler_loop():
    """Loop principal do agendador rodando a cada 10 segundos."""
    global _scheduler_running
    _scheduler_running = True
    while _scheduler_running:
        try:
            await check_and_run_jobs()
        except Exception as e:
            logging.exception("Cron: Erro fatal no loop de agendamento")
            print(f"[CRON FATAL] Erro no loop de agendamento: {e}", file=sys.stderr)
        await asyncio.sleep(10)

def start_scheduler():
    """Inicia o agendador em uma thread dedicada com seu próprio event loop."""
    logging.info("Cron: Iniciando o agendador de tarefas em segundo plano...")
    def run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(scheduler_loop())
        
    t = threading.Thread(target=run_in_thread, daemon=True, name="AgenteCronScheduler")
    t.start()

def stop_scheduler():
    global _scheduler_running
    _scheduler_running = False
