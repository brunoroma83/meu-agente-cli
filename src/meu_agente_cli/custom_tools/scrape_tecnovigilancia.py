from datetime import datetime
import requests
from bs4 import BeautifulSoup
import time
import meu_agente_cli.db as db
                                                                                                                                                   
# Configurações
BASE_URL = 'https://www.anvisa.gov.br/sistec/Alerta/RelatorioAlerta.asp?NomeColuna=CO_SEQ_ALERTA&Parametro={}'
MAX_ITERATIONS = 30


def tratar_texto(texto: str) -> str:
    """
    Trata e sanitiza o texto extraído para evitar erros de inserção no banco de dados.
    Remove caracteres nulos (\x00) e caracteres surrogate inválidos,
    e retira espaços extras nas pontas.
    """
    if not texto:
        return ""
    
    # Remove caractere nulo (\x00) que o PostgreSQL rejeita em strings
    texto = texto.replace('\x00', '')
    
    # Trata caracteres surrogate inválidos
    try:
        texto = texto.encode('utf-8', errors='surrogateescape').decode('utf-8', errors='replace')
    except Exception:
        pass
        
    # Filtra mantendo apenas caracteres imprimíveis e quebras de linha/tabs padrão
    texto = "".join(c for c in texto if c.isprintable() or c in ('\n', '\r', '\t')) #' ' is a non-breaking space character
    
    search_word = 'Código'
    pos = texto.find(search_word)

    if pos != -1:
        resultado = texto[pos:].strip()  # .strip() limpa espaços em branco extras no início/fim
    else:
        resultado = ''
    
    return resultado


def setup_database():
    """Cria a tabela no banco PostgreSQL se não existir."""
    conn = db.get_connection()
    cursor = conn.cursor()
                                                                                                                                                
    # Criação da tabela com campos genéricos.
    # ATENÇÃO: O usuário deve ajustar os nomes das colunas conforme o HTML real da página.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tecnovigilancia (
            id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            numero_alerta INT UNIQUE,  
            data_alerta DATE DEFAULT NULL,    
            conteudo TEXT NOT NULL,
            resumo TEXT DEFAULT NULL
        )
    ''')
    conn.commit()
    print("[OK] Conexão com banco de dados estabelecida.")
    return conn
                                                                                                                                                
def scrape_and_save():
    """Executa o loop """
    conn = setup_database()
    cursor = conn.cursor()
                                                                                                                                                
    # Headers para imitar um navegador legítimo (pode não ser suficiente contra Cloudflare)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',      
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7'
    }

    # consultar o número do último alerta e data do último alerta no banco de dados
    cursor.execute('SELECT numero_alerta, data_alerta FROM tecnovigilancia ORDER BY numero_alerta DESC LIMIT 1')
    ultimo_alerta = cursor.fetchone()
    if ultimo_alerta:
        i = ultimo_alerta[0] + 1
        data_anterior = ultimo_alerta[1]
        print(f"Último alerta encontrado: {ultimo_alerta[0]}. Iniciando a partir do alerta {i}.")
    else:
        i = 1
        data_anterior = None
        print("Nenhum alerta encontrado. Iniciando a partir do alerta 1.")
    
                                                                                                                                                
    print(f"Iniciando scraping... (Máximo {MAX_ITERATIONS} iterações)")
                                                                                                                                                
    for i in range(i, i + MAX_ITERATIONS + 1):
        # preciso comparar se a data do alerta a ser lido é igual ou mais recente que o alerta lido anteriormente para continuar
        # caso contrário, o for deve parar
        
        url = BASE_URL.format(i)
        
        # inicia lista resposta
        lista_resposta = []
                                                                                                                                                
        try:
            response = requests.get(url, headers=headers, timeout=15)
            print(f"Acessando: {url}")                                                                                                                
                                                                                                                                                
            # Verificação de bloqueio Cloudflare (baseado no conteúdo da página anterior)
            if 'Cloudflare' in response.text or 'blocked' in str(response.status_code).lower():
                print(f"[Bloqueio] i={i} - Acesso negado por segurança. Parando para evitar bans.")
                break
                                                                                                                                                
            # Verificação básica de status HTTP
            if response.status_code != 200:
                print(f"[Erro HTTP] i={i} - Status {response.status_code}")
                continue

            soup = BeautifulSoup(response.text, 'html.parser')
                                                                                                                                                
            # Localizar a tabela. O selector depende da estrutura HTML real.
            # Exemplo comum para tabelas de alertas:
            
            tables = soup.find_all('table')
            table = None
            for t in tables:
                if t.get_text().strip().find("Código da Classe") > 0:
                    table = t.get_text().strip()
                    break

            if table is None:
                print(f"[Sem Tabela] i={i} - Não encontrou tabela na página.")
                continue

            #retirar informação de data da ocorrência
            linhas = table.split('\n')
            for j in range(len(linhas)):
                if 'data ocorrência' in linhas[j].lower():
                    data_str = linhas[j+1].strip()[:10]
                    data_obj = datetime.strptime(data_str, '%d/%m/%Y').date()
                    break
            
            # verficar data anterior
            if data_anterior is not None:
                if data_obj < data_anterior:
                    print("Data anterior ao alerta anterior. Parando.")
                    break
            data_anterior = data_obj # na próxima iteração a data_anterior está definida

            conteudo_tratado = tratar_texto(table)
            
            cursor.execute(
                'INSERT INTO tecnovigilancia (numero_alerta, data_alerta, conteudo) VALUES (%s, %s, %s) ON CONFLICT (numero_alerta) DO NOTHING',
                (i, data_obj, conteudo_tratado)
            )                                                                                                                              
            conn.commit()
            lista_resposta.append(f"Alerta encontrado: {i}")
                                                                                                                                                
        except Exception as e:
            print(f"[Erro] i={i} - {str(e)}")
                                                                                                                                                
        # Delay para ser "polite" ao servidor (evita sobrecarga)
        time.sleep(0.4)
                                                                                                                                                
    conn.close()
    lista_resposta.append(f"Foram encontrados um total de {len(lista_resposta)} alertas de tecnovigilância")
    return "\n".join(lista_resposta)
                                                                                                                                                
def run(**kwargs):
    return scrape_and_save()