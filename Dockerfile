FROM python:3.11-slim

# Instala dependências do sistema para o psycopg e criptografia
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instala o gerenciador uv de dependências do Python
ADD https://astral.sh/uv/install.sh /install.sh
RUN sh /install.sh && rm /install.sh
ENV PATH="/root/.local/bin/:${PATH}"

WORKDIR /app

# Copia arquivos de dependência e sincroniza o ambiente virtual
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copia o código fonte do projeto e arquivos estáticos
COPY src/ ./src/
COPY README.md CHANGELOG.md safe_commands.json ./

# Sincroniza o ambiente instalando o projeto em si
RUN uv sync --frozen --no-dev


# Comando de entrada usando o executável do uv
CMD ["uv", "run", "meu-agente-cli"]
