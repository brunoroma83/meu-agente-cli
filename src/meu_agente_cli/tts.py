"""
Módulo de Síntese de Voz (Text-to-Speech - TTS)
Utiliza a biblioteca edge-tts para gerar áudios em MP3 de alta fidelidade
com vozes neurais em português brasileiro (pt-BR).
"""

import asyncio
import os
import re
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import edge_tts
except ImportError:
    edge_tts = None

# Mapeamento de vozes amigáveis
VOICES = {
    "francisca": "pt-BR-FranciscaNeural",
    "feminina": "pt-BR-FranciscaNeural",
    "antonio": "pt-BR-AntonioNeural",
    "masculina": "pt-BR-AntonioNeural",
    "thalita": "pt-BR-ThalitaNeural",
}
DEFAULT_VOICE = "pt-BR-FranciscaNeural"

# Diretório padrão para armazenamento de áudios gerados
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
TTS_OUTPUT_DIR = PROJECT_ROOT / "uploads" / "archive" / "audio" / "tts"

def get_voice_identifier(voice_name: Optional[str]) -> str:
    """Retorna o identificador oficial do edge-tts para o nome da voz fornecido."""
    if not voice_name:
        return DEFAULT_VOICE
    v_clean = voice_name.strip().lower()
    return VOICES.get(v_clean, voice_name if "Neural" in voice_name else DEFAULT_VOICE)

def sanitize_filename(title: str) -> str:
    """Gera um nome de arquivo seguro a partir do título."""
    clean = re.sub(r'[^a-zA-Z0-9_\-]', '_', title)
    clean = re.sub(r'_+', '_', clean).strip('_')
    return clean[:40] if clean else "audio"

async def _synthesize_async(text: str, output_path: str, voice: str, rate: str = "+0%") -> str:
    """Executa a síntese de fala assíncrona usando edge-tts."""
    if edge_tts is None:
        raise RuntimeError("A biblioteca 'edge-tts' não está instalada. Execute 'uv add edge-tts'.")
        
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_path)
    return output_path

def synthesize_speech(
    text: str,
    output_path: Optional[str] = None,
    voice: str = "francisca",
    rate: str = "+0%",
    title: Optional[str] = None
) -> str:
    """
    Sintetiza texto em arquivo de áudio MP3.
    Retorna o caminho absoluto do arquivo MP3 gerado.
    """
    if not text or not text.strip():
        raise ValueError("O texto para síntese de áudio não pode ser vazio.")

    TTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    voice_id = get_voice_identifier(voice)

    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_slug = sanitize_filename(title or "audio_resumo")
        output_path = str((TTS_OUTPUT_DIR / f"{timestamp}_{file_slug}.mp3").resolve())
    else:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        output_path = str(out_p.resolve())

    # Trata loop assíncrono caso já estejamos dentro de um (ex: Telegram / asyncio)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Se já estivermos em um event loop rodando, usa uma thread separada para não travar
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(lambda: asyncio.run(_synthesize_async(text, output_path, voice_id, rate)))
            res_path = future.result()
    else:
        res_path = asyncio.run(_synthesize_async(text, output_path, voice_id, rate))

    set_last_generated_audio(res_path)
    return res_path

# Armazena em memória o último arquivo de áudio gerado para entrega direta por clientes como Telegram
_last_generated_audio: Optional[str] = None

def set_last_generated_audio(path: str):
    global _last_generated_audio
    _last_generated_audio = path

def get_last_generated_audio() -> Optional[str]:
    global _last_generated_audio
    return _last_generated_audio

def pop_last_generated_audio() -> Optional[str]:
    """Retorna e limpa o caminho do último áudio sintetizado."""
    global _last_generated_audio
    path = _last_generated_audio
    _last_generated_audio = None
    return path

