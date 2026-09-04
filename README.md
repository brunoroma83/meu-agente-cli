# Meu Agente CLI 🤖💼

O **Meu Agente CLI** é um assistente virtual pessoal e financeiro inteligente construído em Python, projetado especificamente para rodar no terminal Linux via WSL (Windows Subsystem for Linux), integrando-se localmente com o **LM Studio** no Windows Host e um banco de dados **PostgreSQL** local no WSL.

Ele utiliza o gerenciador de dependências `uv` para máxima velocidade e isolamento de ambiente.

---

## 🌟 Principais Funcionalidades

### 1. 🧠 Chat Inteligente & Orquestração de Ferramentas
* **Interface Rich Markdown**: Respostas estruturadas no terminal em tempo real via *streaming* com compilação Markdown incremental, encabeçadas por um rótulo estilizado `Agente IA: 🤖`.
* **Janela Deslizante de Contexto**: O histórico recente é mantido no PostgreSQL e o agente consulta automaticamente os últimos 15 turnos de conversa.
* **Memória de Longo Prazo (Notas)**: Ferramenta integrada de salvamento e pesquisa de fatos cruciais na base de dados (`/notes`).
* **Resiliência a Modelos Locais**: O interpretador de chamadas de ferramenta é resiliente e consegue isolar e rodar blocos de código JSON mesmo se a resposta do modelo vier acompanhada de textos explicativos ou chit-chat inicial.

### 2. 💰 Gestão Financeira Pessoal Completa (`/finance`)
* **Dashboard Web Especializado com Gradio (`http://localhost:7860`)**: Interface web moderna dividida em abas dedicadas:
  - **📅 Contas Mensais & Faturas**: Visão focada exclusivamente em custos fixos recorrentes (aluguel, condomínio, luz, internet) e faturas consolidadas de cartão, com checklist de liquidação (`🟢 Paga` / `⏳ Pendente`).
  - **💳 Cartões de Crédito**: Extrato detalhado individual por cartão (*Itaú*, *BB*, *C6*, *Porto*, etc.), exibindo cada compra/parcela e consolidando o valor total a pagar na aba de Contas Mensais.
  - **☕ Gastos Diários & Teto por Dia**: Extrato limpo apenas com despesas rotineiras (alimentação, transporte, lazer) e painel orçamentário que calcula em tempo real o **Teto Diário Recomendado** ($\text{Saldo Livre} / \text{Dias Restantes}$).
  - **📊 Extrato Geral & Relatórios**: Visão contábil geral unificada e gráficos de evolução.
  - **💼 Carteira de Investimentos**: Gestão patrimonial com alocação por classe, instituição bancária e atualização de cotações.
* **Alertas Diários às 11:00 AM com Teto de Gastos**: Cron job automático que notifica diariamente sobre contas a vencer e informa a disponibilidade de gastos recomendada para o dia.
* **Lançamentos Ágeis por Voz e Fotos (Telegram)**: Fale despesas ou receitas por áudio (ex: *"Gastei 45 reais no almoço de hoje"*) ou envie fotos de comprovantes PIX, cupons fiscais e faturas para leitura via IA multimodal e confirmação em 1 toque (`[✅ Confirmar Lançamento]`).
* **Cartões de Crédito e Parcelamento (`/finance card`)**: Cadastre datas de fechamento e vencimento de faturas de cartões e registre compras parceladas com ajuste exato de dízimas e projeção automática para os meses subsequentes.
* **Lançamentos Individuais e em Lote (Bulk)**: Registre despesas e receitas uma a uma ou insira listas inteiras de uma vez, otimizando o consumo de turnos da IA.
* **Data de Vencimento (`due_date`)**: Suporte nativo a datas de vencimento em lançamentos para controle de contas futuras.
* **Painel de Resumo Dinâmico**: O resumo consolidado (Receitas, Despesas e Saldo) calcula e exibe em tempo real os somatórios exatos dos itens que correspondem aos filtros de busca aplicados.
* **Busca Inteligente Insensível a Acentos**: Pesquisa textual case-insensitive e sotaque-insensitive (ex: buscar `cartao` localiza registros cadastrados como `"Cartão"` utilizando transliteração nativa no SQL).
* **Exclusão Lógica (Soft Delete)**: O agente adota a política de inativação lógica. Dados removidos são apenas marcados como inativos (`active = false`), garantindo que nenhuma informação financeira seja permanentemente destruída.


### 3. 🗓️ Subagentes & Tarefas em Segundo Plano (`/cron`)
* Thread de agendamento paralelo utilizando `asyncio` e `croniter`.
* Executa rotinas agendadas (ex: relatórios diários, checagens de notícias ou varredura de dados) e registra o histórico de logs diretamente na memória do banco de dados para consulta posterior.

### 4. 🧮 Habilidades e Ferramentas Locais
* **Calculadora Segura**: Interpretador aritmético avançado para somar despesas ou fazer contas de porcentagem de forma precisa, suportando lote (`expressions` mapeadas por ID).
* **Cotações de Mercado**: Integração com a API do Yahoo Finance (`yfinance`) para checagem de ativos e moedas cambiais.
* **Clima em Tempo Real**: Consulta dinâmica da API climática Open-Meteo.
* **rss Feeds**: Agregador de feed RSS de Economia, Tecnologia e Ciência.

### 5. 🛡️ Segurança CLI (Modo Seguro vs. Não-Seguro)
* **Modo Seguro**: Impede a execução de comandos não listados em uma whitelist de segurança no WSL. Permite carregar comandos adicionais seguros a partir do arquivo `safe_commands.json` na raiz do projeto, validando sua integridade através de **assinatura criptográfica assimétrica RSA**. A assinatura garante que somente o usuário (humano) com a senha de segurança consegue assinar alterações no arquivo (através do comando `/unsafe sign`), enquanto o agente apenas valida usando a chave pública do banco (sem pedir senha ao iniciar), impedindo que o agente burle a whitelist.
* **Modo Não-Seguro**: Permite rodar comandos CLI arbitrários, condicionado à validação de uma **Senha de Segurança** (salva no PostgreSQL com criptografia SHA-256) e à confirmação interativa do usuário.
* **Restrição de Subagentes**: Tarefas rodando em segundo plano são estritamente proibidas de executar comandos CLI inseguros, prevenindo qualquer execução autônoma indevida.

### 6. 🎙️ Transcrição de Áudio e Registro de Reuniões
* **Recepção de Mídias**: Suporta o envio de mensagens de voz e arquivos de áudio (como `.ogg`, `.mp3`) diretamente pelo Telegram.
* **Processamento Automatizado**: Os arquivos são baixados para `uploads/archive/audio/` e convertidos via `ffmpeg` para WAV para serem transcritos localmente usando a biblioteca `speech_recognition` (Google Speech Recognition).
* **Teclado de Ações (Resumos & Planos)**: Teclado inline interativo para solicitar resumos simples, detalhados com plano de ação ou enviar instruções personalizadas para o texto transcrito.
* **Persistência Completa**: Grava de forma permanente os metadados do áudio, a transcrição textual, a instrução executada e o relatório/resumo gerado pela LLM no PostgreSQL na tabela `audio_transcriptions`, ideal para arquivamento de reuniões.

---

## 🛠️ Instalação e Requisitos

### Pré-requisitos
* **WSL2** com Ubuntu 20.04 ou superior.
* **Python 3.10** ou superior instalado no WSL.
* **PostgreSQL** instalado e ativo no WSL.
* **LM Studio** rodando no Windows Host (com a API servindo localmente na porta `1234`).
* **Gerenciador de Dependências `uv`** instalado no WSL:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

---

## 🚀 Como Inicializar

### Opção A: Executando localmente no WSL
1. Entre na pasta do projeto no WSL:
   ```bash
   cd meu-agente-cli
   ```
2. Inicialize o agente usando o `uv`:
   ```bash
   uv run meu-agente
   ```

### Opção B: Executando com Docker Compose 🐳
Este projeto vem com suporte nativo a Docker, permitindo levantar o banco de dados PostgreSQL e o assistente de forma isolada com um único comando.

1. Suba o banco e compile os containers dos serviços (agente, bot do Telegram e dashboard Gradio):
   ```bash
   docker compose up -d --build
   ```
2. Conecte-se interativamente no console do agente para chat:
   ```bash
   docker compose run agent
   ```
   *(Nota: O parâmetro `host.docker.internal` resolve automaticamente o endereço do Windows Host para conectar no LM Studio).*
3. Acesse o **Dashboard Financeiro Gradio** diretamente no seu navegador:
   👉 **[http://localhost:7860](http://localhost:7860)**

### Resiliência na Inicialização:
* **PostgreSQL**: Se o PostgreSQL do WSL estiver parado, o script tentará iniciá-lo automaticamente.
* **Credenciais do Banco**: Caso a conexão local falhar ou seja o primeiro uso, o instalador perguntará de forma interativa o IP, Porta, Usuário e Senha e salvará a configuração com segurança no arquivo `config.json`.
* **Conexão com LM Studio**: O agente resolve dinamicamente o IP do Windows Host (usualmente lendo o `/etc/resolv.conf`). Caso não consiga detectar, entrará em um menu interativo para você informar o IP manualmente e realizar testes de ping/API antes de salvar.

---

## ⌨️ Comandos de Barra Disponíveis (Console)

Digite esses comandos diretamente na barra de entrada de texto no terminal:

* **`/help`**: Exibe o painel de ajuda com todos os comandos e parâmetros.
* **`/status`**: Mostra o estado de segurança (Safe/Unsafe), o provedor de LLM ativo, o status de conexão/autenticação e o modelo de IA atualmente selecionado.
* **`/clear`**: Limpa o histórico de chat da sessão corrente no banco (reseta a janela de contexto deslizante do agente).
* **`/history <limite>`**: Exibe ou configura o limite dinâmico de mensagens de histórico enviadas ao LLM (ex: `/history 4`).
* **`/models`**: Configura o provedor e modelo de linguagem ativo. Permite alternar entre o LM Studio local e os provedores externos (OpenAI, Google Gemini, Anthropic Claude, DeepSeek, Alibaba Qwen, Moonshot Kimi ou um Provedor Personalizado compatível com a API da OpenAI).
  - No terminal, apresenta um assistente interativo para configurar a chave de API e selecionar o modelo.
  - No Telegram, aceita parâmetros em linha (ex: `/models openai <API_KEY> <modelo>` ou `/models custom <API_KEY> <URL_BASE> <modelo>`).
* **`/safe`**: Ativa instantaneamente o Modo Seguro.
* **`/unsafe`**: Solicita a senha de segurança para desativar o Modo Seguro.
  - `/unsafe sign`: Solicita a senha para assinar criptograficamente (`RSA`) o arquivo `safe_commands.json` após você editá-lo.
* **`/notes`**: Exibe todas as notas da base de conhecimento persistente.
* **`/cron`**: Gerencia e exibe os subagentes agendados.
  - `/cron add`: Cria um cron job interativamente.
  - `/cron delete`: Exclui/desativa um cronjob pelo ID.
* **`/reunioes`**: Lista as reuniões e gravações de áudio registradas no banco.
  - `/reunioes <ID>`: Exibe os detalhes completos de uma reunião específica (transcrição original, áudio correspondente, solicitação de processamento e resumo/plano de ação da LLM).
* **`/transcrever`**: Lista arquivos de áudio presentes na pasta `uploads/` do servidor.
  - `/transcrever <nome_do_arquivo>`: Inicia a transcrição do áudio local (processando automaticamente em chunks caso o arquivo seja longo/grande) e oferece o menu interativo de resumo.
* **`/finance`**: Exibe a tabela de transações do **Mês Atual** e o painel de resumo financeiro dinâmico.
  - `/finance web`: Exibe o link e status do Dashboard Web interativo Gradio (`http://localhost:7860`).
  - `/finance card`: Gestão e lançamentos de cartões de crédito (`card add`, `card list`, `card buy`).
  - `/finance import`: Importação de receitas e despesas a partir de `uploads/finance.csv`.
  - `/finance next`: Mostra a projeção e planejamento de contas para o **Mês Seguinte**.
  - `/finance deleted`: Lista todos os lançamentos que foram inativados (Soft Deleted).
  - `/finance restore <ID>`: Restaura um lançamento inativado de volta ao status ativo.
  - `/finance all`: Exibe todos os lançamentos ativos cadastrados na história do banco.
  - `/finance mes=MM-YYYY` *(ex: `/finance mes=10-2026`)*: Filtra os lançamentos ativos pelo mês e ano de vencimento especificados.
  - `/finance q=termo` *(ex: `/finance q=bb`)*: Pesquisa lançamentos que contenham o termo de busca na categoria ou descrição (insensível a maiúsculas/minúsculas e acentos).
  - `/finance delete <ID>`: Inativa o lançamento correspondente a esse ID (Soft Delete).
* **`/exit`** ou **`/quit`**: Interrompe os agendamentos em segundo plano e encerra o assistente.
