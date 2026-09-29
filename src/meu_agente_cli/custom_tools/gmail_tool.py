import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, Dict, Any
from meu_agente_cli.google_auth import get_google_credentials

def run(**kwargs):
    """
    Ferramenta para consultar, ler e enviar e-mails via Gmail API.

    Args:
        action (str): 'list' (ou 'search'), 'read', ou 'send'. Padrão: 'list'.
        query (str, optional): Filtro de busca Gmail (ex: 'is:unread', 'from:cliente@empresa.com').
        max_results (int, optional): Quantidade de e-mails para listar (padrão: 5).
        message_id (str, optional): ID do e-mail para ler (obrigatório para action='read').
        to (str, optional): Destinatário do e-mail (obrigatório para action='send').
        subject (str, optional): Assunto do e-mail (obrigatório para action='send').
        body (str, optional): Conteúdo/corpo da mensagem (obrigatório para action='send').
        cc (str, optional): E-mails em cópia.
    """
    creds = get_google_credentials()
    if not creds:
        return (
            "Erro: Credenciais do Gmail não encontradas ou não autorizadas no Docker.\n"
            "Para autorizar, execute no terminal do projeto o comando:\n"
            "  docker compose exec agent uv run python -m meu_agente_cli.google_auth\n"
            "Certifique-se de que o arquivo 'credentials.json' está presente na pasta raiz."
        )

    try:
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
    except Exception as e:
        return f"Erro ao inicializar serviço Gmail: {e}"

    action = kwargs.get("action", "list").lower()

    if action in ("list", "search"):
        return list_emails(service, kwargs)
    elif action == "read":
        return read_email(service, kwargs)
    elif action == "send":
        return send_email(service, kwargs)
    else:
        return f"Erro: Ação '{action}' não suportada. Use: 'list', 'read' ou 'send'."

def list_emails(service, kwargs: dict) -> str:
    query = kwargs.get("query", "is:unread")
    max_results = min(int(kwargs.get("max_results", 5)), 20)

    try:
        results = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return f"Nenhum e-mail encontrado com o filtro '{query}'."

        output_lines = [f"📬 Encontrados {len(messages)} e-mails (filtro: '{query}'):"]

        for item in messages:
            msg_id = item["id"]
            msg = service.users().messages().get(
                userId="me",
                id=msg_id,
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            sender = headers.get("From", "Remetente desconhecido")
            subject = headers.get("Subject", "(Sem assunto)")
            date = headers.get("Date", "")
            snippet = msg.get("snippet", "")

            output_lines.append(
                f"- **De:** {sender}\n"
                f"  **Assunto:** {subject}\n"
                f"  **Data:** {date}\n"
                f"  **Snippet:** {snippet}\n"
                f"  **ID:** `{msg_id}`"
            )

        return "\n\n".join(output_lines)
    except Exception as e:
        return f"Erro ao consultar e-mails: {e}"

def read_email(service, kwargs: dict) -> str:
    message_id = kwargs.get("message_id")
    if not message_id:
        return "Erro: 'message_id' é obrigatório para ler o conteúdo de um e-mail."

    try:
        msg = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        sender = headers.get("From", "Desconhecido")
        to = headers.get("To", "Desconhecido")
        subject = headers.get("Subject", "(Sem assunto)")
        date = headers.get("Date", "")

        body_content = ""
        payload = msg.get("payload", {})
        
        def extract_body(part):
            nonlocal body_content
            mime_type = part.get("mimeType", "")
            data = part.get("body", {}).get("data")
            if data and mime_type == "text/plain":
                decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                body_content += decoded + "\n"
            elif part.get("parts"):
                for subpart in part["parts"]:
                    extract_body(subpart)

        if payload.get("parts"):
            for part in payload["parts"]:
                extract_body(part)
        else:
            data = payload.get("body", {}).get("data")
            if data:
                body_content = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        if not body_content.strip():
            body_content = msg.get("snippet", "(Sem texto legível no corpo)")

        # Limita o tamanho para evitar estourar o contexto do modelo
        if len(body_content) > 3000:
            body_content = body_content[:3000] + "\n... [Texto truncado]"

        return (
            f"📧 **E-mail ID:** `{message_id}`\n"
            f"**De:** {sender}\n"
            f"**Para:** {to}\n"
            f"**Data:** {date}\n"
            f"**Assunto:** {subject}\n\n"
            f"**Conteúdo:**\n{body_content}"
        )
    except Exception as e:
        return f"Erro ao ler e-mail ID '{message_id}': {e}"

def send_email(service, kwargs: dict) -> str:
    to = kwargs.get("to")
    subject = kwargs.get("subject")
    body = kwargs.get("body")
    cc = kwargs.get("cc")

    if not to or not subject or not body:
        return "Erro: 'to', 'subject' e 'body' são obrigatórios para enviar um e-mail."

    try:
        message = MIMEMultipart()
        message["to"] = to
        message["subject"] = subject
        if cc:
            message["cc"] = cc

        message.attach(MIMEText(body, "plain", "utf-8"))

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        send_result = service.users().messages().send(
            userId="me",
            body={"raw": raw}
        ).execute()

        return f"✅ E-mail enviado com sucesso para '{to}'!\nID da Mensagem: `{send_result.get('id')}`"
    except Exception as e:
        return f"Erro ao enviar e-mail: {e}"
