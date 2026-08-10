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
        
    payload = {
        "model": model,
        "messages": payload_messages,
        "temperature": 0.3, # Baixa temperatura para manter chamadas de ferramentas consistentes
    }
    
    if stream:
        payload["stream"] = True
        
        def stream_generator():
            try:
                with httpx.stream("POST", f"{url}/v1/chat/completions", json=payload, timeout=LLM_TIMEOUT) as r:
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
            res = httpx.post(f"{url}/v1/chat/completions", json=payload, timeout=LLM_TIMEOUT)
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
    Garante resiliência mesmo se a LLM gerou JSON com aspas não escapadas ou novas linhas cruas.
    """
    text = response_text.strip()
    if not text:
        return None
        
    # Tenta encontrar bloco de código markdown
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not match:
        # Procura a primeira ocorrência de { e a última de }
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        
    if match:
        raw_json = match.group(1).strip()
        try:
            parsed = json.loads(raw_json)
            if "tool" in parsed:
                return parsed
        except Exception:
            # Fallback robusto se a LLM gerou JSON com aspas internas não escapadas ou quebras de linha cruas
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
                            # Tratamos de trás para frente para limpar os fechamentos do JSON
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
