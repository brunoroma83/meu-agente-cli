FROM python:3.11-slim

# Instala dependências do sistema para o psycopg, criptografia, pyaudio, conversão de áudio (ffmpeg) e Google Cloud CLI (gcloud)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    portaudio19-dev \
    ffmpeg \
    apt-transport-https \
    ca-certificates \
    gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /etc/apt/keyrings/cloud.google.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update && apt-get install -y --no-install-recommends google-cloud-cli \
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
