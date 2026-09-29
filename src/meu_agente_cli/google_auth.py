import os
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Permite conexões locais HTTP para captura do código de autorização OAuth
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# Escopos necessários para leitura/envio de e-mails e gerenciamento completo da agenda
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events"
]

# Caminho persistente no volume Docker agent_data
GOOGLE_DATA_DIR = Path("/root/.config/meu-agente-cli/google")
TOKEN_FILE = GOOGLE_DATA_DIR / "token.json"
CREDENTIALS_FILE = Path("/app/credentials.json")

def get_google_credentials() -> Optional[Credentials]:
    """
    Retorna as credenciais OAuth 2.0 válidas do Google salvas no volume persistente.
    Se o token estiver expirado mas possuir refresh_token, renova automaticamente.
    """
    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception as e:
            print(f"[AVISO] Erro ao ler token.json existente: {e}")

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Salva o token renovado
            GOOGLE_DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        except Exception as e:
            print(f"[ERRO] Falha ao renovar token OAuth: {e}")
            creds = None

    return creds

def run_oauth_flow():
    """
    Inicia o fluxo de autorização OAuth 2.0 no terminal Docker.
    Gera a URL para o usuário abrir no navegador do Windows e salvar o token.
    """
    if not CREDENTIALS_FILE.exists():
        print("\n" + "=" * 70)
        print("❌ ARQUIVO 'credentials.json' NÃO ENCONTRADO!")
        print("=" * 70)
        print("Para que o agente consiga acessar o Gmail e Google Calendar:")
        print("1. Acesse o console: https://console.cloud.google.com/")
        print("2. Ative as APIs: 'Gmail API' e 'Google Calendar API'.")
        print("3. Em 'Tela de permissão OAuth' (OAuth consent screen), adicione seu e-mail como Usuário de Teste.")
        print("4. Em 'Credenciais', crie um 'ID do cliente OAuth' do tipo 'App para computador' (Desktop App).")
        print("5. Baixe o JSON gerado, renomeie para 'credentials.json' e coloque na raiz do projeto:")
        print("   c:\\agy2-projects\\meu-agente-cli\\credentials.json (ou /app/credentials.json no Docker)")
        print("=" * 70 + "\n")
        return False

    GOOGLE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_FILE),
        SCOPES,
        redirect_uri="http://localhost:8085/"
    )
    
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    
    print("\n" + "=" * 70)
    print("🔑 AUTORIZAÇÃO GOOGLE WORKSPACE (GMAIL & GOOGLE CALENDAR)")
    print("=" * 70)
    print("1. Copie e abra a URL abaixo no navegador do seu Windows:\n")
    print(f"👉 {auth_url}\n")
    print("2. Faça login com sua conta Google e aprove as permissões de acesso.")
    print("3. Ao final, a página tentará redirecionar para 'http://localhost:8085/?code=...'.")
    print("   Copie a URL inteira da barra de endereços do seu navegador (ou apenas o código após 'code=')")
    print("   e cole abaixo:")
    print("=" * 70)
    
    try:
        user_input = input("\nCole a URL de redirecionamento ou o código: ").strip()
        if not user_input:
            print("[ERRO] Entrada vazia fornecida.")
            return False
            
        # Extrai o código caso o usuário tenha colado a URL completa
        auth_code = user_input
        if "code=" in user_input:
            parsed = parse_qs(urlparse(user_input).query)
            if "code" in parsed:
                auth_code = parsed["code"][0]
                
        flow.fetch_token(code=auth_code)
        creds = flow.credentials
        
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
            
        print("\n" + "=" * 70)
        print("✅ SUCESSO! Token OAuth do Google salvo no volume persistente!")
        print(f"Local: {TOKEN_FILE}")
        print("=" * 70)
        
        # Teste imediato de validação
        test_apis(creds)
        return True
    except Exception as e:
        print(f"\n[ERRO] Falha ao concluir autorização OAuth: {e}")
        return False

def test_apis(creds: Credentials):
    """Testa a conectividade com a API do Gmail e do Google Calendar."""
    print("\n🔍 Testando conectividade com as APIs do Google...")
    try:
        from googleapiclient.discovery import build
        
        # Teste Gmail
        gmail_service = build("gmail", "v1", credentials=creds)
        profile = gmail_service.users().getProfile(userId="me").execute()
        email_addr = profile.get("emailAddress", "desconhecido")
        total_msgs = profile.get("messagesTotal", 0)
        print(f"  ✉️ Gmail conectado: {email_addr} ({total_msgs} mensagens totais)")
        
        # Teste Calendar
        calendar_service = build("calendar", "v3", credentials=creds)
        cal_list = calendar_service.calendarList().list(maxResults=5).execute()
        cals = [c.get("summary", "Sem título") for c in cal_list.get("items", [])]
        print(f"  📅 Calendar conectado. Agendas encontradas: {', '.join(cals)}")
        print("\n🎉 Tudo pronto! O agente já pode consultar seus e-mails e sua agenda.")
    except Exception as e:
        print(f"  ⚠️ Aviso no teste das APIs: {e}")

if __name__ == "__main__":
    run_oauth_flow()
