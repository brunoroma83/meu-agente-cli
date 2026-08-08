import subprocess
import json
from datetime import datetime, timedelta

def run(**kwargs):
    """
    Consulta eventos do Google Calendar usando o comando 'gws'.
    
    Args:
        calendar_id (str, optional): ID do calendário. Padrão: 'bruuno@gmail.com'.
        time_range (str, optional): 'today', 'tomorrow', 'week', 'next_week', 'month'. Padrão: 'week'.
        custom_start (str, optional): Data/hora início no formato ISO 8601 (YYYY-MM-DDTHH:MM:SSZ).
        custom_end (str, optional): Data/hora fim no formato ISO 8601 (YYYY-MM-DDTHH:MM:SSZ).
    """
    calendar_id = kwargs.get('calendar_id', 'bruuno@gmail.com')
    time_range = kwargs.get('time_range', 'week')
    custom_start = kwargs.get('custom_start')
    custom_end = kwargs.get('custom_end')
    
    now = datetime.utcnow()
    
    if custom_start and custom_end:
        time_min = custom_start
        time_max = custom_end
    else:
        if time_range == 'today':
            time_min = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'
            time_max = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'
        elif time_range == 'tomorrow':
            start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            time_min = start.isoformat() + 'Z'
            time_max = (start + timedelta(days=1)).isoformat() + 'Z'
        elif time_range == 'week':
            start = now - timedelta(days=now.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            time_min = start.isoformat() + 'Z'
            time_max = (start + timedelta(days=7)).isoformat() + 'Z'
        elif time_range == 'next_week':
            start = now - timedelta(days=now.weekday()) + timedelta(days=7)
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            time_min = start.isoformat() + 'Z'
            time_max = (start + timedelta(days=7)).isoformat() + 'Z'
        elif time_range == 'month':
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            time_min = start.isoformat() + 'Z'
            # Próximo mês
            if now.month == 12:
                end = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            else:
                end = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
            time_max = end.isoformat() + 'Z'
        else:
            return f"Erro: time_range '{time_range}' não suportado. Use: today, tomorrow, week, next_week, month."

    params = {
        "calendarId": calendar_id,
        "timeMin": time_min,
        "timeMax": time_max
    }
    
    params_json = json.dumps(params)
    command = f"gws calendar events list --params '{params_json}'"
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            return f"Erro ao executar comando: {result.stderr}"
        
        return result.stdout
    except subprocess.TimeoutExpired:
        return "Erro: Comando demorou mais de 30 segundos."
    except Exception as e:
        return f"Erro inesperado: {str(e)}"
