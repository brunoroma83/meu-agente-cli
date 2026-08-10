# 💰 Módulo Financeiro (/finance) - Manual do Usuário

O módulo financeiro do Meu Agente CLI permite o controle detalhado de receitas, despesas, faturamento de cartões de crédito e importações automatizadas via arquivos CSV. Ele suporta persistência no banco de dados PostgreSQL e pode ser acessado tanto pelo terminal local (WSL) quanto remotamente (bot do Telegram).

---

## 📊 1. Consultas Gerais e Filtros de Data

Os comandos de consulta exibem um sumário consolidado de Receitas, Despesas e o Saldo líquido calculado.

* `/finance` ou `/finance mes=MM-YYYY`
  Exibe as transações com vencimento no mês atual (ou no mês e ano especificados).
  *Exemplo:* `/finance mes=09-2026`
* `/finance next`
  Filtro rápido que exibe as transações projetadas para o próximo mês.
* `/finance all`
  Exibe todo o histórico de transações ativas cadastradas no banco de dados.

---

## 💳 2. Lançamento e Controle de Cartões de Crédito

O comando `/finance card` permite o gerenciamento completo de cartões, calculando faturas futuras com base no dia de fechamento e resolvendo arredondamentos de compras parceladas.

### A. Cadastrar Cartão de Crédito
Configura o dia de fechamento da fatura e o dia do vencimento.
* **Formato:** `/finance card add <nome_do_cartao> <dia_fechamento> <dia_vencimento>`
* **Exemplo:** `/finance card add Nubank 5 15`
  *(Indica que compras feitas a partir do dia 5 entram apenas na fatura do mês seguinte, vencendo no dia 15).*

### B. Listar Cartões
Exibe a lista de todos os cartões cadastrados e suas regras de data.
* **Formato:** `/finance card list`

### C. Lançar Compra (À Vista ou Parcelada)
Calcula automaticamente a divisão das parcelas, projeta as datas de vencimento futuras no banco de dados e ajusta o centavo de dízimas de arredondamento na primeira parcela.
* **Formato:** `/finance card buy <nome_cartao> <categoria> <valor_total> <quantidade_parcelas> <descricao> [data_da_compra]`
* **Exemplo à vista (1 parcela):**
  `/finance card buy Nubank Alimentação 150.00 1 "Supermercado"`
* **Exemplo parcelado (3 parcelas):**
  `/finance card buy Nubank Lazer 100.00 3 "Show de Rock"`
  *(Lançará 3 parcelas nas faturas subsequentes. A primeira parcela terá o valor de R$ 33.34 e as outras duas R$ 33.33).*

---

## 🔍 3. Busca, Exclusão e Restauração de Dados

* `/finance q=<termo>`
  Realiza uma pesquisa de texto livre na descrição ou categoria das transações.
  *Exemplo:* `/finance q=Comgas`
* `/finance delete <ID>`
  Exclui logicamente um lançamento do banco de dados (o registro é marcado como inativo).
* `/finance deleted`
  Lista todas as transações excluídas logicamente.
* `/finance restore <ID>`
  Restaura uma transação excluída logicamente, reativando-a no seu balanço financeiro.

---

## 📥 4. Importação e Exportação de Arquivos CSV

O sistema permite a ingestão em massa de dados financeiros a partir de planilhas.

* `/finance import`
  Importa transações em lote a partir do arquivo CSV localizado em `uploads/finance.csv`.
* **Formato esperado do CSV:**
  O arquivo CSV deve possuir as seguintes colunas: `tipo`, `categoria`, `valor`, `descricao`, `data` (no formato `DD/MM/YYYY`) e `vencimento` (no formato `DD/MM/YYYY`, opcional).
  *Exemplo:*
  ```csv
  tipo,categoria,valor,descricao,data,vencimento
  despesa,Alimentação,45.50,Almoço Executivo,10/08/2026,10/08/2026
  receita,Freelance,1200.00,Desenvolvimento Web,05/08/2026,05/08/2026
  ```

---

*Nota: Pelo bot do Telegram, você pode simplesmente anexar um arquivo `.csv` no chat, e o bot iniciará a importação de finanças automaticamente.*
