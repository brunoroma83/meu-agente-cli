# Projeto Meu Agente CLI

## Objetivo
O objetivo do agente é realizar tarefas simples e tirar dúvidas sobre programação, comandos CLI do Linux, capturar notícias gerais, mercado financeiro, ações, variação de câmbio, inflação, previsão do tempo. Deve também armazenar informações, e pretendo incluir skills e tools para ele me ajudar. Pretendo também fazer com que ele me ajude com finanças.

## Armazenamento de informações
Gostaria que as informações fossem armazenadas em um servidor SQL aberto hospedado no próprio WSL, iniciado pelo agente quando ele for iniciado

## LLM
Vamos usar o LM Studio, como o IP da rede pode mudar, é importante que na inicialização, o código pegar o IP da máquina e testar a conexão. Inicialmente vamos usar o modelo google/gemma-4-31b-qat, mas precisa ter uma forma de consultar os modelos disponíveis e permitir mudar e salvar a alteração para as próximas inicializações

## O Agente
Após inicializar o agente, ele deve sempre ficar aguardando um input do usuário, e então trabalhar na resposta, usando skills e tools disponíveis. Ao final da iteração e resposta, o agente deve apresentar o input e esperar a próxima instrução.

## Subagentes
Permitir que o agente crie subagentes se necessário, para tarefas cronojob por exemplo.