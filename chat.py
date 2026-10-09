import os
import json
import time
from dotenv import load_dotenv
from google import genai
from database import (
    registrar_gasto, resumo_gastos,
    verificar_orcamento, definir_orcamento
)

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

MODEL = "gemini-3.8-flash"   

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
- Se o usuário pedir para registrar gasto, use o formato:
  [REGISTRAR] descricao | valor | categoria | tipo(entrada/saida)
- Se o usuário pedir para definir orçamento, use:
  [ORCAMENTO] categoria | valor_mensal
- Se o usuário pedir análise de negócio, use:
  [NEGOCIO] faturamento | custos
- Se o usuário pedir plano de ação, use:
  [PLANO] objetivo
"""

def triage(mensagem):
    """Roteia a mensagem para o módulo certo."""
    prompt = f"""Classifique em UMA categoria (apenas a palavra):
- gastos
- negocios
- seguranca
- organizacao
- geral

Mensagem: "{mensagem}"
Responda APENAS a categoria."""
    try:
        r = client.models.generate_content(model=MODEL, contents=prompt)
        return r.text.strip().lower()
    except:
        return "geral"

def gerar_resposta(mensagem, historico):
    categoria = triage(mensagem)

    # Injetar dados reais quando for módulo de gastos
    contexto_extra = ""
    if categoria == "gastos":
        resumo = resumo_gastos()
        orc = verificar_orcamento()
        contexto_extra = f"\nDADOS REAIS DO USUÁRIO:\nResumo 30 dias: {json.dumps(resumo, ensure_ascii=False)}\nOrçamento: {orc}\n"

    # Montar histórico no formato da API
    conteudo = []
    for msg in historico[-10:]:
        conteudo.append({"role": msg["role"], "parts": [{"text": msg["content"]}]})

    prompt_final = f"{contexto_extra}\nPergunta atual: {mensagem}"

    try:
        resposta = client.models.generate_content(
            model=MODEL,
            contents=conteudo + [{"role": "user", "parts": [{"text": prompt_final}]}],
            config={
                "system_instruction": SYSTEM_PROMPT,
                "temperature": 0.7,
                "max_output_tokens": 1024
            }
        )
        return resposta.text
    except Exception as e:
        if "503" in str(e):
            time.sleep(5)
            return gerar_resposta(mensagem, historico)  # retry
        return f"❌ Erro: {e}"

def processar_acoes(texto, historico):
    """Executa ações especiais [REGISTRAR], [ORCAMENTO], [NEGOCIO], [PLANO]."""
    acoes = []

    if "[REGISTRAR]" in texto:
        try:
            parte = texto.split("[REGISTRAR]")[1].split("\n")[0].strip()
            campos = [c.strip() for c in parte.split("|")]
            desc, valor, cat, tipo = campos[0], float(campos[1]), campos[2], campos[3]
            registrar_gasto(desc, valor, cat, tipo)
            acoes.append(f"✅ Registrado: {desc} (R${valor:.2f}) em {cat}")
        except:
            pass

    if "[ORCAMENTO]" in texto:
        try:
            parte = texto.split("[ORCAMENTO]")[1].split("\n")[0].strip()
            campos = [c.strip() for c in parte.split("|")]
            cat, valor = campos[0], float(campos[1])
            definir_orcamento(cat, valor)
            acoes.append(f"✅ Orçamento de {cat} definido: R${valor:.2f}/mês")
        except:
            pass

    return acoes

# ═══════════════════════════════════════════
# LOOP PRINCIPAL
# ═══════════════════════════════════════════

print("=" * 52)
print("  🏦 SKAYX — Sua IA Financeira")
print("  Gastos | Negócios | Segurança | Organização")
print("  Digite 'sair' para encerrar.")
print("=" * 52)

historico = []

while True:
    usuario = input("\n🧑 Você: ").strip()
    if usuario.lower() in ("sair", "sair.", "exit", "q"):
        print("\n👋 Até logo! SkayX encerrado.")
        break
    if not usuario:
        continue

    historico.append({"role": "user", "content": usuario})

    print("\n🤖 SkayX: ", end="", flush=True)
    resposta = gerar_resposta(usuario, historico)

    # Processar ações especiais
    acoes = processar_acoes(resposta, historico)
    for acao in acoes:
        print(f"\n  {acao}")

    # Limpar tags antes de mostrar
    texto_limpo = resposta.replace("[REGISTRAR]", "").replace("[ORCAMENTO]", "").replace("[NEGOCIO]", "").replace("[PLANO]", "").strip()
    print(texto_limpo)

    historico.append({"role": "model", "content": resposta})   