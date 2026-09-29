import httpx
import json
import re
from typing import List, Dict, Any, Generator, Optional
from meu_agente_cli.config import get_lm_studio_url, clean_string

from pathlib import Path

CURRENT_DIR = Path(__file__).parent.resolve()

# Timeout em segundos para chamadas de LLM (aumentado para processamento robusto de documentos e imagens)
LLM_TIMEOUT = 300.0

def build_system_prompt(agent_slug: Optional[str] = None) -> str:
    """Constrói dinamicamente o SYSTEM_PROMPT carregando dados do agente ativo do banco e ferramentas."""
    try:
        from meu_agente_cli import db

        if not agent_slug:
            agent_slug = db.get_active_agent_slug()

        agent_data = db.get_agent(agent_slug)
        if not agent_data:
            agent_data = db.get_agent("geral")

        config_path = CURRENT_DIR / "system_prompt_config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
            
        suffix = config_data.get("base_instruction_suffix", "")
        core_tools = config_data.get("core_tools", {})
        
        # 1. Identidade e Instruções do Agente Ativo
        prompt_parts = []
        
        # Injeção de Data e Hora Atual do Sistema para contextualizar cálculos de tempo
        from datetime import datetime, timezone, timedelta
        tz_br = timezone(timedelta(hours=-3))
        now = datetime.now(tz_br)
        dias_semana = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
        dia_nome = dias_semana[now.weekday()]
        prompt_parts.append(f"📅 DATA E HORA ATUAL DO SISTEMA: {dia_nome}, {now.strftime('%d/%m/%Y, %H:%M')} (Horário de Brasília/Local). Utilize sempre esta data de referência ao calcular períodos, compromissos ou datas relativas (ex: hoje, amanhã, semana que vem).")
        prompt_parts.append("")

        if agent_data:
            agent_header = (
                f"=== IDENTIDADE DO AGENTE ATIVO: {agent_data['icon']} {agent_data['name']} (`{agent_data['slug']}`) ===\n"
                f"{agent_data['system_prompt']}\n"
                f"============================================================"
            )
            prompt_parts.append(agent_header)
        else:
            prefix = config_data.get("base_instruction_prefix", "")
            prompt_parts.append(prefix)

        prompt_parts.append("\nInstruções para chamadas de ferramentas:")
        prompt_parts.append("Se o usuário solicitar informações que requerem uma ferramenta, responda EXCLUSIVAMENTE com um único bloco JSON correspondente, sem qualquer outro texto de conversa.\nFormatos de JSON aceitos:\n")

        # 2. Filtragem de Ferramentas (se o agente possuir whitelist definida)
        allowed = agent_data.get("allowed_tools") if agent_data else None
        
        for tool_name, desc in core_tools.items():
            if allowed is None or tool_name in allowed:
                prompt_parts.append(desc)
                prompt_parts.append("") # Quebra de linha entre ferramentas
            
        # 3. Ferramentas Customizadas Dinâmicas (custom_tools.json)
        custom_config_path = CURRENT_DIR / "custom_tools" / "custom_tools.json"
        if custom_config_path.exists():
            try:
                with open(custom_config_path, "r", encoding="utf-8") as f_custom:
                    custom_data = json.load(f_custom)
                custom_tools = custom_data.get("tools", {})
                for c_tool_name, c_tool_info in custom_tools.items():
                    if allowed is None or c_tool_name in allowed:
                        instruction = c_tool_info.get("prompt_instruction", "")
                        if instruction:
                            prompt_parts.append(instruction)
                            prompt_parts.append("")
            except Exception as ex:
                print(f"[WARNING] Falha ao carregar ferramentas customizadas no prompt: {ex}")

        # 3.1. Ferramentas Dinâmicas de Servidores MCP (Model Context Protocol)
        try:
            from meu_agente_cli import mcp_client
            mcp_instructions = mcp_client.get_mcp_prompt_instructions()
            for instruction in mcp_instructions:
                prompt_parts.append(instruction)
        except Exception as ex:
            print(f"[WARNING] Falha ao carregar ferramentas MCP no prompt: {ex}")

        # 4. Inserção de Informações de Perfil do Usuário
        try:
            logged_user = db.get_logged_in_user()
            if logged_user:
                profile_data = db.get_user_profile(user_name=logged_user)
                if profile_data:
                    profile_section = [
                        f"\n=== PERFIL DO USUÁRIO ({logged_user}) ===",
                        "Use as informações abaixo para personalizar suas respostas, adaptando-as às preferências, rotina, estudos e contexto do usuário:"
                    ]
                    for cat, content in profile_data.items():
                        profile_section.append(f"- {cat.capitalize()}: {content}")
                    profile_section.append("=========================\n")
                    prompt_parts.append("\n".join(profile_section))
        except Exception as ex:
            print(f"[WARNING] Falha ao carregar perfil do usuário no prompt: {ex}")

        prompt_parts.append(suffix)
        return "\n".join(prompt_parts)
    except Exception as e:
        print(f"[ERROR] Erro ao carregar configurações de prompt dinâmico: {e}")
        return "Você é o 'Meu Agente'. Ajude o usuário de forma concisa."

def __getattr__(name: str) -> Any:
    if name == "SYSTEM_PROMPT":
        return build_system_prompt()
    raise AttributeError(f"module {__name__} has no attribute {name}")

class LLMProviderError(Exception):
    """Exceção levantada quando uma chamada a um provedor de LLM falha."""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code

_failover_callback = None

def set_failover_callback(cb):
    """Define um callback chamado quando ocorre failover: cb(primary_prov, primary_model, backup_prov, backup_model, error_reason)."""
    global _failover_callback
    _failover_callback = cb

def notify_failover(p_prov: str, p_mod: str, b_prov: str, b_mod: str, reason: str):
    """Notifica nos logs e via callback sobre o failover acionado."""
    import logging
    logging.warning("⚠️ Failover de LLM acionado! Principal (%s - %s) falhou: %s. Alternando para Backup (%s - %s).",
                    p_prov, p_mod, reason, b_prov, b_mod)
    if _failover_callback:
        try:
            _failover_callback(p_prov, p_mod, b_prov, b_mod, reason)
        except Exception:
            pass

def _resolve_provider_target(provider: str, api_key: str, base_url: str):
    """Retorna (provider, url, headers, is_claude) para o provedor dado."""
    headers = {"Content-Type": "application/json"}
    is_claude = False
    
    if provider == "openai":
        url = "https://api.openai.com/v1"
        headers["Authorization"] = f"Bearer {api_key}"
    elif provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/openai/v1"
        headers["Authorization"] = f"Bearer {api_key}"
    elif provider == "deepseek":
        url = "https://api.deepseek.com/v1"
        headers["Authorization"] = f"Bearer {api_key}"
    elif provider == "qwen":
        url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        headers["Authorization"] = f"Bearer {api_key}"
    elif provider == "kimi":
        url = "https://api.moonshot.cn/v1"
        headers["Authorization"] = f"Bearer {api_key}"
    elif provider == "custom":
        url = base_url.rstrip('/') if base_url else ""
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    elif provider == "claude":
        url = "https://api.anthropic.com/v1"
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        is_claude = True
    elif provider == "cloudflare":
        api_key = api_key or os.environ.get("CLOUDFLARE_API_TOKEN", "")
        account_id = base_url or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run"
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        # Fallback para LM Studio
        url = get_lm_studio_url()
        provider = "lm_studio"
        
    return provider, url, headers, is_claude

def test_provider_connection(target: str = "primary") -> bool:
    """
    Testa a conexão com o provedor de LLM configurado ('primary' ou 'backup').
    """
    from meu_agente_cli import db
    if target == "backup":
        cfg = db.get_backup_llm_config()
        llm_provider = cfg.get("provider", "")
        active_model = cfg.get("model", "")
        api_key = cfg.get("api_key", "")
        base_url = cfg.get("base_url", "")
        if not llm_provider:
            return False
    else:
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
    elif llm_provider == "cloudflare":
        api_key = api_key or os.environ.get("CLOUDFLARE_API_TOKEN", "")
        account_id = base_url or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        if not account_id or not api_key:
            return False
        model_name = active_model or "@cf/qwen/qwen3-30b-a3b-fp8"
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model_name}"
        headers["Authorization"] = f"Bearer {api_key}"
        payload = {"messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
        
    try:
        res = httpx.post(url, json=payload, headers=headers, timeout=5.0)
        return res.status_code == 200
    except Exception:
        return False

def test_backup_provider_connection() -> bool:
    """Testa a conexão com o provedor de LLM configurado como backup."""
    return test_provider_connection(target="backup")

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

def _prepare_payload_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    has_system = any(m.get("role") == "system" for m in messages)
    payload_messages = []
    for m in messages:
        content = m.get("content", "")
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
        payload_messages.append({"role": m.get("role", "user"), "content": content})
    if not has_system:
        payload_messages.insert(0, {"role": "system", "content": build_system_prompt()})
    return payload_messages

def _execute_single_provider(
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    payload_messages: List[Dict[str, Any]],
    stream: bool
) -> Generator[str, None, None] | str:
    prov, url, headers, is_claude = _resolve_provider_target(provider, api_key, base_url)
    
    if is_claude:
        system_content = ""
        user_assistant_messages = []
        for m in payload_messages:
            if m["role"] == "system":
                system_content = m["content"]
            else:
                role = m["role"]
                if role not in ["user", "assistant"]:
                    role = "user"
                user_assistant_messages.append({"role": role, "content": m["content"]})
        payload = {
            "model": model,
            "messages": user_assistant_messages,
            "max_tokens": 4000,
            "temperature": 0.3,
        }
        if system_content:
            payload["system"] = system_content
        target_url = f"{url}/messages"
    elif prov == "cloudflare":
        payload = {
            "messages": payload_messages,
            "temperature": 0.3,
        }
        target_url = f"{url}/{model}"
    else:
        payload = {
            "model": model,
            "messages": payload_messages,
            "temperature": 0.3,
        }
        target_url = f"{url}/chat/completions" if prov != "lm_studio" else f"{url}/v1/chat/completions"

    if stream:
        payload["stream"] = True
        
        def stream_generator():
            client = httpx.Client(timeout=LLM_TIMEOUT)
            try:
                request = client.build_request("POST", target_url, json=payload, headers=headers)
                response = client.send(request, stream=True)
                if response.status_code != 200:
                    try:
                        err_body = response.read().decode('utf-8', errors='ignore')
                    except Exception:
                        err_body = ""
                    response.close()
                    client.close()
                    raise LLMProviderError(f"Provedor {prov} retornou status {response.status_code}. Detalhes: {err_body}", status_code=response.status_code)
                    
                for line in response.iter_lines():
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
                            elif prov == "cloudflare":
                                c = ""
                                if "response" in data and isinstance(data["response"], str) and data["response"]:
                                    c = data["response"]
                                elif "choices" in data and len(data["choices"]) > 0:
                                    c = data["choices"][0].get("delta", {}).get("content", "")
                                if c:
                                    yield clean_string(c)
                            else:
                                content = data["choices"][0]["delta"].get("content", "")
                                if content:
                                    yield clean_string(content)
                        except Exception:
                            pass
                response.close()
            except Exception as e:
                client.close()
                if isinstance(e, LLMProviderError):
                    raise
                raise LLMProviderError(f"Falha na conexão com {prov}: {e}")
            finally:
                client.close()

        return stream_generator()
    else:
        payload["stream"] = False
        try:
            res = httpx.post(target_url, json=payload, headers=headers, timeout=LLM_TIMEOUT)
            if res.status_code == 200:
                data = res.json()
                if is_claude:
                    raw_content = data["content"][0].get("text", "")
                elif prov == "cloudflare":
                    result = data.get("result", {})
                    if isinstance(result, dict):
                        if "choices" in result and len(result["choices"]) > 0:
                            raw_content = result["choices"][0].get("message", {}).get("content", "")
                        elif "response" in result:
                            raw_content = result["response"]
                        else:
                            raw_content = str(result)
                    elif isinstance(result, str):
                        raw_content = result
                    else:
                        raw_content = data.get("response", "")
                else:
                    raw_content = data["choices"][0]["message"].get("content", "")
                return clean_string(raw_content)
            try:
                err_detail = res.json()
            except Exception:
                err_detail = res.text
            raise LLMProviderError(f"Status {res.status_code}. Detalhes: {err_detail}", status_code=res.status_code)
        except Exception as e:
            if isinstance(e, LLMProviderError):
                raise
            raise LLMProviderError(f"Falha na conexão com {prov}: {e}")

def chat_completion(
    model: str, 
    messages: List[Dict[str, str]], 
    stream: bool = True
) -> Generator[str, None, None] | str:
    """
    Executa a chamada ao provedor de LLM configurado.
    Caso o provedor principal falhe e um LLM de backup esteja habilitado,
    emite aviso transparente e executa automaticamente no modelo alternativo.
    """
    from meu_agente_cli import db
    primary_provider = db.get_setting("llm_provider", "lm_studio")
    primary_key = db.get_setting("provider_api_key", "")
    primary_url = db.get_setting("provider_base_url", "")
    primary_model = model or db.get_setting("active_model", "google/gemma-4-31b-qat")
    
    payload_messages = _prepare_payload_messages(messages)
    
    if stream:
        def stream_failover_wrapper():
            backup_cfg = db.get_backup_llm_config()
            has_backup = backup_cfg.get("enabled") and bool(backup_cfg.get("provider"))
            
            # Tenta executar no provedor principal
            try:
                primary_gen = _execute_single_provider(
                    primary_provider, primary_model, primary_key, primary_url, payload_messages, stream=True
                )
                first_chunk = next(primary_gen, None)
                if first_chunk is not None:
                    yield first_chunk
                for chunk in primary_gen:
                    yield chunk
                return
            except Exception as err_primary:
                err_reason = str(err_primary)
                if not has_backup:
                    yield clean_string(f"[ERROR: Provedor {primary_provider} falhou: {err_reason}]")
                    return
                
                # Failover acionado
                b_prov = backup_cfg["provider"]
                b_mod = backup_cfg["model"] or "default"
                b_key = backup_cfg["api_key"]
                b_url = backup_cfg["base_url"]
                notify_failover(primary_provider, primary_model, b_prov, b_mod, err_reason)
                
                # Avisa o usuário de forma visível
                banner = f"\n⚠️ [AVISO: LLM Principal ({primary_provider.upper()} - {primary_model}) falhou: {err_reason}. Alternando para LLM de Backup: {b_prov.upper()} - {b_mod}...]\n\n"
                yield clean_string(banner)
                
                # Executa no backup
                try:
                    backup_gen = _execute_single_provider(
                        b_prov, b_mod, b_key, b_url, payload_messages, stream=True
                    )
                    for chunk in backup_gen:
                        yield chunk
                except Exception as err_backup:
                    yield clean_string(f"\n[ERROR: Falha também no LLM de Backup ({b_prov}): {err_backup}]")

        return stream_failover_wrapper()
    else:
        backup_cfg = db.get_backup_llm_config()
        has_backup = backup_cfg.get("enabled") and bool(backup_cfg.get("provider"))
        
        try:
            return _execute_single_provider(
                primary_provider, primary_model, primary_key, primary_url, payload_messages, stream=False
            )
        except Exception as err_primary:
            err_reason = str(err_primary)
            if not has_backup:
                return clean_string(f"Erro ao chamar LLM ({primary_provider}): {err_reason}")
                
            b_prov = backup_cfg["provider"]
            b_mod = backup_cfg["model"] or "default"
            b_key = backup_cfg["api_key"]
            b_url = backup_cfg["base_url"]
            notify_failover(primary_provider, primary_model, b_prov, b_mod, err_reason)
            
            banner = f"⚠️ [AVISO: LLM Principal ({primary_provider.upper()} - {primary_model}) falhou: {err_reason}. Alternando para LLM de Backup: {b_prov.upper()} - {b_mod}...]\n\n"
            try:
                res_backup = _execute_single_provider(
                    b_prov, b_mod, b_key, b_url, payload_messages, stream=False
                )
                return banner + res_backup
            except Exception as err_backup:
                return clean_string(f"Falha tanto no LLM Principal ({primary_provider}: {err_reason}) quanto no Backup ({b_prov}: {err_backup})")


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
                    elif tool_name == "tts_tool":
                        text_match = re.search(r'"text"\s*:\s*"(.*)"', raw_json, re.DOTALL)
                        if text_match:
                            text_val = text_match.group(1).strip()
                            for suffix in ['"} }', '"} \n}', '"}', '}']:
                                if text_val.endswith(suffix):
                                    text_val = text_val[:-len(suffix)].strip()
                                    break
                            if text_val.endswith('"') and not text_val.endswith('\\"'):
                                text_val = text_val[:-1]
                            text_val = text_val.replace('\\n', '\n').replace('\\"', '"')
                            args["text"] = text_val
                        voice_match = re.search(r'"voice"\s*:\s*"([^"]+)"', raw_json)
                        if voice_match:
                            args["voice"] = voice_match.group(1)
                        title_match = re.search(r'"title"\s*:\s*"([^"]+)"', raw_json)
                        if title_match:
                            args["title"] = title_match.group(1)
                            
                    # Retorna se conseguiu achar a ferramenta e mapear
                    return {"tool": tool_name, "args": args}
            except Exception:
                pass
                
    return None
