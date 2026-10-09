import os
import time
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

pergunta = "Olá, me apresente em 2 frases como um assistente financeiro chamado SkayX."

# Tenta até 3 vezes se der erro 503
for tentativa in range(3):
    try:
        resposta = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=pergunta
        )
        print(resposta.text)
        break
    except Exception as e:
        if "503" in str(e) and tentativa < 2:
            print(f"⏳ Servidor ocupado, tentando de novo em 5s... (tentativa {tentativa+1}/3)")
            time.sleep(5)
        else:
            print(f"❌ Erro: {e}")
            break   