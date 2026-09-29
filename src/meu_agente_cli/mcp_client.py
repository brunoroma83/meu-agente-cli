import os
import json
import time
import asyncio
import logging
import httpx
from typing import List, Dict, Any, Optional, Tuple
import concurrent.futures

import meu_agente_cli.db as db

logger = logging.getLogger(__name__)

# Cache em memória para ferramentas descobertas dos servidores MCP
# Formato: { "server_name": { "tools": [...], "timestamp": float } }
_MCP_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 300.0  # 5 minutos

# Mapeamento de URLs originais para URLs resolvidas que responderam com sucesso
_RESOLVED_URLS: Dict[str, str] = {}

def get_candidate_urls(url: str) -> List[str]:
    """
    Retorna uma lista ordenada de URLs candidatas para conexão.
    Se estiver em ambiente Docker e a URL contiver 'localhost' ou '127.0.0.1',
    testa tanto a URL original (caso o serviço esteja no próprio container)
    quanto 'host.docker.internal' (caso o serviço esteja no host da máquina/Windows).
    """
    clean_u = url.strip()
    if clean_u in _RESOLVED_URLS:
        return [_RESOLVED_URLS[clean_u]]

    candidates = [clean_u]
    is_docker = os.path.exists("/.dockerenv") or os.environ.get("DB_HOST") == "db"
    if is_docker:
        if "://localhost" in clean_u:
            candidates.append(clean_u.replace("://localhost", "://host.docker.internal"))
        elif "://127.0.0.1" in clean_u:
            candidates.append(clean_u.replace("://127.0.0.1", "://host.docker.internal"))
    return candidates

def build_headers(api_key: Optional[str] = None, custom_headers: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Constrói os cabeçalhos HTTP para autenticação com o servidor MCP."""
    headers = {
        "Accept": "application/json, text/event-stream",
        "User-Agent": "meu-agente-cli-mcp-client/1.0"
    }
    if api_key:
        clean_key = api_key.strip()
        if clean_key:
            headers["Authorization"] = f"Bearer {clean_key}"
            headers["x-api-key"] = clean_key

    if custom_headers:
        for k, v in custom_headers.items():
            headers[str(k)] = str(v)

    return headers

def run_async(coro):
    """Executa uma corrotina assíncrona de forma segura tanto em contextos síncronos quanto com event loops ativos."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)

async def _sse_list_tools(target_url: str, headers: Dict[str, str], timeout: float = 10.0) -> List[Dict[str, Any]]:
    """Conecta ao servidor MCP via SSE e lista as ferramentas disponíveis."""
    from mcp.client.sse import sse_client
    from mcp.client.session import ClientSession

    async with sse_client(target_url, headers=headers, timeout=timeout) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            tools_list = []
            for t in result.tools:
                schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", {})
                if hasattr(schema, "model_dump"):
                    schema = schema.model_dump()
                tools_list.append({
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": schema or {}
                })
            return tools_list

async def _http_jsonrpc_list_tools(target_url: str, headers: Dict[str, str], timeout: float = 10.0) -> List[Dict[str, Any]]:
    """Fallback: Envia requisição JSON-RPC direta via HTTP POST para servidores MCP que não utilizam SSE puro."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {}
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(target_url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Erro JSON-RPC: {data['error']}")
        result = data.get("result", {})
        raw_tools = result.get("tools", [])
        tools_list = []
        for t in raw_tools:
            tools_list.append({
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "input_schema": t.get("inputSchema", {}) or t.get("input_schema", {})
            })
        return tools_list

async def _sse_call_tool(target_url: str, tool_name: str, arguments: dict, headers: Dict[str, str], timeout: float = 60.0) -> str:
    """Executa uma ferramenta no servidor MCP via SSE."""
    from mcp.client.sse import sse_client
    from mcp.client.session import ClientSession

    async with sse_client(target_url, headers=headers, timeout=timeout) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            res = await session.call_tool(tool_name, arguments)
            
            output_parts = []
            if hasattr(res, "content"):
                for block in res.content:
                    if hasattr(block, "text"):
                        output_parts.append(block.text)
                    elif isinstance(block, dict) and "text" in block:
                        output_parts.append(block["text"])
                    else:
                        output_parts.append(str(block))
            else:
                output_parts.append(str(res))

            response_str = "\n".join(output_parts).strip()
            if getattr(res, "isError", False):
                return f"[ERRO MCP] {response_str}"
            return response_str or "Sucesso: Ferramenta MCP executada sem saída textual."

async def _http_jsonrpc_call_tool(target_url: str, tool_name: str, arguments: dict, headers: Dict[str, str], timeout: float = 60.0) -> str:
    """Fallback: Executa uma ferramenta via JSON-RPC POST."""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(target_url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return f"[ERRO MCP] {data['error']}"
        result = data.get("result", {})
        content = result.get("content", [])
        texts = []
        for c in content:
            if isinstance(c, dict) and "text" in c:
                texts.append(c["text"])
            else:
                texts.append(str(c))
        return "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)

def test_mcp_connection(url: str, api_key: Optional[str] = None, transport: str = "sse", headers: Optional[dict] = None) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """
    Testa a conexão com o servidor MCP e retorna a lista de ferramentas encontradas.
    Testa candidatos (ex: localhost e host.docker.internal no Docker) até obter sucesso.
    Retorna (sucesso: bool, mensagem: str, ferramentas: list).
    """
    req_headers = build_headers(api_key, headers)
    candidates = get_candidate_urls(url)
    last_err = ""

    for candidate in candidates:
        # 1. Tenta SSE
        if transport == "sse" or "/sse" in candidate.lower():
            try:
                tools = run_async(_sse_list_tools(candidate, req_headers))
                _RESOLVED_URLS[url.strip()] = candidate
                return True, f"Conexão SSE estabelecida com sucesso. {len(tools)} ferramentas localizadas.", tools
            except Exception as e_sse:
                last_err = f"SSE: {e_sse}"
                logger.debug("Tentativa SSE falhou para %s: %s. Tentando JSON-RPC HTTP...", candidate, e_sse)
                try:
                    tools = run_async(_http_jsonrpc_list_tools(candidate, req_headers))
                    _RESOLVED_URLS[url.strip()] = candidate
                    return True, f"Conexão HTTP JSON-RPC estabelecida. {len(tools)} ferramentas localizadas.", tools
                except Exception as e_http:
                    last_err = f"SSE ({e_sse}) / HTTP ({e_http})"
        else:
            # 2. Tenta HTTP primeiro
            try:
                tools = run_async(_http_jsonrpc_list_tools(candidate, req_headers))
                _RESOLVED_URLS[url.strip()] = candidate
                return True, f"Conexão HTTP JSON-RPC estabelecida. {len(tools)} ferramentas localizadas.", tools
            except Exception as e_http:
                last_err = f"HTTP: {e_http}"
                try:
                    tools = run_async(_sse_list_tools(candidate, req_headers))
                    _RESOLVED_URLS[url.strip()] = candidate
                    return True, f"Conexão SSE estabelecida. {len(tools)} ferramentas localizadas.", tools
                except Exception as e_sse:
                    last_err = f"HTTP ({e_http}) / SSE ({e_sse})"

    return False, f"Falha ao conectar em todas as tentativas ({last_err})", []

def fetch_server_tools(server_data: Dict[str, Any], force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Obtém as ferramentas de um servidor MCP específico com cache em memória."""
    name = server_data["name"]
    now = time.time()
    
    if not force_refresh and name in _MCP_CACHE:
        cached = _MCP_CACHE[name]
        if now - cached["timestamp"] < CACHE_TTL_SECONDS:
            return cached["tools"]

    url = server_data["url"]
    api_key = server_data.get("api_key")
    transport = server_data.get("transport", "sse")
    headers = server_data.get("headers", {})

    success, msg, tools = test_mcp_connection(url, api_key, transport, headers)
    if success:
        _MCP_CACHE[name] = {
            "tools": tools,
            "timestamp": now
        }
        return tools
    else:
        logger.warning("Não foi possível carregar ferramentas do servidor MCP '%s': %s", name, msg)
        return _MCP_CACHE.get(name, {}).get("tools", [])

def sync_all_active_tools(force_refresh: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """Sincroniza ferramentas de todos os servidores MCP ativos no banco de dados."""
    active_servers = db.list_mcp_servers(only_active=True)
    all_tools = {}
    for s in active_servers:
        name = s["name"]
        tools = fetch_server_tools(s, force_refresh=force_refresh)
        all_tools[name] = tools
    return all_tools

def sanitize_arguments(arguments: dict, input_schema: Optional[dict] = None) -> dict:
    """
    Higieniza os argumentos recebidos do LLM antes de enviar ao servidor MCP.
    - Remove parâmetros opcionais enviados como string vazia (""), "null" ou None.
    - Converte tipos numéricos/booleanos se enviados como strings pelo modelo.
    """
    if not isinstance(arguments, dict):
        return {}

    props = input_schema.get("properties", {}) if input_schema else {}
    required = input_schema.get("required", []) if input_schema else []
    
    clean_args = {}
    for k, v in arguments.items():
        prop_def = props.get(k, {})
        expected_type = prop_def.get("type", "")
        
        # Se for vazio ou nulo
        if v is None or v == "" or (isinstance(v, str) and v.strip().lower() in ("null", "none")):
            if k in required and expected_type == "string":
                clean_args[k] = ""
            continue
            
        # Converte tipos se necessário
        if expected_type == "integer" and isinstance(v, str):
            clean_str = v.strip()
            if clean_str.lstrip("-").isdigit():
                clean_args[k] = int(clean_str)
                continue
            elif k not in required:
                continue
        elif expected_type == "number" and isinstance(v, str):
            try:
                clean_args[k] = float(v.strip().replace(",", "."))
                continue
            except ValueError:
                if k not in required:
                    continue
        elif expected_type == "boolean" and isinstance(v, str):
            clean_args[k] = v.strip().lower() in ("true", "1", "yes", "sim")
            continue

        clean_args[k] = v

    return clean_args

def call_mcp_tool(server_name: str, actual_tool_name: str, arguments: dict) -> str:
    """Executa uma ferramenta específica de um servidor MCP cadastrado."""
    server_data = db.get_mcp_server(server_name)
    if not server_data:
        return f"Erro: Servidor MCP '{server_name}' não encontrado no banco de dados."

    if not server_data.get("is_active", True):
        return f"Erro: O servidor MCP '{server_name}' está inativo no momento. Use /mcp toggle {server_name} para ativar."

    # Obtém o schema da ferramenta para higienizar argumentos vazios do LLM
    tools = fetch_server_tools(server_data)
    tool_schema = {}
    for t in tools:
        if t["name"] == actual_tool_name:
            tool_schema = t.get("input_schema", {})
            break

    clean_args = sanitize_arguments(arguments, tool_schema)

    url = server_data["url"]
    api_key = server_data.get("api_key")
    transport = server_data.get("transport", "sse")
    headers = build_headers(api_key, server_data.get("headers", {}))
    candidates = get_candidate_urls(url)
    last_err = ""

    for candidate in candidates:
        if transport == "sse" or "/sse" in candidate.lower():
            try:
                res = run_async(_sse_call_tool(candidate, actual_tool_name, clean_args, headers))
                _RESOLVED_URLS[url.strip()] = candidate
                return res
            except Exception as e_sse:
                last_err = str(e_sse)
                try:
                    res = run_async(_http_jsonrpc_call_tool(candidate, actual_tool_name, clean_args, headers))
                    _RESOLVED_URLS[url.strip()] = candidate
                    return res
                except Exception as e_http:
                    last_err = f"SSE ({e_sse}) / HTTP ({e_http})"
        else:
            try:
                res = run_async(_http_jsonrpc_call_tool(candidate, actual_tool_name, arguments, headers))
                _RESOLVED_URLS[url.strip()] = candidate
                return res
            except Exception as e_http:
                last_err = str(e_http)
                try:
                    res = run_async(_sse_call_tool(candidate, actual_tool_name, arguments, headers))
                    _RESOLVED_URLS[url.strip()] = candidate
                    return res
                except Exception as e_sse:
                    last_err = f"HTTP ({e_http}) / SSE ({e_sse})"

    return f"Erro ao executar ferramenta '{actual_tool_name}' no servidor MCP '{server_name}': {last_err}"

def format_tool_schema_to_json_example(tool_name_full: str, input_schema: dict) -> str:
    """Formata o esquema de entrada JSON da tool MCP no padrão JSON do prompt do agente."""
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])

    args_example = {}
    for prop_name, prop_def in properties.items():
        prop_type = prop_def.get("type", "string")
        prop_desc = prop_def.get("description", "")
        req_str = "obrigatório" if prop_name in required else "opcional"
        if prop_desc:
            args_example[prop_name] = f"[{prop_type}] {prop_desc} ({req_str})"
        else:
            args_example[prop_name] = f"[{prop_type}] ({req_str})"

    example_dict = {
        "tool": tool_name_full,
        "args": args_example
    }
    return json.dumps(example_dict, indent=2, ensure_ascii=False)

def get_mcp_prompt_instructions() -> List[str]:
    """Gera os blocos de instruções de ferramentas MCP para injeção no SYSTEM_PROMPT do LLM."""
    instructions = []
    active_servers = db.list_mcp_servers(only_active=True)
    if not active_servers:
        return instructions

    for s in active_servers:
        server_name = s["name"]
        tools = fetch_server_tools(s)
        if not tools:
            continue

        instructions.append(f"\n--- FERRAMENTAS MCP CONECTADAS: Servidor '{server_name.upper()}' ---")
        instructions.append(
            f"DIRETRIZES MANDATÓRIAS PARA FERRAMENTAS DO {server_name.upper()}:\n"
            f"1. CONSULTAS E LISTAGENS: Sempre que o usuário pedir para ver, listar, buscar ou consultar tarefas, clientes, reuniões, dashboard ou contatos do CRM, você DEVE responder EXCLUSIVAMENTE com o bloco JSON da ferramenta correspondente (ex: 'mcp_{server_name}_list_tasks', 'mcp_{server_name}_list_clients', 'mcp_{server_name}_get_dashboard'). É TERMINANTEMENTE PROIBIDO inventar tarefas ou clientes fictícios em texto.\n"
            "2. HUMAN-IN-THE-LOOP: Para ações de criação ou modificação, se faltar algum parâmetro obrigatório, NÃO invente dados. Pergunte ao usuário o que falta e aguarde a resposta.\n"
        )

        for t in tools:
            t_name = t["name"]
            t_desc = t.get("description", "")
            t_schema = t.get("input_schema", {})
            full_name = f"mcp_{server_name}_{t_name}"

            json_example = format_tool_schema_to_json_example(full_name, t_schema)
            instruction = f"• {full_name} ({t_desc or 'Operação no ' + server_name}):\n{json_example}\n"
            instructions.append(instruction)

    return instructions
