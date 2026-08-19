import yfinance as yf

def run(symbol, period="6mo"):
    df = yf.download(symbol, period=period)
    
    if df.empty:
        return f"Não encontrei dados para o símbolo {symbol}"
        
    print(f"Dados: {df}")
    
    #trend = "Subida 🔼" if ma50.iloc[-1] > ma200.iloc[-1] else "Queda 📉"
    
    return f"{symbol} | MA(50): {ma50.iloc[-1]:.2f} | MA(200): {ma200.iloc[-1]:.2f}"

if __name__ == "__main__":
    print(run("PETR4.SA"))