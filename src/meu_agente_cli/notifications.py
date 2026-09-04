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

def check_and_build_due_bills_report(target_date: Optional[date] = None) -> str:
    """
    Consulta despesas com vencimento hoje e nos próximos 2 dias, gerando um relatório formatado.
    """
    if target_date is None:
        target_date = datetime.now().date()
        
    today_str = target_date.strftime("%Y-%m-%d")
    today_display = target_date.strftime("%d/%m/%Y")
    
    # Busca contas vencendo hoje
    today_bills = db.search_financial_records(due_date=today_str, record_type="despesa")
    
    # Busca contas vencendo nos próximos 2 dias
    next_start = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")
    next_end = (target_date + timedelta(days=2)).strftime("%Y-%m-%d")
    upcoming_bills = db.search_financial_records(
        start_due_date=next_start,
        end_due_date=next_end,
        record_type="despesa"
    )
    
    lines = []
    lines.append("🔔 *Alerta Financeiro Diário (11:00)*\n")
    
    total_today = 0.0
    if today_bills:
        lines.append(f"📅 *Contas Vencendo Hoje ({today_display}):*")
        for bill in today_bills:
            rec_id, _, cat, val, desc, _, due_dt = bill
            total_today += float(val)
            desc_part = f" - {desc}" if desc else ""
            lines.append(f"• *{cat}*: R$ {val:.2f}{desc_part}")
        lines.append(f"👉 *Total Hoje:* `R$ {total_today:.2f}`\n")
    else:
        lines.append(f"✅ *Hoje ({today_display}):* Nenhuma conta com vencimento para hoje!\n")
        
    if upcoming_bills:
        lines.append("⏳ *Próximos 2 Dias:*")
        total_upcoming = 0.0
        for bill in upcoming_bills:
            rec_id, _, cat, val, desc, _, due_dt = bill
            total_upcoming += float(val)
            due_str = due_dt.strftime("%d/%m") if due_dt else "N/D"
            desc_part = f" - {desc}" if desc else ""
            lines.append(f"• *{cat}*: R$ {val:.2f} (Venc: {due_str}){desc_part}")
        lines.append(f"👉 *Total Próximos Dias:* `R$ {total_upcoming:.2f}`\n")
        
    if not today_bills and not upcoming_bills:
        lines.append("🎉 *Tudo em dia!* Nenhuma conta pendente para hoje ou para os próximos dias.")
        
    # Adiciona resumo do teto diário de gastos
    try:
        budget = db.get_daily_budget_summary()
        teto = budget.get("teto_diario", 0.0)
        livre_rest = budget.get("saldo_livre_restante", 0.0)
        dias_rest = budget.get("dias_restantes", 1)
        if teto > 0 or livre_rest > 0:
            lines.append("\n🎯 *Disponibilidade para Gastos Diários:*")
            lines.append(f"• Teto sugerido para hoje: `R$ {teto:.2f}/dia` (restam {dias_rest} dias)")
            lines.append(f"• Saldo livre disponível no mês: `R$ {livre_rest:.2f}`")
    except Exception:
        pass
        
    return "\n".join(lines)

def send_due_bills_alert() -> str:
    """Executa a verificação de contas e envia o alerta proativo via Telegram."""
    report = check_and_build_due_bills_report()
    sent = send_telegram_broadcast(report)
    return f"Alerta financeiro gerado com sucesso. Enviado para {sent} destinatário(s) no Telegram.\n\n{report}"
