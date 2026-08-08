# Guia de Inicialização: WSL e Docker

Este guia contém as instruções passo a passo para configurar e executar o **Meu Agente CLI** tanto nativamente no WSL quanto em containers Docker utilizando Docker Compose.

---

## 🛠️ Pré-requisitos Gerais

Antes de iniciar em qualquer um dos ambientes, garanta que:
1. **LM Studio**: O LM Studio deve estar rodando na máquina hospedeira (Windows) com o servidor local ativo (geralmente na porta `1234`).
   - Certifique-se de que o modelo (como `google/gemma-4-31b-qat`) esteja carregado no LM Studio.
   - O servidor local do LM Studio deve estar habilitado para aceitar conexões (de preferência vinculando ao IP de rede ou permitindo requisições externas para que o WSL/Docker consiga acessá-lo).

---

## 🐧 1. Executando Nativamente no WSL

Esta opção executa o agente diretamente no seu ambiente WSL usando o PostgreSQL local.

### Passo 1: Instalar dependências de sistema
O agente necessita do cliente PostgreSQL e de algumas ferramentas de build para instalar certas dependências Python.
```bash
sudo apt-get update
sudo apt-get install -y build-essential libpq-dev curl
```

### Passo 2: Instalar o gerenciador `uv` (se ainda não tiver)
O `uv` é usado para gerenciar de forma rápida as dependências do Python e o ambiente virtual:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
*Lembre-se de reiniciar o terminal ou recarregar as variáveis de ambiente (`source ~/.bashrc`) após instalar o `uv`.*

### Passo 3: Sincronizar o ambiente Python
Navegue até a pasta do projeto e execute a sincronização:
```bash
cd meu-agente-cli
uv sync
```
Isso criará a pasta `.venv` e instalará o projeto e todos os pacotes necessários de forma rápida e segura.

### Passo 4: Banco de Dados PostgreSQL
O agente tenta detectar, instalar e iniciar o PostgreSQL local automaticamente no WSL caso ele não esteja rodando.
No entanto, caso queira garantir manualmente, você pode iniciar o serviço com:
```bash
sudo service postgresql start
```

### Passo 5: Executar o Agente
Rode o agente no modo interativo usando o `uv`:
```bash
uv run meu-agente-cli
```
> [!NOTE]
> Na primeira inicialização, o agente pedirá para você definir uma **senha de segurança**. Guarde esta senha, pois ela será solicitada para executar comandos de terminal não-seguros ou entrar no modo não-seguro do agente.

---

## 🐳 2. Executando via Docker / Docker Compose

Esta opção isola o agente e o PostgreSQL em containers dedicados utilizando Docker Compose, eliminando a necessidade de instalar o banco de dados nativamente no WSL.

### Passo 1: Construir e iniciar os serviços
Na pasta raiz do projeto, execute o build da imagem do agente e inicialize o banco de dados em segundo plano:
```bash
docker compose up -d --build
```
Isso fará o seguinte:
- Baixará e iniciará uma imagem oficial do PostgreSQL (`meu-agente-db`).
- Compilará a imagem customizada do agente (`meu-agente-cli`), instalando as dependências do Python em cache.
- Aguardará o PostgreSQL estar totalmente saudável antes de finalizar.

### Passo 2: Executar o Agente de forma interativa
Como o agente é um utilitário CLI interativo (que requer comandos do usuário e entrada de teclado), você deve executá-lo em modo interativo (`tty` / `stdin` anexados):
```bash
docker compose run --rm agent
```
*A flag `--rm` garante que o container temporário do terminal seja removido após você sair (`/exit`), mantendo seu ambiente limpo.*

### Passo 3: Parar os serviços
Quando terminar de usar e desejar parar o container do banco de dados em segundo plano:
```bash
docker compose down
```

---

## ⚙️ Arquivos de Configuração e Persistência

### 📌 WSL Local
- **Configurações gerais**: Salvas em `~/.config/meu-agente-cli/config.json`.
- **Banco de Dados**: Utiliza a instância local do PostgreSQL do WSL (`meu_agente_cli`).

### 📌 Docker Compose
- **Persistência de Dados**: O banco de dados PostgreSQL e os arquivos de configuração do agente são persistidos mesmo se você rodar `docker compose down`. Eles utilizam os volumes nomeados:
  - `meu-agente-cli_agent_data`: Mapeia para as configurações em `/root/.config/meu-agente-cli` dentro do container.
  - `meu-agente-cli_pgdata`: Armazena os dados físicos do PostgreSQL.
- **Configuração de Comandos Seguros**: O arquivo `safe_commands.json` é compartilhado diretamente da máquina host para a pasta `/app/safe_commands.json` do container. Qualquer edição feita no host se aplica instantaneamente ao agente em execução no Docker.
- **Configuração de Ambiente**: As variáveis como `DB_HOST=db`, `DB_NAME=meu_agente_db` e `LM_STUDIO_URL=http://host.docker.internal:1234` são injetadas automaticamente no container através do arquivo `docker-compose.yml`.
