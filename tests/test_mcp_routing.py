import unittest
from rich.console import Console
from meu_agente_cli import db, tools, agent, mcp_client

class TestMCPRoutingAndFlowCRM(unittest.TestCase):
    def test_get_mcp_server_flexible_alias(self):
        """Testa se busca por FlowCRM, flow_crm e crm encontram o servidor cadastrado."""
        s1 = db.get_mcp_server("FlowCRM")
        s2 = db.get_mcp_server("flowcrm")
        s3 = db.get_mcp_server("crm")
        self.assertIsNotNone(s1, "Deve encontrar servidor por 'FlowCRM'")
        self.assertIsNotNone(s2, "Deve encontrar servidor por 'flowcrm'")
        self.assertIsNotNone(s3, "Deve encontrar servidor por 'crm'")
        self.assertEqual(s1["id"], s3["id"])

    def test_manage_mcp_tool_list_tools(self):
        """Testa a ação list_tools no manage_mcp_tool."""
        res = tools.manage_mcp_tool(action="list_tools", name="FlowCRM")
        self.assertIn("FERRAMENTAS DO SERVIDOR MCP", res)
        self.assertIn("get_dashboard", res)
        self.assertIn("list_tasks", res)

    def test_sanitize_arguments_id_conversion(self):
        """Testa se campos terminados em _id são convertidos para int mesmo sem tipo explícito."""
        raw_args = {
            "project_id": "42",
            "client_id": "100",
            "title": "Nova Tarefa",
            "notes": ""
        }
        schema = {
            "properties": {
                "project_id": None,
                "client_id": {"description": "ID do cliente"},
                "title": {"type": "string"}
            }
        }
        cleaned = mcp_client.sanitize_arguments(raw_args, schema)
        self.assertEqual(cleaned["project_id"], 42)
        self.assertEqual(cleaned["client_id"], 100)
        self.assertEqual(cleaned["title"], "Nova Tarefa")
        self.assertNotIn("notes", cleaned)

    def test_execute_mcp_tool_routing_variants(self):
        """Testa execução de ferramenta via execute_mcp_tool com diferentes variações de nome."""
        # 1. Nome nativo
        res_native = tools.execute_mcp_tool("get_dashboard", {})
        self.assertIn("visao_geral_ano", res_native)

        # 2. Prefixo com mcp_crm_
        res_full = tools.execute_mcp_tool("mcp_crm_get_dashboard", {})
        self.assertIn("visao_geral_ano", res_full)

        # 3. Prefixo com mcp_
        res_mcp = tools.execute_mcp_tool("mcp_get_dashboard", {})
        self.assertIn("visao_geral_ano", res_mcp)

    def test_agent_fallback_to_mcp(self):
        """Testa se o despachante do agent executa ferramentas nativas sem prefixo mcp_."""
        console = Console()
        res = agent.execute_tool_by_name("get_dashboard", {}, console, False)
        self.assertIn("visao_geral_ano", str(res))

if __name__ == "__main__":
    unittest.main()
