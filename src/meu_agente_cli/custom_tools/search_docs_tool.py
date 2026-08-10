import os
import re
from pathlib import Path

def run(query: str = "", **kwargs) -> str:
    """
    Ferramenta para pesquisar manuais e documentações na pasta docs/
    para resolver dúvidas sobre o funcionamento do agente.
    """
    # Localiza o diretório docs de forma robusta
    current_file = Path(__file__).resolve()
    # Caminho subindo os diretórios: custom_tools -> meu_agente_cli -> src -> raiz
    docs_dir = current_file.parents[3] / "docs"
    
    # Fallback caso não seja encontrado na estrutura padrão
    if not docs_dir.exists():
        docs_dir = Path(os.getcwd()) / "docs"
        
    if not docs_dir.exists() or not docs_dir.is_dir():
        return "[INFO] Pasta de documentação 'docs/' não encontrada na raiz do projeto."
        
    md_files = list(docs_dir.glob("*.md"))
    
    if not md_files:
        return "[INFO] Nenhum documento de ajuda (.md) encontrado na pasta 'docs/'."
        
    # Cenário A: Sem termo de pesquisa -> Lista arquivos disponíveis
    if not query.strip():
        linhas = ["📚 *Documentos de Ajuda Disponíveis:*"]
        for f in md_files:
            linhas.append(f"• `{f.name}` - Use a ferramenta com query='{f.stem}' para ler o manual completo.")
        return "\n".join(linhas)
        
    # Cenário B: Termo de busca fornecido -> Pesquisa nos arquivos
    query_clean = query.strip().lower()
    matches = []
    
    for f in md_files:
        filename = f.name.lower()
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as file_obj:
                content = file_obj.read()
                
            # Verifica se o termo está no nome do arquivo ou no conteúdo
            if query_clean in filename or query_clean in content.lower():
                matches.append((f.name, content))
        except Exception as e:
            print(f"[Warning] Falha ao ler documento {f.name}: {e}")
            
    if not matches:
        linhas = [f"🔍 Nenhuma instrução encontrada para o termo '{query}'."]
        linhas.append("\n📚 *Documentos de Ajuda Disponíveis:*")
        for f in md_files:
            linhas.append(f"• `{f.name}`")
        return "\n".join(linhas)
        
    # Retorna o conteúdo dos arquivos correspondentes
    resultado = []
    for fname, content in matches:
        resultado.append(f"📖 *Documento encontrado: {fname}*\n\n{content}\n")
        
    return "\n\n".join(resultado)
