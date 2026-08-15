import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from meu_agente_cli.db import get_connection, get_setting
import meu_agente_cli.llm as llm

# 1. Defina limites de concorrência (ajuste conforme sua API LLM e conexão DB)
MAX_WORKERS = 4  
# Limite a taxa de chamadas se houver rate-limit na LLM: ex: max_workers=3 para GPT-4

# Evento global para controle de interrupção graciosa
shutdown_event = threading.Event()

def resumo_llm(conteudo: str) -> str:
    """
    Gera um resumo estruturado de um alerta de tecnovigilância utilizando a LLM configurada.
    """
    if not conteudo or not str(conteudo).strip():
        return ""

    model = get_setting("active_model", "google/gemma-4-31b-qat")
    prompt = (
        "Resuma o seguinte alerta de tecnovigilância de forma clara, concisa e estruturada, contendo:\n"
        "Identificação do Produto / Equipamento (Nome comercial, fabricante, registro Anvisa, modelos/lotes afetados)\n"
        "Problema Identificado / Risco (O que foi constatado e riscos associados aos pacientes e operadores)\n"
        "Recomendações e Ações (Orientações práticas para serviços de saúde, engenharia clínica e usuários)\n\n"
        f"Conteúdo:\n{conteudo}\n\nInstrução para o resumo: não usar Markdown, nem negrito, nem itálico, nem sublinhado, nem outros decoradores no texto"
    )

    messages = [
        {"role": "system", "content": "Você é um assistente especialista em tecnovigilância hospitalar e engenharia clínica."},
        {"role": "user", "content": prompt}
    ]

    try:
        resumo = llm.chat_completion(model, messages, stream=False)
        return resumo
    except Exception as e:
        raise RuntimeError(f"Erro ao gerar resumo com LLM: {e}")


def process_alert(id_alerta, conteudo):
    if shutdown_event.is_set():
        return

    conn = None
    cur = None
    try:
        # 1. Chama a LLM
        resumo = resumo_llm(conteudo)
        if not resumo or shutdown_event.is_set():
            return

        # Cada thread cria sua própria conexão (psycopg2 não é thread-safe para sharing)
        conn = get_connection()
        cur = conn.cursor()
        
        # 2. Atualiza no banco
        cur.execute("UPDATE tecnovigilancia SET resumo = %s WHERE id = %s", (resumo, id_alerta))
        
        # 3. Commit da thread atual
        conn.commit()
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        if not shutdown_event.is_set():
            raise e
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# 2. Execução paralela com tqdm sincronizado no principal thread
def main():
    alerts_completados = set()

    consulta = get_connection()
    cur = consulta.cursor()
    cur.execute("SELECT id, conteudo FROM tecnovigilancia WHERE resumo IS NULL ORDER BY numero_alerta ASC")
    alertas = cur.fetchall()
    cur.close()
    consulta.close()
    
    if not alertas:
        print("✅ Nenhum alerta pendente de resumo.")
        return

    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    try:
        # Cria futures e mapeia para id_alerta
        futures = {
            executor.submit(process_alert, id_alerta, cnt): id_alerta 
            for (id_alerta, cnt) in alertas
        }
        
        # tqdm gerencia a barreira progressiva de forma thread-safe
        with tqdm(total=len(alertas), desc="Resumindo alertas", unit="alerta") as pbar:
            for future in as_completed(futures):
                if shutdown_event.is_set():
                    break
                id_alerta = futures[future]
                try:
                    future.result()  # Releva exceções que escaparam do process_alert
                    alerts_completados.add(id_alerta)
                except Exception as e:
                    if not shutdown_event.is_set():
                        print(f"\n❌ CRITICO - Alert {id_alerta}: Falha não tratada → {e}")
                
                pbar.update(1)  # Atualiza progressão na main thread
            
        print(f"\n✅ Processamento concluído. {len(alerts_completados)}/{len(alertas)} alertas processados.")
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupção solicitada (Ctrl+C). Cancelando tarefas pendentes...")
        shutdown_event.set()
        # Cancela todas as tarefas na fila que ainda não iniciaram
        executor.shutdown(wait=False, cancel_futures=True)
        print(f"🛑 Execução interrompida com segurança. {len(alerts_completados)}/{len(alertas)} alertas foram salvos no banco.")
        sys.exit(0)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

if __name__ == "__main__":
    main()
