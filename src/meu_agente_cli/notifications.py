import logging
from datetime import datetime, timedelta, date
from typing import Optional, List
import telebot
from meu_agente_cli import config, db

logger = logging.getLogger(__name__)

def get_telegram_bot() -> Optional[telebot.TeleBot]:
    """Retorna uma instância do TeleBot se o token estiver configurado."""
    token = config.get_telegram_bot_token()
    if not token or token.startswith("123456:dummy"):
        return None
    try:
        return telebot.TeleBot(token)
    except Exception as e:
        logger.error(f"Erro ao instanciar bot do Telegram: {e}")
        return None

def send_telegram_broadcast(message_text: str, parse_mode: Optional[str] = "Markdown") -> int:
    """
    Envia uma mensagem para todos os IDs de usuários autorizados no Telegram.
    Retorna o número de mensagens enviadas com sucesso.
    """
    bot = get_telegram_bot()
    if not bot:
        logger.warning("Notificação Telegram não enviada: Bot ou token não configurado.")
        return 0
        
    authorized_ids = config.get_telegram_authorized_user_ids()
    if not authorized_ids:
        logger.warning("Notificação Telegram não enviada: Nenhum ID de usuário autorizado em TELEGRAM_AUTHORIZED_USER_IDS.")
        return 0
        
    sent_count = 0
    for chat_id in authorized_ids:
        try:
            bot.send_message(chat_id, message_text, parse_mode=parse_mode)
            sent_count += 1
        except Exception as e:
            # Fallback sem parse_mode caso ocorra erro de formatação Markdown
            try:
                bot.send_message(chat_id, message_text)
                sent_count += 1
            except Exception as ex_fallback:
                logger.error(f"Falha ao enviar mensagem Telegram para chat_id {chat_id}: {ex_fallback}")
                
    return sent_count

