FROM python:3.11-slim

# Instala dependências mínimas do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instala o gerenciador uv
ADD https://astral.sh/uv/install.sh /install.sh
RUN sh /install.sh && rm /install.sh
ENV PATH="/root/.local/bin/:${PATH}"

WORKDIR /app

# Copia arquivos de dependência e sincroniza o ambiente virtual
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copia o código fonte do projeto
COPY src/ ./src/
COPY README.md CHANGELOG.md ./

# Sincroniza o ambiente instalando o pacote
RUN uv sync --frozen --no-dev

# Porta da aplicação Web + MCP SSE
EXPOSE 8000

# Executa o servidor de finanças e MCP
CMD ["uv", "run", "meu-financeiro", "--host", "0.0.0.0", "--port", "8000"]
