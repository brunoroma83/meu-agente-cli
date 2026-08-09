import random
import json
import os
from datetime import datetime

# Lista expandida com 30 dicas variadas
dicas = [
    "Crie landing pages simples para pequenos negócios locais usando WordPress ou HTML/CSS.",
    "Ofereça serviços de otimização de perfis no LinkedIn para profissionais da sua área.",
    "Desenvolva scripts em Python para automatizar tarefas repetitivas de planilhas (Excel/CSV).",
    "Venda templates de currículos e cartas de apresentação personalizados no Canva.",
    "Ofereça consultoria básica de segurança digital para idosos (senhas, phishing, backups).",
    "Crie tutoriais em vídeo curtos (Reels/TikTok) ensinando atalhos de teclado ou softwares comuns.",
    "Revenda serviços de design gráfico simples (logos, banners) usando ferramentas de IA como Midjourney ou DALL-E.",
    "Ofereça manutenção preventiva de computadores e limpeza de vírus para vizinhos e conhecidos.",
    "Crie e venda e-books sobre temas técnicos que você domina (ex: 'Introdução à Automação com Python').",
    "Prestar serviços de transcrição de áudio para texto usando ferramentas de IA e revisando o resultado.",
    "Ofereça serviços de montagem e configuração de PCs gamers ou para escritório.",
    "Crie bots simples para Telegram ou Discord para comunidades locais ou grupos de estudo.",
    "Venda fotos de stock (paisagens, tecnologia, objetos) em plataformas como Shutterstock ou Adobe Stock.",
    "Ofereça aulas particulares de informática básica para adultos ou idosos.",
    "Desenvolva pequenos plugins ou extensões para navegadores que resolvam problemas específicos.",
    "Prestar serviços de tradução técnica (artigos, manuais) se tiver domínio de outro idioma.",
    "Crie infográficos educativos sobre saúde, finanças ou tecnologia para blogs e redes sociais.",
    "Ofereça serviços de recuperação de dados de HDs ou pendrives danificados (nível básico/intermediário).",
    "Venda cursos online gravados sobre habilidades específicas (ex: 'Como usar Excel para Finanças Pessoais').",
    "Prestar consultoria de acessibilidade digital para sites pequenos (WCAG básico).",
    "Crie e gerencie perfis de redes sociais para micro-influenciadores ou pequenos comércios.",
    "Ofereça serviços de teste de usabilidade (UX Testing) para startups ou apps em fase beta.",
    "Venda prints de arte gerada por IA em camisetas, canecas ou posters (Print on Demand).",
    "Prestar serviços de instalação e configuração de câmeras de segurança ou redes Wi-Fi domésticas.",
    "Crie resumos executivos de artigos longos ou notícias técnicas para profissionais ocupados.",
    "Ofereça serviços de formatação e organização de documentos acadêmicos ou técnicos (ABNT, etc.).",
    "Desenvolva calculadoras online simples para nichos específicos (ex: cálculo de material de construção).",
    "Venda pacotes de ícones ou ilustrações vetoriais criadas com ferramentas de design.",
    "Prestar serviços de monitoramento remoto de servidores ou sites para pequenas empresas.",
    "Crie roteiros de viagem personalizados com base em preferências específicas, usando IA para pesquisa."
]

# Caminho para o arquivo de histórico
HISTORY_FILE = os.path.join(os.path.dirname(__file__), 'tips_history.json')

def load_history():
    """Carrega o histórico de dicas já exibidas."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []

def save_history(history):
    """Salva o histórico de dicas."""
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f)
    except IOError:
        pass

def get_next_tip():
    """Retorna a próxima dica em sequência, sem repetir até esgotar a lista."""
    history = load_history()
    
    # Se o histórico estiver vazio ou tiver mostrado todas as dicas, reseta
    if len(history) >= len(dicas):
        history = []
        save_history(history)
    
    # Encontra o índice da próxima dica
    # O índice é o tamanho do histórico atual (0, 1, 2...)
    next_index = len(history)
    
    # Garante que não saia dos limites (caso haja inconsistência)
    if next_index >= len(dicas):
        next_index = 0
        history = []
    
    tip = dicas[next_index]
    
    # Adiciona ao histórico
    history.append(tip)
    save_history(history)
    
    return tip

def run(**kwargs):
    """Função principal para obter uma dica diária."""
    tip = get_next_tip()
    return f"💡 **Dica de Renda Extra:** {tip}"
