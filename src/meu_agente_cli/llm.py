import httpx
import json
import re
from typing import List, Dict, Any, Generator, Optional
from meu_agente_cli.config import get_lm_studio_url, clean_string

from pathlib import Path

CURRENT_DIR = Path(__file__).parent.resolve()

# Timeout em segundos para chamadas de LLM (aumentado para processamento robusto de documentos e imagens)
LLM_TIMEOUT = 300.0

def build_system_prompt() -> str:
    """Constrói dinamicamente o SYSTEM_PROMPT carregando dados dos arquivos de configuração JSON."""
    try:
        config_path = CURRENT_DIR / "system_prompt_config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
            
        prefix = config_data.get("base_instruction_prefix", "")
        suffix = config_data.get("base_instruction_suffix", "")
        core_tools = config_data.get("core_tools", {})
        
        # Junta todas as ferramentas nativas
        prompt_parts = [prefix]
        for tool_name, desc in core_tools.items():
            prompt_parts.append(desc)
            prompt_parts.append("") # Quebra de linha entre ferramentas
            
        # Tenta carregar ferramentas customizadas dinâmicas do custom_tools.json
        custom_config_path = CURRENT_DIR / "custom_tools" / "custom_tools.json"
        if custom_config_path.exists():
            try:
                with open(custom_config_path, "r", encoding="utf-8") as f_custom:
                    custom_data = json.load(f_custom)
                custom_tools = custom_data.get("tools", {})
                for c_tool_name, c_tool_info in custom_tools.items():
                    instruction = c_tool_info.get("prompt_instruction", "")
                    if instruction:
                        prompt_parts.append(instruction)
                        prompt_parts.append("")
            except Exception as ex:
                print(f"[WARNING] Falha ao carregar ferramentas customizadas no prompt: {ex}")
                
        prompt_parts.append(suffix)
        return "\n".join(prompt_parts)
    except Exception as e:
        print(f"[ERROR] Erro ao carregar configurações de prompt dinâmico: {e}")
        return "Você é o 'Meu Agente'. Ajude o usuário de forma concisa."

def __getattr__(name: str) -> Any:
    if name == "SYSTEM_PROMPT":
        return build_system_prompt()
    raise AttributeError(f"module {__name__} has no attribute {name}")

def test_provider_connection() -> bool:
    """Testa a conexão com o provedor de LLM atualmente ativo."""
    from meu_agente_cli import db
    llm_provider = db.get_setting("llm_provider", "lm_studio")
    active_model = db.get_setting("active_model", "")
    api_key = db.get_setting("provider_api_key", "")
    base_url = db.get_setting("provider_base_url", "")
    
    if llm_provider == "lm_studio":
        url = get_lm_studio_url()
        try:
            res = httpx.get(f"{url}/v1/models", timeout=5.0)
            return res.status_code == 200
        except Exception:
            return False
            
    # Para provedores externos, faremos um ping/request muito simples (uma chamada leve de chat completion de 1 token)
    headers = {"Content-Type": "application/json"}
    
    if llm_provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
        headers["Authorization"] = f"Bearer {api_key}"
        payload = {"model": active_model or "gpt-4o-mini", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
    elif llm_provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/openai/v1/chat/completions"
        headers["Authorization"] = f"Bearer {api_key}"
        payload = {"model": active_model or "gemini-1.5-flash", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
    elif llm_provider == "deepseek":
        url = "https://api.deepseek.com/v1/chat/completions"
        headers["Authorization"] = f"Bearer {api_key}"
        payload = {"model": active_model or "deepseek-chat", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
    elif llm_provider == "qwen":
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        headers["Authorization"] = f"Bearer {api_key}"
        payload = {"model": active_model or "qwen-plus", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
    elif llm_provider == "kimi":
        url = "https://api.moonshot.cn/v1/chat/completions"
        headers["Authorization"] = f"Bearer {api_key}"
        payload = {"model": active_model or "moonshot-v1-8k", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
    elif llm_provider == "custom":
        if not base_url:
            return False
        url = f"{base_url.rstrip('/')}/chat/completions"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {"model": active_model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
    elif llm_provider == "claude":
        url = "https://api.anthropic.com/v1/messages"
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        payload = {"model": active_model or "claude-3-5-sonnet-latest", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
    else:
        return False
        
    try:
        res = httpx.post(url, json=payload, headers=headers, timeout=5.0)
        return res.status_code == 200
    except Exception:
        return False

def test_lm_studio_connection() -> bool:
    """Mantido por compatibilidade legado, delega ao novo test_provider_connection."""
    return test_provider_connection()

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
    Executa a chamada ao provedor de LLM configurado (LM Studio ou provedores externos).
    Se stream=True, retorna um gerador que faz o streaming dos tokens de resposta.
    Se stream=False, retorna a string completa da resposta.
    """
    from meu_agente_cli import db
    llm_provider = db.get_setting("llm_provider", "lm_studio")
    api_key = db.get_setting("provider_api_key", "")
    base_url = db.get_setting("provider_base_url", "")
    
    # Determina a URL base e cabeçalhos com base no provedor
    headers = {
        "Content-Type": "application/json"
    }
    
    if llm_provider == "openai":
        url = "https://api.openai.com/v1"
        headers["Authorization"] = f"Bearer {api_key}"
    elif llm_provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/openai/v1"
        headers["Authorization"] = f"Bearer {api_key}"
    elif llm_provider == "deepseek":
        url = "https://api.deepseek.com/v1"
        headers["Authorization"] = f"Bearer {api_key}"
    elif llm_provider == "qwen":
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        headers["Authorization"] = f"Bearer {api_key}"
    elif llm_provider == "kimi":
        url = "https://api.moonshot.cn/v1"
        headers["Authorization"] = f"Bearer {api_key}"
    elif llm_provider == "custom":
        url = base_url.rstrip('/') if base_url else ""
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    elif llm_provider == "claude":
        url = "https://api.anthropic.com/v1"
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        # Fallback para LM Studio
        url = get_lm_studio_url()
        llm_provider = "lm_studio"
        
    # Adiciona o system prompt se não houver um nas mensagens
    has_system = any(m["role"] == "system" for m in messages)
    
    # Sanitiza todas as mensagens enviadas para a LLM
    payload_messages = []
    for m in messages:
        content = m["content"]
        if isinstance(content, list):
            cleaned_content = []
            for item in content:
                if isinstance(item, dict):
                    cleaned_item = item.copy()
                    if "text" in cleaned_item:
                        cleaned_item["text"] = clean_string(cleaned_item["text"])
                    cleaned_content.append(cleaned_item)
                else:
                    cleaned_content.append(item)
            content = cleaned_content
        else:
            content = clean_string(content)
            
        payload_messages.append({
            "role": m["role"],
            "content": content
        })
        
    if not has_system:
        payload_messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        
    if llm_provider == "claude":
        # Anthropic usa formato próprio de mensagens e system prompt
        system_content = ""
        user_assistant_messages = []
        for m in payload_messages:
            if m["role"] == "system":
                system_content = m["content"]
            else:
                role = m["role"]
                if role not in ["user", "assistant"]:
                    role = "user"
                user_assistant_messages.append({
                    "role": role,
                    "content": m["content"]
                })
        
        payload = {
            "model": model,
            "messages": user_assistant_messages,
            "max_tokens": 4000,
            "temperature": 0.3,
        }
        if system_content:
            payload["system"] = system_content
            
        target_url = f"{url}/messages"
    else:
        # Formato compatível com OpenAI
        payload = {
            "model": model,
            "messages": payload_messages,
            "temperature": 0.3,
        }
        target_url = f"{url}/chat/completions" if llm_provider != "lm_studio" else f"{url}/v1/chat/completions"
        
    if stream:
        payload["stream"] = True
        
        def stream_generator():
            try:
                is_claude = (llm_provider == "claude")
                with httpx.stream("POST", target_url, json=payload, headers=headers, timeout=LLM_TIMEOUT) as r:
                    if r.status_code != 200:
                        try:
                            err_body = r.read().decode('utf-8', errors='ignore')
                            yield clean_string(f"[ERROR: Provedor {llm_provider} retornou status {r.status_code}. Detalhes: {err_body}]")
                        except Exception:
                            yield clean_string(f"[ERROR: Provedor {llm_provider} retornou status {r.status_code}]")
                        return
                    for line in r.iter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                if is_claude:
                                    if data.get("type") == "content_block_delta":
                                        content = data["delta"].get("text", "")
                                        if content:
                                            yield clean_string(content)
                                else:
                                    content = data["choices"][0]["delta"].get("content", "")
                                    if content:
                                        yield clean_string(content)
                            except Exception:
                                pass
            except Exception as e:
                yield clean_string(f"[ERROR: Conexão perdida com o provedor {llm_provider}: {e}]")
                
        return stream_generator()
    else:
        payload["stream"] = False
        try:
            res = httpx.post(target_url, json=payload, headers=headers, timeout=LLM_TIMEOUT)
            if res.status_code == 200:
                data = res.json()
                if llm_provider == "claude":
                    raw_content = data["content"][0].get("text", "")
                else:
                    raw_content = data["choices"][0]["message"].get("content", "")
                return clean_string(raw_content)
            
            try:
                err_detail = res.json()
            except Exception:
                err_detail = res.text
            return clean_string(f"Erro ao chamar LLM ({llm_provider}): Status {res.status_code}. Detalhes: {err_detail}")
        except Exception as e:
            return clean_string(f"Falha na conexão com o LLM do provedor {llm_provider}: {str(e)}")

def extract_json_object(text: str) -> Optional[str]:
    """Encontra o primeiro '{' e procura a correspondente chave '}' de fechamento, lidando com strings e escapes."""
    start_idx = text.find('{')
    if start_idx == -1:
        return None
        
    brace_count = 0
    in_string = False
    escape = False
    
    for idx in range(start_idx, len(text)):
        char = text[idx]
        
        if escape:
            escape = False
            continue
            
        if char == '\\':
            escape = True
            continue
            
        if char == '"':
            in_string = not in_string
            continue
            
        if not in_string:
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    return text[start_idx:idx+1]
                    
    return None

def parse_tool_call(response_text: str) -> Optional[Dict[str, Any]]:
    """
    Analisa se a resposta do LLM contém uma chamada de ferramenta no formato JSON.
    Aceita JSON puro ou encapsulado em blocos de código markdown.
    Garante resiliência mesmo se a LLM gerou JSON com aspas não escapadas ou novas linhas cruas.
    """
    text = response_text.strip()
    if not text:
        return None
        
    # 1. Tenta extrair usando o extrator robusto de chaves casadas
    raw_json = extract_json_object(text)
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict) and "tool" in parsed:
                return parsed
        except Exception:
            pass
            
    # 2. Fallback: tenta encontrar bloco de código markdown por regex
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not match:
        # Procura a primeira ocorrência de { e a última de }
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        
    if match:
        raw_json = match.group(1).strip()
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict) and "tool" in parsed:
                return parsed
        except Exception:
            # Fallback robusto se a LLM gerou JSON com aspas internas não escapadas ou quebras de linha cruas (ex: execute_cli_command)
            try:
                # Tenta extrair a ferramenta e seus argumentos usando regexes mais flexíveis
                tool_match = re.search(r'"tool"\s*:\s*"([^"]+)"', raw_json)
                if tool_match:
                    tool_name = tool_match.group(1)
                    args = {}
                    
                    # Para a ferramenta execute_cli_command, extraímos o command de forma especial
                    if tool_name == "execute_cli_command":
                        cmd_match = re.search(r'"command"\s*:\s*"(.*)"', raw_json, re.DOTALL)
                        if cmd_match:
                            cmd_val = cmd_match.group(1).strip()
                            
                            # Remove chaves de fechamento finais se capturadas no modo ganancioso
                            for suffix in ['"} }', '"} \n}', '"}', '}']:
                                if cmd_val.endswith(suffix):
                                    cmd_val = cmd_val[:-len(suffix)].strip()
                                    break
                                
                            # Trata se a última aspa sobrou no final
                            if cmd_val.endswith('"') and not cmd_val.endswith('\\"'):
                                cmd_val = cmd_val[:-1]
                            
                            # Normaliza quebras de linha e aspas
                            cmd_val = cmd_val.replace('\\n', '\n').replace('\\"', '"')
                            args["command"] = cmd_val
                            
                    # Retorna se conseguiu achar a ferramenta e mapear
                    return {"tool": tool_name, "args": args}
            except Exception:
                pass
                
    return None
