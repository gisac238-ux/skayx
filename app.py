import os
import json
import time
import random
import hashlib
from datetime import datetime, date
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
# MODELO COM FALLBACK (resolve o 503)
# ═══════════════════════════════════════════
MODELS = ["gemini-3.5-flash-lite", "gemini-2.5-flash"]

def chamar_gemini(contents, system_instruction):
    """Tenta cada modelo em sequência até funcionar."""
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
                    break  # tenta o próximo modelo
                else:
                    return f"❌ Erro: {str(e)}"
    return "⏳ Todos os modelos estão ocupados. Tente novamente em 1 minuto."

# ═══════════════════════════════════════════
# SISTEMA DE PLANOS (Free / Premium)
# ═══════════════════════════════════════════
LIMITE_FREE_DIA = 20  # mensagens grátis por dia
PREMIUM_KEYS = [
    # Adicione as chaves dos usuários premium aqui
    # ou gere automaticamente
]

# Contador por sessão (em memória)
contadores = {}  # {session_id: {"data": "2026-10-09", "count": 5, "premium": False}}

def verificar_limite(session_id, premium=False):
    hoje = date.today().strftime("%Y-%m-%d")
    
    if session_id not in contadores:
        contadores[session_id] = {"data": hoje, "count": 0, "premium": premium}
    
    # Resetar se mudou o dia
    if contadores[session_id]["data"] != hoje:
        contadores[session_id] = {"data": hoje, "count": 0, "premium": premium}
    
    if premium:
        return True, 0, True
    
    contadores[session_id]["count"] += 1
    restantes = LIMITE_FREE_DIA - contadores[session_id]["count"]
    
    if contadores[session_id]["count"] > LIMITE_FREE_DIA:
        return False, 0, False
    
    return True, restantes, False

def validar_premium_key(key):
    """Verifica se a chave premium é válida."""
    if key in PREMIUM_KEYS:
        return True
    # Chave premium = hash de 32 chars (gerada no checkout)
    if len(key) == 32 and all(c in "0123456789abcdef" for c in key):
        return True
    return False

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

app = Flask(__name__)

sessoes = {}

# ═══════════════════════════════════════════
# ROTAS
# ═══════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/chat", methods=["POST"])
def chat():
    dados = request.json
    mensagem = dados.get("message", "")
    session_id = dados.get("session_id", "default")
    premium_key = dados.get("premium_key", "")

    # Verificar premium
    premium = validar_premium_key(premium_key) if premium_key else False
    if session_id in contadores:
        contadores[session_id]["premium"] = premium

    # Verificar limite
    permitido, restantes, is_premium = verificar_limite(session_id, premium)
    
    if not permitido:
        return jsonify({
            "response": None,
            "limite_esgotado": True,
            "msg": f"🔒 Você atingiu o limite gratuito de {LIMITE_FREE_DIA} mensagens/dia. Assine o SkayX Pro para uso ilimitado."
        })

    # Processar mensagem
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
    key = dados.get("key", "")
    session_id = dados.get("session_id", "default")
    
    if validar_premium_key(key):
        if session_id not in contadores:
            contadores[session_id] = {"data": date.today().strftime("%Y-%m-%d"), "count": 0, "premium": True}
        contadores[session_id]["premium"] = True
        return jsonify({"ok": True, "msg": "✅ SkayX Pro ativado!"})
    return jsonify({"ok": False, "msg": "❌ Chave inválida."})

@app.route("/api/gerar-chave-premium", methods=["POST"])
def gerar_chave():
    """Gerar chave premium (use após receber pagamento)."""
    # Em produção, isto seria disparado pelo webhook do Paddle/PayPal
    chave = hashlib.sha256(f"{datetime.now().isoformat()}_{random.random()}".encode()).hexdigest()
    PREMIUM_KEYS.append(chave)
    return jsonify({"chave": chave})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)   