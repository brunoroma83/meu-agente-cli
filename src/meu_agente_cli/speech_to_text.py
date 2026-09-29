# speech-to-text tool
from tqdm import tqdm
import speech_recognition as sr
import subprocess
from pathlib import Path

def listen(self=None):
    r = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening...")
        r.pause_threshold = 1
        audio = r.listen(source)
    return transcrever_audio(audio)

def transcrever_audio(audio):
    r = sr.Recognizer()
    try:
        print("Recognizing...")
        text = r.recognize_google(audio, language="pt-BR")
        print(f"Transcrição: {text}")
        return text
    except sr.UnknownValueError:
        print("Não foi possível entender o áudio")
        return ""
    except sr.RequestError as e:
        print(f"Não foi possível obter resultados do serviço Google Speech Recognition; {e}")
        return ""

def transcrever_arquivo_audio(file_path: str) -> str:
    """
    Carrega um arquivo de áudio local (M4A, MP3, OGG, WAV, etc.), 
    converte para WAV PCM 16kHz Mono via ffmpeg se necessário, 
    detecta a duração e faz a transcrição (em blocos se for longo).
    """
    p = Path(file_path)
    if not p.exists():
        print(f"Erro: Arquivo não encontrado: {file_path}")
        return ""

    r = sr.Recognizer()
    target_wav = p
    temp_converted = False

    # Converte para WAV PCM 16kHz mono se não for .wav ou para garantir formato compatível
    if p.suffix.lower() != ".wav":
        target_wav = p.parent / f"{p.stem}_tmp_transcribe.wav"
        cmd = [
            "ffmpeg", "-y",
            "-i", str(p),
            "-acodec", "pcm_s16le",
            "-ac", "1",
            "-ar", "16000",
            str(target_wav)
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode != 0:
                err_msg = res.stderr.decode("utf-8", errors="ignore")
                print(f"Erro ao converter áudio com ffmpeg: {err_msg}")
                return ""
            temp_converted = True
        except Exception as e:
            print(f"Erro ao executar ffmpeg para conversão: {e}")
            return ""

    try:
        with sr.AudioFile(str(target_wav)) as source:
            duration = source.DURATION
            print(f"Lendo áudio do arquivo: {p.name}. Duração total: {duration:.2f}s")
            
            # Se o áudio for curto (até 60s), lê tudo de uma vez
            if duration <= 60:
                print("Áudio curto, transcrevendo...")
                audio_data = r.record(source)
                text = r.recognize_google(audio_data, language="pt-BR")
                return text
                
            # Áudio longo: transcrever em pedaços de 300 segundos para evitar erros de timeout e tamanho
            chunk_size = 300
            text_chunks = []
            print("Áudio longo, transcrevendo...")
            with tqdm(total=int(duration), desc="Transcrevendo", unit="s") as pbar:
                for i, offset in enumerate(range(0, int(duration), chunk_size)):
                    step = min(chunk_size, int(duration) - offset)
                    audio_data = r.record(source, duration=chunk_size)
                    try:
                        text = r.recognize_google(audio_data, language="pt-BR")
                        if text.strip():
                            text_chunks.append(text)
                    except sr.UnknownValueError:
                        pass
                    except sr.RequestError as e:
                        print(f"Erro no serviço de reconhecimento no bloco {i+1}: {e}")
                    pbar.update(step)
                    
            return " ".join(text_chunks)
            
    except sr.UnknownValueError:
        print("Google Speech Recognition não conseguiu entender o áudio do arquivo.")
        return ""
    except sr.RequestError as e:
        print(f"Erro ao contatar o serviço de Speech Recognition do Google: {e}")
        return ""
    except Exception as e:
        print(f"Erro geral no processamento de transcrição de arquivo: {e}")
        return ""
    finally:
        if temp_converted and target_wav.exists():
            try:
                target_wav.unlink()
            except Exception:
                pass

if __name__ == "__main__":
    listen()
