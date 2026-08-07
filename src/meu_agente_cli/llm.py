import httpx
import json
import re
from typing import List, Dict, Any, Generator, Optional
from meu_agente_cli.config import get_lm_studio_url, clean_string

# Prompt do Sistema que ensina o LLM a chamar ferramentas
SYSTEM_PROMPT = """Você é o 'Meu Agente', um assistente virtual inteligente e proativo que roda no terminal Linux (WSL).
Você tem acesso a várias ferramentas para ajudar o usuário.

Instruções para chamadas de ferramentas:
Se o usuário solicitar informações que requerem uma ferramenta (clima, cotações financeiras, notícias, registro financeiro, anotações ou comando CLI do sistema), você deve responder EXCLUSIVAMENTE com um único bloco JSON correspondente, sem qualquer outro texto de conversa.

Formatos de JSON aceitos para cada ferramenta:

1. Clima:
{
  "tool": "get_weather",
  "args": {"city_name": "Nome da Cidade"}
}

2. Cotações Financeiras (Ações ou Câmbio):
{
  "tool": "get_financial_quote",
  "args": {"symbol": "Ticker ou Moeda (ex: PETR4.SA, USDBRL=X, IBOV)"}
}

3. Notícias:
{
  "tool": "get_news",
  "args": {"category": "economia | tecnologia | geral | ciencia"}
}

4. Finanças Pessoais (Lançamentos individuais, em lote, extrato ou resumo):
- Para um único lançamento (especifique due_date no formato YYYY-MM-DD se houver data de vencimento informada):
{
  "tool": "finance_tool",
  "args": {
    "action": "add_receita | add_despesa",
    "category": "categoria (ex: Alimentação, Transporte)",
    "amount": 150.50,
    "description": "descrição [opcional]",
    "due_date": "YYYY-MM-DD [opcional]"
  }
}
- Para múltiplos lançamentos de uma vez (especifique due_date no formato YYYY-MM-DD se houver data de vencimento informada):
{
  "tool": "finance_tool",
  "args": {
    "action": "add_bulk",
    "items": [
      {"type": "receita | despesa", "category": "categoria", "amount": 100.0, "description": "descrição", "due_date": "YYYY-MM-DD [opcional]"},
      {"type": "despesa", "category": "categoria", "amount": 50.0, "description": "descrição", "due_date": "YYYY-MM-DD [opcional]"}
    ]
  }
}
- Para deletar lançamentos incorretos ou duplicados (use record_id para excluir um único ID, ou record_ids como lista [ID1, ID2, ...] para excluir vários de uma só vez):
{
  "tool": "finance_tool",
  "args": {
    "action": "delete",
    "record_id": 123,  // Para um único ID [opcional]
    "record_ids": [3, 4, 5]  // Para múltiplos IDs [opcional]
  }
}
- Para extrato ou resumo:
{
  "tool": "finance_tool",
  "args": {
    "action": "extrato | resumo"
  }
}

5. Anotações (Adicionar, buscar, listar ou deletar notas da memória):
{
  "tool": "notes_tool",
  "args": {
    "action": "add | search | list | delete",
    "content": "conteúdo da nota a ser salva [opcional]",
    "query": "termo de busca [opcional]",
    "note_id": 123
  }
}

6. Comando de Terminal CLI (Executar comandos do Linux WSL):
{
  "tool": "execute_cli_command",
  "args": {"command": "comando a ser executado no shell (ex: df -h, ls -la)"}
}

7. Calculadora (Sempre use para realizar contas matemáticas, somas de despesas/valores ou operações aritméticas de forma precisa):
- Para uma única expressão:
{
  "tool": "calculator_tool",
  "args": {"expression": "expressão (ex: (1410.67 + 2373.24) * 0.15)"}
}
- Para múltiplas expressões de uma vez (lote/bulk):
{
  "tool": "calculator_tool",
  "args": {
    "expressions": {
      "id_conta_1": "1410.67 + 2373.24",
      "id_conta_2": "125.49 * 0.15"
    }
  }
}

Importante:
- Se você puder responder diretamente sem ferramentas (ex: dúvidas de programação, papo furado, piadas, explicações de comandos), responda com texto markdown simples.
- NUNCA misture explicações em texto com o bloco JSON. Responda apenas com o JSON se for usar uma ferramenta.
- Mantenha suas respostas diretas e concisas.
- Sempre que responder diretamente em texto ao usuário, utilize emoticons/emojis (como 🤖, 💡, 🌤️, 📈, 📝, 💰) de forma natural para tornar as respostas mais expressivas, legíveis e amigáveis.
"""

def test_lm_studio_connection() -> bool:
    """Testa a conexão com o LM Studio local."""
    url = get_lm_studio_url()
    try:
        res = httpx.get(f"{url}/v1/models", timeout=5.0)
        return res.status_code == 200
    except Exception:
        return False

def get_available_models() -> List[str]:
    """Retorna os IDs de modelos carregados no LM Studio."""
    url = get_lm_studio_url()
    try:
        res = httpx.get(f"{url}/v1/models", timeout=5.0)
        if res.status_code == 200:
            data = res.json()
            return [model["id"] for model in data.get("data", [])]
    except Exception:
        pass
    return []

def chat_completion(
    model: str, 
    messages: List[Dict[str, str]], 
    stream: bool = True
) -> Generator[str, None, None] | str:
    """
    Executa a chamada ao LM Studio.
    Se stream=True, retorna um gerador que faz o streaming dos tokens de resposta.
    Se stream=False, retorna a string completa da resposta.
    """
    url = get_lm_studio_url()
    
    # Adiciona o system prompt se não houver um nas mensagens
    has_system = any(m["role"] == "system" for m in messages)
    
    # Sanitiza todas as mensagens enviadas para a LLM, prevenindo erros de surrogate no payload JSON
    payload_messages = []
    for m in messages:
        payload_messages.append({
            "role": m["role"],
            "content": clean_string(m["content"])
        })
        
    if not has_system:
        payload_messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        
    payload = {
        "model": model,
        "messages": payload_messages,
        "temperature": 0.3, # Baixa temperatura para manter chamadas de ferramentas consistentes
    }
    
    if stream:
        payload["stream"] = True
        
        def stream_generator():
            try:
                with httpx.stream("POST", f"{url}/v1/chat/completions", json=payload, timeout=60.0) as r:
                    if r.status_code != 200:
                        yield f"[ERROR: LM Studio returned status code {r.status_code}]"
                        return
                    for line in r.iter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                content = data["choices"][0]["delta"].get("content", "")
                                if content:
                                    # Sanitiza o conteúdo retornado pela LLM antes de repassar
                                    yield clean_string(content)
                            except Exception:
                                pass
            except Exception as e:
                # Garante que a mensagem de erro também esteja limpa de surrogates
                yield clean_string(f"[ERROR: Conexão perdida com LM Studio: {e}]")
                
        return stream_generator()
    else:
        payload["stream"] = False
        try:
            res = httpx.post(f"{url}/v1/chat/completions", json=payload, timeout=60.0)
            if res.status_code == 200:
                raw_content = res.json()["choices"][0]["message"].get("content", "")
                return clean_string(raw_content)
            return clean_string(f"Erro ao chamar LLM: Status {res.status_code}")
        except Exception as e:
            return clean_string(f"Falha na conexão com o LLM no LM Studio: {str(e)}")

def parse_tool_call(response_text: str) -> Optional[Dict[str, Any]]:
    """
    Analisa se a resposta do LLM contém uma chamada de ferramenta no formato JSON.
    Aceita JSON puro ou encapsulado em blocos de código markdown.
    """
    text = response_text.strip()
    if not text:
        return None
        
    # Tenta encontrar bloco de código markdown
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not match:
        # Se não achou bloco markdown, procura a primeira ocorrência de { e a última de }
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        
    if match:
        try:
            parsed = json.loads(match.group(1))
            if "tool" in parsed:
                return parsed
        except Exception:
            pass
            
    return None
