import psycopg

def main():
    conn = psycopg.connect("dbname=financeiro_db user=financeiro_user password=financeiro_dev_password host=192.168.1.15 port=5432")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT type, category, amount, description, date, due_date, active, nature, card_name, is_paid, payment_date, user_name
            FROM financial_records
            WHERE card_id = 5
            ORDER BY id
        """)
        rows = cur.fetchall()

    lines = []
    lines.append("-- ==============================================================================")
    lines.append("-- Migracao dos Lancamentos do Cartao Itau Fabiana para o Servidor de Producao")
    lines.append(f"-- Total de lancamentos de cartao: {len(rows)}")
    lines.append("-- ==============================================================================")
    lines.append("")
    lines.append("BEGIN;")
    lines.append("")
    lines.append("-- 1. Cadastra ou atualiza o cartao na tabela credit_cards")
    lines.append("INSERT INTO credit_cards (name, bank, due_day, closing_day, user_name, active)")
    lines.append("VALUES ('Itaú Fabiana', 'Itaú', 13, 2, 'fabiana', TRUE)")
    lines.append("ON CONFLICT (name) DO UPDATE")
    lines.append("SET bank = EXCLUDED.bank,")
    lines.append("    due_day = EXCLUDED.due_day,")
    lines.append("    closing_day = EXCLUDED.closing_day,")
    lines.append("    user_name = EXCLUDED.user_name,")
    lines.append("    active = EXCLUDED.active;")
    lines.append("")
    lines.append("-- 2. Insere os lancamentos vinculando dinamicamente ao card_id correspondente")
    lines.append("DO $$")
    lines.append("DECLARE")
    lines.append("    v_card_id INT;")
    lines.append("BEGIN")
    lines.append("    SELECT id INTO v_card_id FROM credit_cards WHERE name = 'Itaú Fabiana' LIMIT 1;")
    lines.append("    IF v_card_id IS NULL THEN")
    lines.append("        RAISE EXCEPTION 'Cartao Itau Fabiana nao encontrado na tabela credit_cards!';")
    lines.append("    END IF;")
    lines.append("")

    for r in rows:
        tipo, cat, amt, desc, dt, due_dt, act, nat, c_name, is_p, p_date, u_name = r
        desc_escaped = desc.replace("'", "''")
        cat_escaped = cat.replace("'", "''") if cat else "Outros"
        c_name_escaped = c_name.replace("'", "''") if c_name else "Itaú Fabiana"
        dt_str = dt.isoformat()
        due_val = f"'{due_dt.isoformat()}'" if due_dt else "NULL"
        act_str = "TRUE" if act else "FALSE"
        p_paid_str = "TRUE" if is_p else "FALSE"
        p_date_val = f"'{p_date.isoformat()}'" if p_date else "NULL"
        amt_str = str(amt)

        line = (
            f"    INSERT INTO financial_records (type, category, amount, description, date, due_date, active, nature, card_name, is_paid, payment_date, user_name, card_id) "
            f"SELECT '{tipo}', '{cat_escaped}', {amt_str}, '{desc_escaped}', '{dt_str}', {due_val}, {act_str}, '{nat}', '{c_name_escaped}', {p_paid_str}, {p_date_val}, '{u_name}', v_card_id "
            f"WHERE NOT EXISTS (SELECT 1 FROM financial_records WHERE description = '{desc_escaped}' AND due_date = {due_val} AND amount = {amt_str} AND user_name = '{u_name}');"
        )
        lines.append(line)

    lines.append("END $$;")
    lines.append("")
    lines.append("COMMIT;")
    lines.append("")

    sql_content = "\n".join(lines)
    with open("scripts/migrar_itau_fabiana.sql", "w", encoding="utf-8") as f:
        f.write(sql_content)

    print(f"Sucesso! Gerado scripts/migrar_itau_fabiana.sql com {len(rows)} registros.")
    conn.close()

if __name__ == "__main__":
    main()
