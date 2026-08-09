# Changelog

Todas as alterações notáveis deste projeto serão documentadas neste arquivo.

O formato é baseado no [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/) e este projeto adota o [Versionamento Semântico](https://semver.org/lang/pt-BR/).

---

## [Unreleased]

### Added
- **Backup e Restauração Criptografados**: Novos comandos `/backup` e `/restore` para exportação e importação seguras de toda a base de dados (tabelas e parâmetros) em código Python puro (sem dependência de binários externos), protegendo os dados com criptografia AES-256 (via Fernet com PBKDF2) e compactação Gzip.
- **Suporte a Containerização Docker**: Adicionados arquivos `Dockerfile` e `docker-compose.yml` para empacotamento completo do agente e do banco PostgreSQL isolado em containers.
- **Assinatura Criptográfica RSA**: Whitelist de comandos seguros `safe_commands.json` agora é validada criptograficamente em tempo de execução usando uma assinatura digital gerada por chave assimétrica RSA.
- **Comando `/unsafe sign`**: Permite ao usuário (humano) assinar digitalmente o arquivo `safe_commands.json` após editá-lo, fornecendo sua senha de segurança.
- **Estrutura de Plugins Dinâmicos**: Nova pasta `custom_tools/` e arquivo `custom_tools.json` para carregar ferramentas customizadas sem a necessidade de editar código-fonte Python central.
- **Prompt Dinâmico**: O prompt do sistema do agente agora é gerado em tempo de execução carregando as configurações a partir do arquivo `system_prompt_config.json`.
- **Filtro `/finance next`**: Adicionado atalho para visualizar e planejar transações do mês seguinte no console.
- **Comando `/history`**: Permite ler ou configurar dinamicamente o número máximo de mensagens recentes mantidas no histórico e enviadas para o contexto do LLM.
- **Comando `/clear`**: Comando de console para limpar o histórico da conversa e resetar o contexto recente do agente.
- **Ferramenta de Calculadora (`calculator_tool`)**: Calculadora matemática segura integrada que processa expressões individuais ou em lote (bulk) via formato JSON.
- **Datas de Vencimento (`due_date`)**: Campo adicionado nas transações para permitir agendamentos e melhor organização de contas futuras.

### Changed
- **Exclusão Lógica (Soft Delete)**: O agente passou a adotar exclusão lógica. Registros deletados de despesas/receitas, notas ou cronjobs agora apenas são inativados (`active = false`), impedindo perdas destrutivas de dados.
- **Filtro Padrão do `/finance`**: O comando sem argumentos agora assume o mês corrente por padrão (ex: `mes=08-2026`).
- **Resiliência do Parser de Ferramentas**: Modificado o interpretador de fluxo para ser tolerante a respostas conversacionais da LLM que antecedam o bloco de código JSON de chamada da ferramenta.
- **Otimização de Conexão e Codificação**: Tratamento para codepoints de caracteres `surrogate` inválidos vindos de inputs ou outputs no WSL, prevenindo quebras em payloads JSON ou SQL.

### Security
- **Proteção Anteadulteração de Whitelist**: A whitelist de comandos é apenas para leitura da IA. Qualquer modificação externa sem a devida chave privada (senha do usuário) invalida a assinatura e bloqueia o carregamento de novos comandos CLI.
- **Segurança de Armazenamento de Senha**: A senha de segurança do usuário e a chave privada RSA de assinatura são armazenadas no banco PostgreSQL em formato hash SHA-256 e criptografadas por senha, respectivamente.
