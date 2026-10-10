import os
import json
import time
import random
import hashlib
import sqlite3
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
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
# BANCO DE DADOS (usuários)
# ═══════════════════════════════════════════
def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nome TEXT NOT NULL,
            premium INTEGER DEFAULT 0,
            premium_expira TEXT,
            criado_em TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def check_password(password, hashed):
    return hash_password(password) == hashed

# ═══════════════════════════════════════════
# FLASK-LOGIN
# ═══════════════════════════════════════════
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "skayx-secret-2026")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

class User(UserMixin):
    def __init__(self, id, email, nome, premium, premium_expira):
        self.id = id
        self.email = email
        self.nome = nome
        self.premium = premium
        self.premium_expira = premium_expira

    @property
    def is_premium(self):
        if not self.premium or not self.premium_expira:
            return False
        expira = datetime.strptime(self.premium_expira, "%Y-%m-%d %H:%M:%S")
        return datetime.now() < expira

@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
    conn.close()
    if row:
        return User(row["id"], row["email"], row["nome"], row["premium"], row["premium_expira"])
    return None

# ═══════════════════════════════════════════
# ROTAS DE AUTENTICAÇÃO
# ═══════════════════════════════════════════
@app.route("/")
def index():
    if current_user.is_authenticated:
        return render_template("index.html")
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()

        if row and check_password(password, row["password_hash"]):
            user = User(row["id"], row["email"], row["nome"], row["premium"], row["premium_expira"])
            login_user(user)
            return redirect(url_for("index"))
        flash("E-mail ou senha incorretos.")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not nome or not email or len(password) < 6:
            flash("Preencha todos os campos. Senha mínima: 6 caracteres.")
            return render_template("register.html")

        conn = get_db()
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.close()
            flash("E-mail já cadastrado.")
            return render_template("register.html")

        conn.execute(
            "INSERT INTO users (email, password_hash, nome) VALUES (?,?,?)",
            (email, hash_password(password), nome)
        )
        conn.commit()
        conn.close()
        flash("Conta criada! Faça login.")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

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
# CONTADOR PERSISTENTE
# ═══════════════════════════════════════════
def verificar_limite(user_id, premium=False):
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contadores (
            user_id INTEGER PRIMARY KEY,
            data TEXT NOT NULL,
            count INTEGER DEFAULT 0
        )
    """)
    hoje = date.today().strftime("%Y-%m-%d")

    row = conn.execute(
        "SELECT data, count FROM contadores WHERE user_id = ?", (user_id,)
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO contadores (user_id, data, count) VALUES (?,?,1)",
            (user_id, hoje)
        )
        conn.commit()
        conn.close()
        return True, LIMITE_FREE_DIA - 1

    if row["data"] != hoje:
        conn.execute(
            "UPDATE contadores SET data=?, count=1 WHERE user_id=?",
            (hoje, user_id)
        )
        conn.commit()
        conn.close()
        return True, LIMITE_FREE_DIA - 1

    if premium:
        conn.close()
        return True, 0

    if row["count"] >= LIMITE_FREE_DIA:
        conn.close()
        return False, 0

    conn.execute(
        "UPDATE contadores SET count = count + 1 WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()
    restantes = LIMITE_FREE_DIA - row["count"] - 1
    conn.close()
    return True, restantes

# ═══════════════════════════════════════════
# ROTAS DA API
# ═══════════════════════════════════════════
sessoes = {}

@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    dados = request.json
    mensagem = dados.get("message", "")
    user_id = current_user.id

    premium = current_user.is_premium
    permitido, restantes = verificar_limite(user_id, premium)

    if not permitido:
        return jsonify({
            "response": None,
            "limite_esgotado": True,
            "msg": f"🔒 Limite gratuito esgotado ({LIMITE_FREE_DIA} msg/dia). Assine o SkayX Pro para uso ilimitado."
        })

    if user_id not in sessoes:
        sessoes[user_id] = []
    sessoes[user_id].append({"role": "user", "content": mensagem})
    if len(sessoes[user_id]) > 10:
        sessoes[user_id] = sessoes[user_id][-10:]

    contexto_extra = ""
    if any(w in mensagem.lower() for w in ["gastei", "gasto", "orçamento", "mês", "renda"]):
        resumo = resumo_gastos()
        orc = verificar_orcamento()
        contexto_extra = f"\nDADOS REAIS:\nResumo: {json.dumps(resumo, ensure_ascii=False)}\nOrçamento: {orc}\n"

    conteudo = []
    for msg in sessoes[user_id]:
        role = "user" if msg["role"] == "user" else "model"
        conteudo.append({"role": role, "parts": [{"text": msg["content"]}]})

    resposta_texto = chamar_gemini(conteudo, SYSTEM_PROMPT + contexto_extra)
    sessoes[user_id].append({"role": "model", "content": resposta_texto})

    return jsonify({
        "response": resposta_texto,
        "restantes": restantes,
        "premium": premium
    })

@app.route("/api/registrar", methods=["POST"])
@login_required
def registrar():
    dados = request.json
    desc = dados.get("descricao", "")
    valor = float(dados.get("valor", 0))
    cat = dados.get("categoria", "Outros")
    tipo = dados.get("tipo", "saida")
    registrar_gasto(desc, valor, cat, tipo)
    return jsonify({"ok": True, "msg": f"✅ {desc} (R${valor:.2f}) registrado."})

@app.route("/api/resumo")
@login_required
def resumo():
    return jsonify({
        "gastos": resumo_gastos(),
        "orcamento": verificar_orcamento()
    })

@app.route("/api/ativar-premium", methods=["POST"])
@login_required
def ativar_premium():
    dados = request.json
    key = dados.get("key", "").strip()

    if not key or len(key) != 32:
        return jsonify({"ok": False, "msg": "❌ Chave inválida."})

    conn = get_db()
    agora = datetime.now()
    expira = agora + timedelta(days=30)
    conn.execute(
        "UPDATE users SET premium=1, premium_expira=? WHERE id=?",
        (expira.strftime("%Y-%m-%d %H:%M:%S"), current_user.id)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "msg": "✅ SkayX Pro ativado por 30 dias!"})

@app.route("/gerar-chave")
def gerar_chave_page():
    chave = hashlib.sha256(
        f"{datetime.now().isoformat()}_{random.random()}_{os.urandom(8).hex()}".encode()
    ).hexdigest()
    return f"""
    <html><body style="background:#0a0a0f;color:#e8e8e8;font-family:'Inter',sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;">
        <div style="background:#12121a;border:1px solid #2a2a3a;border-radius:16px;padding:40px;text-align:center;max-width:400px;">
            <h1 style="font-size:20px;margin-bottom:8px;">🔑 Chave SkayX Pro</h1>
            <p style="color:#9a9a9a;font-size:14px;margin-bottom:20px;">Copie e envie ao cliente:</p>
            <pre style="font-size:14px;background:#0a0a0f;color:#4ade80;padding:16px 20px;border-radius:10px;cursor:pointer;border:1px solid #2a2a3a;word-break:break-all;" onclick="navigator.clipboard.writeText('{chave}');this.textContent='✅ Copiado!'">{chave}</pre>
            <p style="color:#666;font-size:12px;margin-top:16px;">Validade: 30 dias</p>
        </div>
    </body></html>
    """

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)   