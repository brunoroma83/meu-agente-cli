import unittest
from unittest.mock import patch, MagicMock
from meu_agente_cli import db, llm

class TestLLMBackupAndFailover(unittest.TestCase):
    def setUp(self):
        # Salva o estado original das configurações
        self.orig_primary_provider = db.get_setting("llm_provider", "lm_studio")
        self.orig_active_model = db.get_setting("active_model", "google/gemma-4-31b-qat")
        self.orig_backup_cfg = db.get_backup_llm_config()

    def tearDown(self):
        # Restaura o estado original
        db.set_setting("llm_provider", self.orig_primary_provider)
        db.set_setting("active_model", self.orig_active_model)
        db.set_backup_llm_config(
            enabled=self.orig_backup_cfg["enabled"],
            provider=self.orig_backup_cfg["provider"],
            model=self.orig_backup_cfg["model"],
            api_key=self.orig_backup_cfg["api_key"],
            base_url=self.orig_backup_cfg["base_url"]
        )

    def test_backup_config_persistence(self):
        """Valida salvamento e recuperação das configurações de backup no banco."""
        db.set_backup_llm_config(
            enabled=True,
            provider="gemini",
            model="gemini-1.5-flash",
            api_key="test-api-key-123",
            base_url=""
        )
        cfg = db.get_backup_llm_config()
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["provider"], "gemini")
        self.assertEqual(cfg["model"], "gemini-1.5-flash")
        self.assertEqual(cfg["api_key"], "test-api-key-123")

    @patch("meu_agente_cli.llm._execute_single_provider")
    def test_failover_stream_false(self, mock_execute):
        """Valida se o failover automático funciona para chamadas não-streaming."""
        db.set_setting("llm_provider", "openai")
        db.set_setting("active_model", "gpt-4o")
        db.set_backup_llm_config(
            enabled=True,
            provider="gemini",
            model="gemini-1.5-flash",
            api_key="gemini-key",
            base_url=""
        )

        failover_events = []
        def on_failover(p_prov, p_mod, b_prov, b_mod, reason):
            failover_events.append((p_prov, p_mod, b_prov, b_mod, reason))

        llm.set_failover_callback(on_failover)

        # Mock: primário lança LLMProviderError, backup responde com sucesso
        def side_effect(prov, model, api_key, base_url, messages, stream):
            if prov == "openai":
                raise llm.LLMProviderError("Status 429: Quota Exceeded", status_code=429)
            elif prov == "gemini":
                return "Resposta gerada pelo LLM de backup Gemini!"
            return "Outro"

        mock_execute.side_effect = side_effect

        messages = [{"role": "user", "content": "Olá"}]
        result = llm.chat_completion("gpt-4o", messages, stream=False)

        # Verifica se o aviso explícito foi gerado na resposta
        self.assertIn("⚠️ [AVISO: LLM Principal (OPENAI - gpt-4o) falhou", result)
        self.assertIn("Alternando para LLM de Backup: GEMINI - gemini-1.5-flash", result)
        self.assertIn("Resposta gerada pelo LLM de backup Gemini!", result)

        # Verifica se o callback de failover foi disparado
        self.assertEqual(len(failover_events), 1)
        self.assertEqual(failover_events[0][0], "openai")
        self.assertEqual(failover_events[0][2], "gemini")

    @patch("meu_agente_cli.llm._execute_single_provider")
    def test_failover_stream_true(self, mock_execute):
        """Valida se o failover automático funciona para chamadas com streaming."""
        db.set_setting("llm_provider", "lm_studio")
        db.set_setting("active_model", "local-model")
        db.set_backup_llm_config(
            enabled=True,
            provider="claude",
            model="claude-3-5-haiku-latest",
            api_key="claude-key",
            base_url=""
        )

        def mock_claude_gen():
            yield "Chunk 1 do backup. "
            yield "Chunk 2 do backup."

        def side_effect(prov, model, api_key, base_url, messages, stream):
            if prov == "lm_studio":
                raise llm.LLMProviderError("Falha de conexão: LM Studio offline")
            elif prov == "claude":
                return mock_claude_gen()
            return iter([])

        mock_execute.side_effect = side_effect

        messages = [{"role": "user", "content": "Teste Stream"}]
        stream_gen = llm.chat_completion("local-model", messages, stream=True)

        full_stream_text = "".join(list(stream_gen))

        self.assertIn("⚠️ [AVISO: LLM Principal (LM_STUDIO - local-model) falhou", full_stream_text)
        self.assertIn("Alternando para LLM de Backup: CLAUDE - claude-3-5-haiku-latest", full_stream_text)
        self.assertIn("Chunk 1 do backup. Chunk 2 do backup.", full_stream_text)

    @patch("meu_agente_cli.llm._execute_single_provider")
    def test_backup_disabled_does_not_failover(self, mock_execute):
        """Valida que com backup desativado o erro original é retornado sem acionar backup."""
        db.set_setting("llm_provider", "openai")
        db.set_setting("active_model", "gpt-4o")
        db.set_backup_llm_config(
            enabled=False,
            provider="gemini",
            model="gemini-1.5-flash",
            api_key="gemini-key",
            base_url=""
        )

        mock_execute.side_effect = llm.LLMProviderError("Status 401: Unauthorized", status_code=401)
        messages = [{"role": "user", "content": "Olá"}]
        result = llm.chat_completion("gpt-4o", messages, stream=False)

        self.assertIn("Erro ao chamar LLM (openai): Status 401: Unauthorized", result)
        self.assertNotIn("Alternando para LLM de Backup", result)

    @patch("httpx.post")
    def test_backup_provider_connection_logic(self, mock_post):
        """Valida chamada e status do teste de conexão do backup."""
        db.set_backup_llm_config(
            enabled=True,
            provider="gemini",
            model="gemini-1.5-flash",
            api_key="fake-gemini-key",
            base_url=""
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        ok = llm.test_backup_provider_connection()
        self.assertTrue(ok)
        self.assertTrue(mock_post.called)

if __name__ == "__main__":
    unittest.main()

