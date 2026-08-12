# speech-to-text tool

import speech_recognition as sr

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
    """Carrega um arquivo de áudio WAV local, detecta a duração e faz a transcrição (em blocos se for longo)."""
    r = sr.Recognizer()
    try:
        with sr.AudioFile(file_path) as source:
            duration = source.DURATION
            print(f"Lendo áudio do arquivo: {file_path}. Duração total: {duration:.2f}s")
            
            # Se o áudio for curto (até 60s), lê tudo de uma vez
            if duration <= 60:
                audio_data = r.record(source)
                text = r.recognize_google(audio_data, language="pt-BR")
                return text
                
            # Áudio longo: transcrever em pedaços de 45 segundos para evitar erros de timeout e tamanho
            chunk_size = 45
            text_chunks = []
            
            for i, offset in enumerate(range(0, int(duration), chunk_size)):
                # Grava o próximo chunk do arquivo
                audio_data = r.record(source, duration=chunk_size)
                try:
                    text = r.recognize_google(audio_data, language="pt-BR")
                    if text.strip():
                        text_chunks.append(text)
                except sr.UnknownValueError:
                    # Silêncio ou incompreensível
                    pass
                except sr.RequestError as e:
                    print(f"Erro no serviço de reconhecimento no bloco {i+1}: {e}")
                    # Continua para o próximo bloco
                    
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

if __name__ == "__main__":
    listen()
