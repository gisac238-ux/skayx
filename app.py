import os
import json
import time
import random
import hashlib
import sqlite3
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from google import genai
from database import (
    registrar_gasto, resumo_gastos,
    verificar_orcamento, definir_orcamento
)

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# ═══════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════
MODELS = ["gemini-3.5-flash-lite", "gemini-2.5-flash"]
LIMITE_FREE_DIA = 20
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "skayx.db")

# ═══════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════
SYSTEM_PROMPT = """Você é "SkayX", um assistente de finanças pessoais e empresariais.
Seus módulos:
1. GASTOS: registrar, consultar e analisar gastos/rendas.
2. NEGÓCIOS: dicas de controle, fluxo de caixa, precificação, margem.
3. SEGURANÇA BANCÁRIA: alertar sobre fraudes, phishing, golpes.
4. ORGANIZAÇÃO: planos de ação, checklists, resumos.

REGRAS:
- Responda SEMPRE em português do Brasil.
- Seja direto, prático e com números quando possível.
- NUNCA peça senha, PIN, CVV ou código de verificação.
- Se detectar possível golpe, alerte com urgência.
"""

# ═══════════════════════════════════════════
# GEMINI COM FALLBACK
# ═══════════════════════════════════════════
def chamar_gemini(contents, system_instruction):
    for model in MODELS:
        for tentativa in range(2):
            try:
                resposta = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config={
                        "system_instruction": system_instruction,
                        "temperature": 0.7,
                        "max_output_tokens": 1024
                    }
                )
                return resposta.text
            except Exception as e:
                if "503" in str(e) or "429" in str(e):
                    if tentativa < 1:
                        time.sleep(2 + random.uniform(0, 1))
                        continue
                    break
                else:
                    return f"❌ Erro: {str(e)}"
    return "⏳ Todos os modelos estão ocupados. Tente novamente em 1 minuto."

# ═══════════════════════════════════════════
# CONTADOR PERSISTENTE (SQLite)
# ═══════════════════════════════════════════
def get_counter_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contadores (
            session_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            count INTEGER DEFAULT 0,
            premium INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn

def verificar_limite(session_id, premium=False):
    conn = get_counter_db()
    hoje = date.today().strftime("%Y-%m-%d")

    row = conn.execute(
        "SELECT data, count, premium FROM contadores WHERE session_id = ?",
        (session_id,)
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO contadores (session_id, data, count, premium) VALUES (?,?,?,?)",
            (session_id, hoje, 1, 1 if premium else 0)
        )
        conn.commit()
        conn.close()
        return True, LIMITE_FREE_DIA - 1, premium

    # Resetar se mudou o dia
    if row[0] != hoje:
        conn.execute(
            "UPDATE contadores SET data=?, count=1, premium=? WHERE session_id=?",
            (hoje, 1 if premium else 0, session_id)
        )
        conn.commit()
        conn.close()
        return True, LIMITE_FREE_DIA - 1, premium

    # Premium = ilimitado
    if row[2] == 1:
        conn.close()
        return True, 0, True

    # Free = checar limite
    if row[1] >= LIMITE_FREE_DIA:
        conn.close()
        return False, 0, False

    conn.execute(
        "UPDATE contadores SET count = count + 1 WHERE session_id = ?",
        (session_id,)
    )
    conn.commit()
    restantes = LIMITE_FREE_DIA - row[1] - 1
    conn.close()
    return True, restantes, False

# ═══════════════════════════════════════════
# CHAVE PREMIUM COM VALIDADE (30 dias)
# ═══════════════════════════════════════════
def get_premium_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS premium_keys (
            chave TEXT PRIMARY KEY,
            ativado_em TEXT NOT NULL,
            expira_em TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn

def validar_premium_key(key):
    if not key or len(key) != 32:
        return False
    conn = get_premium_db()
    row = conn.execute(
        "SELECT expira_em FROM premium_keys WHERE chave = ?", (key,)
    ).fetchone()
    conn.close()

    if row is None:
        return False

    expira = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
    return datetime.now() < expira

def ativar_chave(key):
    conn = get_premium_db()
    agora = datetime.now()
    expira = agora + timedelta(days=30)
    conn.execute(
        "INSERT OR REPLACE INTO premium_keys (chave, ativado_em, expira_em) VALUES (?,?,?)",
        (key, agora.strftime("%Y-%m-%d %H:%M:%S"), expira.strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()

# ═══════════════════════════════════════════
# APP FLASK
# ═══════════════════════════════════════════
app = Flask(__name__)
sessoes = {}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/chat", methods=["POST"])
def chat():
    dados = request.json
    mensagem = dados.get("message", "")
    session_id = dados.get("session_id", "default")
    premium_key = dados.get("premium_key", "")

    # Verificar se é premium
    premium = validar_premium_key(premium_key) if premium_key else False

    # Verificar limite
    permitido, restantes, is_premium = verificar_limite(session_id, premium)

    if not permitido:
        return jsonify({
            "response": None,
            "limite_esgotado": True,
            "msg": f"🔒 Você atingiu o limite gratuito de {LIMITE_FREE_DIA} mensagens/dia. Assine o SkayX Pro para uso ilimitado."
        })

    # Histórico
    if session_id not in sessoes:
        sessoes[session_id] = []
    sessoes[session_id].append({"role": "user", "content": mensagem})
    if len(sessoes[session_id]) > 10:
        sessoes[session_id] = sessoes[session_id][-10:]

    # Contexto de gastos
    contexto_extra = ""
    if any(w in mensagem.lower() for w in ["gastei", "gasto", "orçamento", "mês", "renda"]):
        resumo = resumo_gastos()
        orc = verificar_orcamento()
        contexto_extra = f"\nDADOS REAIS:\nResumo: {json.dumps(resumo, ensure_ascii=False)}\nOrçamento: {orc}\n"

    # Montar conteúdo
    conteudo = []
    for msg in sessoes[session_id]:
        role = "user" if msg["role"] == "user" else "model"
        conteudo.append({"role": role, "parts": [{"text": msg["content"]}]})

    resposta_texto = chamar_gemini(conteudo, SYSTEM_PROMPT + contexto_extra)
    sessoes[session_id].append({"role": "model", "content": resposta_texto})

    return jsonify({
        "response": resposta_texto,
        "restantes": restantes,
        "premium": is_premium
    })

@app.route("/api/registrar", methods=["POST"])
def registrar():
    dados = request.json
    desc = dados.get("descricao", "")
    valor = float(dados.get("valor", 0))
    cat = dados.get("categoria", "Outros")
    tipo = dados.get("tipo", "saida")
    registrar_gasto(desc, valor, cat, tipo)
    return jsonify({"ok": True, "msg": f"✅ {desc} (R${valor:.2f}) registrado."})

@app.route("/api/resumo")
def resumo():
    return jsonify({
        "gastos": resumo_gastos(),
        "orcamento": verificar_orcamento()
    })

@app.route("/api/ativar-premium", methods=["POST"])
def ativar_premium():
    dados = request.json
    key = dados.get("key", "").strip()
    session_id = dados.get("session_id", "default")

    if not key or len(key) != 32:
        return jsonify({"ok": False, "msg": "❌ Chave inválida."})

    # Ativar a chave (30 dias)
    ativar_chave(key)

    # Marcar sessão como premium
    conn = get_counter_db()
    hoje = date.today().strftime("%Y-%m-%d")
    conn.execute(
        "INSERT OR REPLACE INTO contadores (session_id, data, count, premium) VALUES (?,?,?,1)",
        (session_id, hoje, 0)
    )
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "msg": "✅ SkayX Pro ativado por 30 dias!"})

@app.route("/api/gerar-chave", methods=["POST"])
def gerar_chave():
    """Gerar chave premium (use após confirmar pagamento no Asaas)."""
    chave = hashlib.sha256(
        f"{datetime.now().isoformat()}_{random.random()}_{os.urandom(8).hex()}".encode()
    ).hexdigest()
    ativar_chave(chave)
    return jsonify({"chave": chave, "validade": "30 dias"})
@app.route("/gerar-chave")
def gerar_chave_page():
    chave = hashlib.sha256(
        f"{datetime.now().isoformat()}_{random.random()}_{os.urandom(8).hex()}".encode()
    ).hexdigest()
    ativar_chave(chave)
    return f"""
    <html><body style="background:#0d0d0d;color:#e8e8e8;font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;">
        <h1>🔑 Chave SkayX Pro</h1>
        <p>Copie e envie ao cliente:</p>
        <pre style="font-size:18px;background:#1a1a1a;color:#4ade80;padding:20px 30px;border-radius:12px;cursor:pointer;border:1px solid #333;" onclick="navigator.clipboard.writeText('{chave}');this.style.color='#f59e0b';this.textContent='✅ Copiado!'">{chave}</pre>
        <p style="color:#9a9a9a;font-size:13px;margin-top:12px;">Clique na chave para copiar. Validade: 30 dias.</p>
    </body></html>
    """   
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)   