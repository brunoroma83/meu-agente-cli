# ferramenta que pesquisa alertas de tecnovigilancia
import re
import json
from meu_agente_cli.db import get_connection

BASE_URL = 'https://www.anvisa.gov.br/sistec/Alerta/RelatorioAlerta.asp?NomeColuna=CO_SEQ_ALERTA&Parametro={}'

def sanitizar_nome_arquivo(nome: str) -> str:
    """Remove caracteres inválidos para nomes de arquivos."""
    return re.sub(r'[\\/*?:"<>| ]', '_', nome)


def buscar_alertas(**kwargs):
    query = kwargs.get('query', '').strip()
    
    if len(query) < 3:
        return "Por favor informe uma palavra com no mínimo 3 caracteres."

    print(f"query: {query}")
    print(f"Kwargs: {kwargs}")

    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            # Busca prioritária por número do alerta se for um dígito pequeno
            if query.isdigit() and len(query) < 6:
                comando = """
                    SELECT numero_alerta, COALESCE(resumo, conteudo) 
                    FROM tecnovigilancia 
                    WHERE numero_alerta = %s
                """
                cur.execute(comando, (int(query),))
            else:
                # Busca textual no resumo ou no conteúdo original caso o resumo ainda não exista
                comando = """
                    SELECT numero_alerta, COALESCE(resumo, conteudo) 
                    FROM tecnovigilancia 
                    WHERE resumo ILIKE %s OR conteudo ILIKE %s
                    ORDER BY numero_alerta DESC 
                    LIMIT 5
                """
                termo_busca = f"%{query}%"
                cur.execute(comando, (termo_busca, termo_busca))
            
            result = cur.fetchall()

            # Escrita segura e estruturada do log se houver resultados
            if result:
                nome_seguro = sanitizar_nome_arquivo(query)
                caminho_arquivo = f"./alertas_tecnovigilancia_{nome_seguro}.txt"
                try:
                    # Salva no formato JSON formatado para melhor leitura
                    dados_salvar = [
                        {"numero_alerta": row[0], "conteudo": row[1]} 
                        for row in result
                    ]
                    with open(caminho_arquivo, "w", encoding="utf-8") as f:
                        json.dump(dados_salvar, f, indent=4, ensure_ascii=False)
                except Exception as file_err:
                    print(f"Erro ao salvar arquivo de log temporário: {file_err}")

            if len(result) == 0:
                return "Nenhum alerta encontrado."
            
            # Formatação amigável das respostas
            if len(result) == 1:
                numero_alerta, texto = result[0]
                return (
                    f"Sua busca teve 1 resultado:\n\n"
                    f"### Alerta {numero_alerta}\n"
                    f"{texto}"
                )
            
            mensagem = f"Sua busca resultou em {len(result)} resultados. Apresentando os primeiros resultados:\n\n"
            for i, (numero_alerta, texto) in enumerate(result):
                # Limita o texto a 800 caracteres com indicador de corte
                texto_exibido = texto[:800] if texto else ""
                if texto and len(texto) > 800:
                    texto_exibido += "..."

                alerta_url = BASE_URL.format(numero_alerta)
                
                mensagem += f"{i+1}. Alerta {numero_alerta}\nURL: {alerta_url}\n{texto_exibido}\n\n"

                mensagem += "Instruções: incluir as URLs na respota, não utilize negrito, itálico, underline."
            
            return mensagem

    except Exception as e:
        return f"Erro ao buscar alertas: {e}"
    
    finally:
        # Garante o fechamento da conexão mesmo em caso de erros
        if conn:
            conn.close()


def run(**kwargs):
    query = kwargs.get('query', '')
    return buscar_alertas(query=query)


# inicio
if __name__ == "__main__":
    print(buscar_alertas(query='3469'))