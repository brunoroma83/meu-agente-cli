from .speech_to_text import transcrever_arquivo_audio

arquivos = ['6-oct.mp3']

for i, a in enumerate(arquivos):
    print(f"Transcrevendo: {a} ... ({i+1}/{len(arquivos)})")
    tras = transcrever_arquivo_audio(f"./uploads/{a}")
    try:
        with open(f"./uploads/{a}.txt", "w", encoding="utf-8") as f:
            f.write(tras)
        print(f"Texto transcrito: {tras[:200]}...")
    except Exception as e:
        with open(f"./uploads/temp-error-{i}.txt", "w", encoding="utf-8") as f:
            f.write(tras)
        print(f"Texto transcrito: {tras[:200]}...")
        


