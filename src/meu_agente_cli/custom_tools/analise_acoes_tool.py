from tqdm import tqdm
from datetime import datetime, timedelta
import yfinance as yf
import json
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error


def calcular_pesos_temporais(n_amostras, half_life_semanas=16):
    """Calcula pesos com decaimento exponencial onde as amostras mais recentes

    recebem peso máximo (1.0).
    """
    # Índices de 0 (mais antigo) até n_amostras-1 (mais recente)
    distancia_do_presente = np.arange(n_amostras)[::-1]
    decaimento = np.log(2) / half_life_semanas
    pesos = np.exp(-decaimento * distancia_do_presente)
    return pesos


def treinar_e_projetar_ponderado(
    df, acao, horizontes=[1, 4, 12, 24], half_life_semanas=16
):
    """Treina os regressores por horizonte aplicando sample_weight exponencial."""
    # 1. Resample semanal
    df_semanal = (
        df['Close'].resample('W-FRI').last().dropna().to_frame('Close_Semanal')
    )

    if len(df_semanal) < 30:
        raise ValueError('Histórico semanal insuficiente para treinamento.')

    # 2. Engenharia de Features e Targets
    df_feat = df_semanal.copy()
    df_feat['Retorno_1S'] = df_feat['Close_Semanal'].pct_change(1)
    df_feat['Retorno_4S'] = df_feat['Close_Semanal'].pct_change(4)
    df_feat['Retorno_12S'] = df_feat['Close_Semanal'].pct_change(12)

    df_feat['Volatilidade_4S'] = df_feat['Retorno_1S'].rolling(4).std()
    df_feat['Volatilidade_12S'] = df_feat['Retorno_1S'].rolling(12).std()

    ema_rapida = df_feat['Close_Semanal'].ewm(span=4, adjust=False).mean()
    ema_lenta = df_feat['Close_Semanal'].ewm(span=9, adjust=False).mean()
    df_feat['Dist_EMA_Ratio'] = (ema_rapida - ema_lenta) / ema_lenta
    df_feat['Tendencia_Binaria'] = np.where(ema_rapida > ema_lenta, 1, 0)

    blocos = (
        df_feat['Tendencia_Binaria'] != df_feat['Tendencia_Binaria'].shift()
    ).cumsum()
    df_feat['Semanas_Na_Tendencia'] = df_feat.groupby(blocos).cumcount() + 1

    for h in horizontes:
        df_feat[f'Target_Retorno_{h}S'] = (
            df_feat['Close_Semanal'].shift(-h) - df_feat['Close_Semanal']
        ) / df_feat['Close_Semanal']

    feature_cols = [
        'Retorno_1S',
        'Retorno_4S',
        'Retorno_12S',
        'Volatilidade_4S',
        'Volatilidade_12S',
        'Dist_EMA_Ratio',
        'Tendencia_Binaria',
        'Semanas_Na_Tendencia',
    ]

    ultimo_preco = float(df_semanal['Close_Semanal'].iloc[-1])
    ultima_data = df_semanal.index[-1]
    ultima_linha_features = df_feat.iloc[[-1]][feature_cols]

    projecoes = []
    df_projecoes = pd.DataFrame()
    

    for h in horizontes:
        target_col = f'Target_Retorno_{h}S'
        dados_treino = df_feat.dropna(subset=feature_cols + [target_col])

        X = dados_treino[feature_cols]
        y = dados_treino[target_col]

        # Divisão temporal (80% treino, 20% validação)
        split_idx = int(len(X) * 0.8)
        X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_val = y.iloc[:split_idx], y.iloc[split_idx:]

        # GERAÇÃO DOS PESOS TEMPORAIS:
        # Pesos calculados para o conjunto de treino com base no tamanho de X_train
        weights_train = calcular_pesos_temporais(
            len(X_train), half_life_semanas=half_life_semanas
        )

        # Regressor com sample_weight
        model = HistGradientBoostingRegressor(
            max_iter=100, max_depth=3, random_state=42
        )
        model.fit(X_train, y_train, sample_weight=weights_train)

        # Avaliação no conjunto de validação
        if len(y_val) > 0:
            val_preds = model.predict(X_val)
            mae = mean_absolute_error(y_val, val_preds) * 100
        else:
            mae = 0.0

        # Inferência
        retorno_projetado = float(model.predict(ultima_linha_features)[0])
        preco_projetado = float(ultimo_preco * (1 + retorno_projetado))
        data_alvo = (ultima_data + pd.DateOffset(weeks=h)).strftime('%Y-%m-%d')

        dict_projecoes = {
                'acao': acao,
                'data_referencia': ultima_data.strftime('%Y-%m-%d'),
                'preco_atual': round(ultimo_preco, 2),
                'half_life_semanas_configurada': half_life_semanas,
                'horizonte_semanas': h,
                'data_alvo': data_alvo,
                'retorno_esperado_pct': float(
                    round(retorno_projetado * 100, 2)
                ),
                'preco_projetado': float(round(preco_projetado, 2)),
                'mae_validacao_pct': float(round(mae, 2)),
            }
        
        projecoes.append(dict_projecoes)
        df_projecoes = pd.concat([df_projecoes, pd.DataFrame([dict_projecoes])], ignore_index=True)

    return df_projecoes

def coletar_info_stocks(ticker: str) -> pd.DataFrame:
    stock = yf.Ticker(ticker)
    end_date = datetime.now()
    # B3 opera ~21 dias de trading/mês, adiciona buffer para alinhamento exato
    meses = 18
    start_days = int(meses * 30) 
    start_date = (end_date - timedelta(days=start_days)).strftime('%Y-%m-%d')
    #print(f"Conta: end date: {end_date} - dias: ({timedelta(days=start_days)})")
    #print(f"Start Date: {start_date}")


    df = stock.history(period='max', auto_adjust=True, start=start_date).copy()
    # Remove linhas com preço nulo e garante ordem cronológica
    #df = df[~df['Close'].isna()].sort_index().reset_index(drop=True)
    df = df[~df['Close'].isna()].sort_index()

    return df


acoes = json.load(open('src/meu_agente_cli/custom_tools/estudos_acoes/acoes.json'))['acoes'].split(',')

acoes_sa = [acao.strip().upper()+".SA" for acao in acoes]

df_total = pd.DataFrame()

print("Iniciando previsões")
pbar = tqdm(acoes_sa, desc="Prevendo ações", unit="ação")
data_hoje = datetime.now().strftime('%Y-%m-%d')
for acao in pbar:
    pbar.set_postfix_str(f"Gravando ação: {acao}")
    df = coletar_info_stocks(acao)
    df_resultado = treinar_e_projetar_ponderado(df, acao, horizontes=[1, 2, 3, 4, 5, 6, 7, 8])
    #df_resultado.to_csv(f"src/meu_agente_cli/custom_tools/estudos_acoes/{data_hoje}_{acao}.csv", index=False)

    df_total = pd.concat([df_total, df_resultado], ignore_index=True)

print("\nFim do processo.")

df_total.to_csv(f"src/meu_agente_cli/custom_tools/estudos_acoes/{data_hoje}_acoes.csv", index=True)

print(df_total)