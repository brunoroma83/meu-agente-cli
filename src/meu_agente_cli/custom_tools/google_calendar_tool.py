import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
from meu_agente_cli.google_auth import get_google_credentials

# Fuso horário padrão do Brasil (Horário de Brasília / UTC-3)
TZ_BRASIL = timezone(timedelta(hours=-3))

def parse_date_bound(val: Optional[str], is_end: bool = False, default_dt: Optional[datetime] = None) -> Optional[datetime]:
    """Interpreta formatos variados de datas (ISO, DD/MM/YYYY, DD/MM, today, tomorrow)."""
    if not val:
        return default_dt
    
    val_clean = str(val).strip().lower()
    now = datetime.now(TZ_BRASIL)
    
    if val_clean in ("today", "hoje"):
        base = now
    elif val_clean in ("tomorrow", "amanha", "amanhã"):
        base = now + timedelta(days=1)
    elif val_clean in ("yesterday", "ontem"):
        base = now - timedelta(days=1)
    else:
        # Regex para YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS
        m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?", val_clean)
        # Regex para DD/MM/YYYY ou DD/MM
        m_br = re.match(r"^(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", val_clean)
        
        if m_iso:
            y, m, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
            hh = int(m_iso.group(4)) if m_iso.group(4) is not None else (23 if is_end else 0)
            mm = int(m_iso.group(5)) if m_iso.group(5) is not None else (59 if is_end else 0)
            ss = int(m_iso.group(6)) if m_iso.group(6) is not None else (59 if is_end else 0)
            return datetime(y, m, d, hh, mm, ss, tzinfo=TZ_BRASIL)
        elif m_br:
            d, m = int(m_br.group(1)), int(m_br.group(2))
            y = int(m_br.group(3)) if m_br.group(3) else now.year
            hh = 23 if is_end else 0
            mm = 59 if is_end else 0
            ss = 59 if is_end else 0
            return datetime(y, m, d, hh, mm, ss, tzinfo=TZ_BRASIL)
        else:
            try:
                dt = datetime.fromisoformat(val_clean.replace("z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=TZ_BRASIL)
                return dt
            except Exception:
                return default_dt

    if is_end:
        return base.replace(hour=23, minute=59, second=59, microsecond=0)
    else:
        return base.replace(hour=0, minute=0, second=0, microsecond=0)

def format_event_datetime(dt_str: str) -> str:
    """Formata strings de data/hora da API do Google para exibição amigável em português."""
    if not dt_str:
        return "Sem data"
    
    # Evento de dia inteiro (ex: "2026-09-23")
    if len(dt_str) == 10 and "-" in dt_str:
        try:
            parts = dt_str.split("-")
            return f"{parts[2]}/{parts[1]}/{parts[0]} (Dia inteiro)"
        except Exception:
            return dt_str
            
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        # Converte para fuso de Brasília
        dt_local = dt.astimezone(TZ_BRASIL)
        dias = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
        dia_nome = dias[dt_local.weekday()]
        return f"{dia_nome}, {dt_local.strftime('%d/%m/%Y às %H:%M')}"
    except Exception:
        return dt_str

def get_target_calendars(service, calendar_id: str) -> List[Tuple[str, str]]:
    """Retorna lista de tuplas (calendar_id, calendar_name) para pesquisa."""
    if calendar_id and calendar_id.lower() not in ("all", "todas", "auto", "default"):
        return [(calendar_id, calendar_id)]
    
    try:
        cal_list = service.calendarList().list().execute()
        calendars = []
        for item in cal_list.get("items", []):
            cid = item.get("id")
            cname = item.get("summary", cid)
            calendars.append((cid, cname))
        return calendars if calendars else [("primary", "Principal")]
    except Exception:
        return [("primary", "Principal")]

def run(**kwargs):
    """
    Ferramenta para consultar e gerenciar eventos no Google Calendar.
    Busca automaticamente em todas as agendas do usuário por padrão.
    """
    creds = get_google_credentials()
    if not creds:
        return (
            "Erro: Credenciais do Google Calendar não encontradas ou não autorizadas no Docker.\n"
            "Para autorizar, execute no terminal do projeto o comando:\n"
            "  docker compose exec agent uv run python -m meu_agente_cli.google_auth\n"
            "Certifique-se de que o arquivo 'credentials.json' está presente na pasta raiz."
        )

    try:
        from googleapiclient.discovery import build
        service = build("calendar", "v3", credentials=creds)
    except Exception as e:
        return f"Erro ao inicializar serviço Google Calendar: {e}"

    action = kwargs.get("action", "list").lower()
    calendar_id = kwargs.get("calendar_id", "all")

    if action == "list":
        return list_events(service, calendar_id, kwargs)
    elif action == "create":
        return create_event(service, calendar_id, kwargs)
    elif action == "delete":
        return delete_event(service, calendar_id, kwargs)
    else:
        return f"Erro: Ação '{action}' não suportada. Use: 'list', 'create' ou 'delete'."

def list_events(service, calendar_id: str, kwargs: dict) -> str:
    now = datetime.now(TZ_BRASIL)
    
    # Suporte a start_date / end_date ou custom_start / custom_end
    start_param = kwargs.get("start_date") or kwargs.get("custom_start")
    end_param = kwargs.get("end_date") or kwargs.get("custom_end")
    time_range = kwargs.get("time_range", "week").lower()

    if start_param or end_param:
        default_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        default_end = default_start + timedelta(days=7, hours=23, minutes=59, seconds=59)
        dt_start = parse_date_bound(start_param, is_end=False, default_dt=default_start)
        dt_end = parse_date_bound(end_param, is_end=True, default_dt=default_end)
        desc_periodo = f"de {dt_start.strftime('%d/%m/%Y')} até {dt_end.strftime('%d/%m/%Y')}"
    else:
        if time_range == "today" or time_range == "hoje":
            dt_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            dt_end = now.replace(hour=23, minute=59, second=59, microsecond=0)
            desc_periodo = f"hoje ({now.strftime('%d/%m/%Y')})"
        elif time_range == "tomorrow" or time_range == "amanhã" or time_range == "amanha":
            dt_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            dt_end = (now + timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=0)
            desc_periodo = f"amanhã ({dt_start.strftime('%d/%m/%Y')})"
        elif time_range == "week" or time_range == "semana":
            # Da data/hora atual até 7 dias corridos à frente
            dt_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            dt_end = (now + timedelta(days=7)).replace(hour=23, minute=59, second=59, microsecond=0)
            desc_periodo = f"próximos 7 dias ({dt_start.strftime('%d/%m')} a {dt_end.strftime('%d/%m/%Y')})"
        elif time_range == "next_week" or time_range == "próxima semana":
            start_next = now + timedelta(days=(7 - now.weekday()))
            dt_start = start_next.replace(hour=0, minute=0, second=0, microsecond=0)
            dt_end = (dt_start + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=0)
            desc_periodo = f"próxima semana ({dt_start.strftime('%d/%m')} a {dt_end.strftime('%d/%m/%Y')})"
        elif time_range == "month" or time_range == "mês" or time_range == "mes":
            dt_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if now.month == 12:
                next_month = now.replace(year=now.year + 1, month=1, day=1)
            else:
                next_month = now.replace(month=now.month + 1, day=1)
            dt_end = (next_month - timedelta(seconds=1)).replace(tzinfo=TZ_BRASIL)
            desc_periodo = f"mês atual ({dt_start.strftime('%B/%Y')})"
        else:
            # Fallback para 7 dias se parâmetro desconhecido
            dt_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            dt_end = (now + timedelta(days=7)).replace(hour=23, minute=59, second=59, microsecond=0)
            desc_periodo = f"período ({dt_start.strftime('%d/%m')} a {dt_end.strftime('%d/%m/%Y')})"

    time_min_iso = dt_start.isoformat()
    time_max_iso = dt_end.isoformat()

    targets = get_target_calendars(service, calendar_id)
    all_events = []
    seen_ids = set()

    for cid, cname in targets:
        try:
            res = service.events().list(
                calendarId=cid,
                timeMin=time_min_iso,
                timeMax=time_max_iso,
                singleEvents=True,
                orderBy="startTime"
            ).execute()

            for item in res.get("items", []):
                eid = item.get("id")
                # Deduplicação caso o mesmo evento esteja em mais de uma agenda
                if eid and eid not in seen_ids:
                    seen_ids.add(eid)
                    all_events.append((item, cname))
        except Exception:
            continue

    # Ordena todos os eventos cronologicamente pela data de início
    def get_sort_key(entry):
        item, _ = entry
        st = item.get("start", {})
        return st.get("dateTime", st.get("date", ""))

    if not all_events:
        nomes_agendas = ", ".join([cname for _, cname in targets[:3]])
        if len(targets) > 3:
            nomes_agendas += f" e mais {len(targets) - 3} agendas"

        # Se a busca foi em uma única data futura e retornou vazia, verifica compromissos entre hoje e essa data
        sugestao = ""
        if dt_start.date() > now.date() and (dt_end.date() - dt_start.date()).days <= 1:
            try:
                res_inter = []
                seen_inter = set()
                for cid, cname in targets:
                    try:
                        r = service.events().list(
                            calendarId=cid,
                            timeMin=now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                            timeMax=dt_end.isoformat(),
                            singleEvents=True,
                            orderBy="startTime"
                        ).execute()
                        for it in r.get("items", []):
                            eid = it.get("id")
                            if eid and eid not in seen_inter:
                                seen_inter.add(eid)
                                res_inter.append((it, cname))
                    except Exception:
                        pass
                if res_inter:
                    res_inter.sort(key=get_sort_key)
                    sugestao = f"\n\n💡 *Porém, foram encontrados {len(res_inter)} compromissos entre hoje ({now.strftime('%d/%m')}) e o dia {dt_end.strftime('%d/%m')}:*\n"
                    for it, cn in res_inter:
                        st_str = format_event_datetime(it.get("start", {}).get("dateTime", it.get("start", {}).get("date", "")))
                        sugestao += f"• **{it.get('summary')}** ({st_str} - Agenda: {cn})\n"
            except Exception:
                pass

        return f"Nenhum compromisso encontrado para o período {desc_periodo} nas agendas ({nomes_agendas}).{sugestao}"

    all_events.sort(key=get_sort_key)

    lines = [f"📅 **Compromissos Encontrados ({len(all_events)}) - Período: {desc_periodo}:**\n"]
    for item, cname in all_events:
        summary = item.get("summary", "(Sem título)")
        start_str = format_event_datetime(item.get("start", {}).get("dateTime", item.get("start", {}).get("date", "")))
        end_str = format_event_datetime(item.get("end", {}).get("dateTime", item.get("end", {}).get("date", "")))
        location = item.get("location")
        desc = item.get("description")
        event_id = item.get("id", "")

        event_text = f"• **{summary}**\n  - 🗓️ **Quando:** {start_str} até {end_str}\n  - 📁 **Agenda:** {cname}"
        if location:
            event_text += f"\n  - 📍 **Local:** {location}"
        if desc:
            clean_desc = desc.strip().replace("\n", " ")
            if len(clean_desc) > 120:
                clean_desc = clean_desc[:120] + "..."
            event_text += f"\n  - 📝 **Detalhes:** {clean_desc}"
        event_text += f"\n  - 🆔 `ID: {event_id}`"

        lines.append(event_text)

    return "\n\n".join(lines)

def create_event(service, calendar_id: str, kwargs: dict) -> str:
    summary = kwargs.get("summary")
    start_time = kwargs.get("start_time")
    end_time = kwargs.get("end_time")

    if not summary:
        return "Erro: 'summary' (título do evento) é obrigatório para criar um evento."
    if not start_time:
        return "Erro: 'start_time' (data/hora início ISO) é obrigatório."
    if not end_time:
        try:
            st = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            end_time = (st + timedelta(hours=1)).isoformat()
        except Exception:
            return "Erro: Formato de 'start_time' inválido. Use ISO 8601 ex: '2026-09-25T14:00:00'."

    # Se calendar_id for 'all', usa 'primary' para criar o evento
    target_cal = "primary" if calendar_id in ("all", "todas", "auto", "default") else calendar_id

    event_body = {
        "summary": summary,
        "description": kwargs.get("description", ""),
        "location": kwargs.get("location", ""),
        "start": {"dateTime": start_time} if "T" in start_time else {"date": start_time},
        "end": {"dateTime": end_time} if "T" in end_time else {"date": end_time},
    }

    try:
        created = service.events().insert(calendarId=target_cal, body=event_body).execute()
        html_link = created.get("htmlLink", "")
        return f"✅ Evento '{summary}' criado com sucesso na agenda!\nID: {created.get('id')}\nInício: {start_time}\nFim: {end_time}\nLink: {html_link}"
    except Exception as e:
        return f"Erro ao criar evento na agenda: {e}"

def delete_event(service, calendar_id: str, kwargs: dict) -> str:
    event_id = kwargs.get("event_id")
    if not event_id:
        return "Erro: 'event_id' é obrigatório para excluir um evento."
    
    target_cal = "primary" if calendar_id in ("all", "todas", "auto", "default") else calendar_id
    try:
        service.events().delete(calendarId=target_cal, eventId=event_id).execute()
        return f"✅ Evento ID '{event_id}' excluído com sucesso."
    except Exception as e:
        return f"Erro ao excluir evento: {e}"
