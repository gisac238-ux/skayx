import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "skayx.db")

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS gastos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            categoria TEXT NOT NULL,
            descricao TEXT,
            valor REAL NOT NULL,
            tipo TEXT CHECK(tipo IN ('entrada','saida')),
            criado_em TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS orcamento (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            categoria TEXT UNIQUE NOT NULL,
            limite_mensal REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS negocios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            faturamento_mensal REAL,
            custos_mensais REAL,
            criado_em TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS plano_acao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT NOT NULL,
            descricao TEXT,
            prazo TEXT,
            concluido INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()

def registrar_gasto(descricao, valor, categoria, tipo="saida"):
    conn = get_db()
    conn.execute(
        "INSERT INTO gastos (data, categoria, descricao, valor, tipo) VALUES (?,?,?,?,?)",
        (datetime.now().strftime("%Y-%m-%d"), categoria, descricao, valor, tipo)
    )
    conn.commit()
    conn.close()

def resumo_gastos():
    conn = get_db()
    inicio = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT categoria, tipo, SUM(valor) as total FROM gastos WHERE data >= ? GROUP BY categoria, tipo",
        (inicio,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def verificar_orcamento():
    conn = get_db()
    orcamentos = conn.execute("SELECT * FROM orcamento").fetchall()
    gastos = conn.execute(
        "SELECT categoria, SUM(valor) as total FROM gastos WHERE tipo='saida' AND data >= date('now','start of month') GROUP BY categoria"
    ).fetchall()
    conn.close()
    alertas = []
    for orc in orcamentos:
        gasto_real = next((g["total"] for g in gastos if g["categoria"] == orc["categoria"]), 0)
        if gasto_real > orc["limite_mensal"]:
            alertas.append(f"🚨 {orc['categoria']}: R${gasto_real:.2f} (limite R${orc['limite_mensal']:.2f})")
        elif gasto_real > orc["limite_mensal"] * 0.8:
            alertas.append(f"⚠️ {orc['categoria']}: 80% do limite (R${gasto_real:.2f}/R${orc['limite_mensal']:.2f})")
    return alertas if alertas else ["✅ Todos os gastos dentro do orçamento."]

def definir_orcamento(categoria, limite):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO orcamento (categoria, limite_mensal) VALUES (?,?)",
        (categoria, limite)
    )
    conn.commit()
    conn.close()

init_db()   