# 🚀 Guia de Implantação em Produção (Servidor)

Este guia orienta o processo de migração e deploy do **Meu Agente CLI** no servidor de produção, integrando-o à rede Docker externa `web_network` do seu proxy reverso, configurando o endpoint de LAN do LM Studio (`http://192.168.1.15:1234`) e transferindo todo o banco de dados PostgreSQL.

---

## 🏗️ 1. Arquitetura em Produção

```
               [ Internet / Clientes ]
                          │
                          ▼
            [ Proxy Reverso (Portas 80 / 443) ]
            (Nginx / Traefik / Nginx Proxy Manager)
                          │
               ┌──────────┴──────────┐
               │  Rede: web_network  │
               └──────────┬──────────┘
                          │ (HTTP / WebSockets)
                          ▼
             [ meu-agente-dashboard:7860 ] (expose: 7860, sem porta no host)
                          │
               ┌──────────┴──────────┐
               │ Rede: meu-agente-net│ (Isolada)
               └──────────┬──────────┘
             ┌────────────┼────────────┐
             ▼            ▼            ▼
      [ meu-agente-db ] [ agent ] [ telegram-bot ]
      (PostgreSQL)         │            │
                           └──────┬─────┘
                                  ▼ (HTTP LAN)
                 [ LM Studio: http://192.168.1.15:1234 ]
```

### Características de Produção:
* **Sem portas abertas no Host:** O PostgreSQL não expõe a porta 5432 para fora da rede Docker interna. O Gradio Dashboard não vincula a porta 7860 à interface física do host, expondo-a apenas para os containers que compartilham a rede `web_network`.
* **Rede Externa:** O container `dashboard` conecta-se simultaneamente à rede interna `meu-agente-net` e à rede externa `web_network`.
* **LM Studio em LAN:** O agente e o bot do Telegram conectam-se diretamente ao IP local `http://192.168.1.15:1234`.

---

## 📋 2. Pré-requisitos no Servidor

1. **Docker Engine e Docker Compose** instalados (Docker v20.10+ / Compose v2+).
2. **Rede Docker `web_network`** existente:
   ```bash
   # Verifique se a rede já existe
   docker network ls | grep web_network

   # Se não existir, crie-a com:
   docker network create web_network
   ```
3. **LM Studio rodando na rede local** no IP `192.168.1.15` na porta `1234` com a opção **CORS** ativada.

---

## 📦 3. Passo a Passo de Migração e Deploy

### Passo 1: Gerar o Dump no Ambiente de Desenvolvimento
No seu computador local (onde o banco atual está com seus dados e 415 lançamentos):

* **No Linux / WSL:**
  ```bash
  chmod +x scripts/export_db.sh
  ./scripts/export_db.sh
  ```
* **No Windows (PowerShell):**
  ```powershell
  .\scripts\export_db.ps1
  ```

Os arquivos de dump serão gerados em:
* `backups/meu_agente_db_prod.dump` (Formato binário comprimido nativo do Postgres)
* `backups/meu_agente_db_prod.sql` (Script SQL compatível)

---

### Passo 2: Transferir o Projeto e os Dumps para o Servidor
Você pode clonar o repositório ou enviar a pasta do projeto via `scp` ou `rsync`:

```bash
# Exemplo enviando via rsync para o servidor:
rsync -avz --exclude '.venv' --exclude '__pycache__' ./ usuario@seu-servidor:/opt/meu-agente-cli/

# Ou enviando apenas a pasta de backups caso o código já tenha sido clonado via Git:
scp backups/meu_agente_db_prod.dump usuario@seu-servidor:/opt/meu-agente-cli/backups/
```

---

### Passo 3: Configurar as Variáveis de Ambiente (`.env`)
No servidor, acesse o diretório da aplicação:

```bash
cd /opt/meu-agente-cli
cp .env.example .env
nano .env
```

Preencha os valores reais:
```env
TELEGRAM_BOT_TOKEN=seu_bot_token_do_telegram
TELEGRAM_AUTHORIZED_USER_IDS=seu_id_telegram
LM_STUDIO_URL=http://192.168.1.15:1234
CLOUDFLARE_ACCOUNT_ID=seu_account_id
CLOUDFLARE_API_TOKEN=seu_api_token
```

---

### Passo 4: Subir o PostgreSQL e Restaurar o Banco de Dados
No servidor, execute o script de restauração:

```bash
chmod +x scripts/restore_db.sh
./scripts/restore_db.sh
```

O script irá:
1. Subir o container `meu-agente-db`;
2. Aguardar o healthcheck do PostgreSQL (`pg_isready`);
3. Restaurar tabelas, índices, sequências e dados a partir de `backups/meu_agente_db_prod.dump`;
4. Exibir o relatório de validação das 16 tabelas e registros recuperados.

---

### Passo 5: Inicializar toda a Stack em Produção
Com o banco restaurado, suba os demais serviços:

```bash
docker compose up -d
```

Verifique se todos os containers estão saudáveis:
```bash
docker compose ps
docker compose logs -f telegram-bot
docker compose logs -f dashboard
```

---

## 🌐 4. Configuração no Proxy Reverso

Como o container `dashboard` está conectado à rede `web_network`, configure seu proxy reverso apontando para:
* **Host / Destino:** `meu-agente-dashboard` (ou o nome do serviço `dashboard`)
* **Porta:** `7860`
* **Suporte a WebSockets:** Obrigatório (o Gradio utiliza WebSockets para atualizações de tela em tempo real).

### A) Nginx Proxy Manager (NPM)
1. Adicione um novo **Proxy Host**:
   * **Domain Names:** `agente.seudominio.com.br`
   * **Scheme:** `http`
   * **Forward Hostname / IP:** `meu-agente-dashboard`
   * **Forward Port:** `7860`
   * Marque: **Websockets Support** ✅
   * Marque: **Block Common Exploits** ✅
2. Na aba **SSL**:
   * Selecione seu certificado Let's Encrypt
   * Marque: **Force SSL** e **HTTP/2 Support** ✅

---

### B) Nginx Tradicional (`nginx.conf`)
```nginx
server {
    server_name agente.seudominio.com.br;

    location / {
        proxy_pass http://meu-agente-dashboard:7860;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

---

### C) Traefik (Labels no Docker Compose)
Se utilizar Traefik, basta descomentar ou adicionar as labels no serviço `dashboard` do `docker-compose.yml`:
```yaml
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.agente.rule=Host(`agente.seudominio.com.br`)"
      - "traefik.http.routers.agente.entrypoints=websecure"
      - "traefik.http.routers.agente.tls.certresolver=letsencrypt"
      - "traefik.http.services.agente.loadbalancer.server.port=7860"
```

---

## 🔄 5. Retornando ao Ambiente de Desenvolvimento Local
Se desejar rodar a stack na sua máquina de desenvolvimento com as portas 7860 e 5432 abertas no host físico (sem exigir proxy ou rede `web_network`), use:

```bash
docker compose -f docker-compose.dev.yml up -d
```
