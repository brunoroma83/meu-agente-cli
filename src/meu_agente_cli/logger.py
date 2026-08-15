import os
import logging
from logging.handlers import RotatingFileHandler
from meu_agente_cli.config import CONFIG_DIR, load_bootstrap_config

def setup_logging():
    """
    Configura o sistema de logging do Python.
    Salva os logs em um arquivo rotativo no diretório de configuração do usuário.
    """
    # Garante que o diretório de configurações exista
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        
    log_file = CONFIG_DIR / "agent.log"
    
    # Carrega a configuração local para obter o nível de log
    config = load_bootstrap_config()
    log_level_str = config.get("log_level", "INFO").upper()
    
    # Mapeia a string para a constante de log correspondente
    level = getattr(logging, log_level_str, logging.INFO)
    
    # Configura o formato
    log_format = "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
    formatter = logging.Formatter(log_format)
    
    # Cria o handler rotativo (5MB por arquivo, no máximo 3 arquivos de backup)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    
    # Obtém o logger raiz e limpa handlers existentes para evitar duplicação no reload
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove handlers antigos se houver
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        
    root_logger.addHandler(file_handler)
    
    # Evita que logs de terceiros muito verbosos fiquem poluindo no nível DEBUG
    # a menos que o nível geral seja de fato DEBUG
    if level > logging.DEBUG:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("yfinance").setLevel(logging.WARNING)
        logging.getLogger("speech_recognition").setLevel(logging.WARNING)
    
    logging.info("Sistema de logs inicializado. Nível de log: %s. Arquivo: %s", log_level_str, log_file)
