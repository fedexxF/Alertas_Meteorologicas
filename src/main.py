import os
import io
import re
import html
import hashlib
import urllib3
import requests
from datetime import datetime

# --- CONFIGURACIÓN GLOBAL Y CONSTANTES ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TOKEN_ALERTAS = os.environ.get('TELEGRAM_TOKEN_ALERTAS') or os.environ.get('TELEGRAM_TOKEN')
CHAT_ID_ALERTAS = os.environ.get('CHAT_ID_ALERTAS') or os.environ.get('CHAT_ID')

URL_API_SMN_VARELA = 'https://ws1.smn.gob.ar/v1/warning/alert/area/766'
ARCHIVO_MEMORIA = "data/memoria_bot.txt"

# Sesión base (sin el token todavía, se lo agregamos dinámicamente)
sesion = requests.Session()
sesion.verify = False
sesion.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36',
    'Origin': 'https://www.smn.gob.ar',
    'Referer': 'https://www.smn.gob.ar/'
})

def cargar_memoria():
    if os.path.exists(ARCHIVO_MEMORIA):
        with open(ARCHIVO_MEMORIA, 'r') as f:
            return set(f.read().splitlines())
    os.makedirs(os.path.dirname(ARCHIVO_MEMORIA), exist_ok=True)
    return set()

def guardar_memoria(id_alerta, memoria_set):
    memoria_set.add(id_alerta)
    with open(ARCHIVO_MEMORIA, 'a') as f:
        f.write(f"{id_alerta}\n")

def tg_safe(texto):
    texto = re.sub(r'<[^>]+>', '', texto)
    return texto.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def enviar_mensaje(mensaje):
    if not TOKEN_ALERTAS or not CHAT_ID_ALERTAS:
        print("-> [ERROR] Falta Token o Chat ID.")
        return
    try:
        url = f"https://api.telegram.org/bot{TOKEN_ALERTAS}/sendMessage"
        res = sesion.post(url, data={'chat_id': CHAT_ID_ALERTAS, 'text': mensaje, 'parse_mode': 'HTML'}, timeout=10)
        print(f"-> [TELEGRAM] Envío exitoso. Código: {res.status_code}")
    except Exception as e:
        print(f"-> [TELEGRAM ERROR]: {e}")

# ==========================================
# 1. EL "ROBADOR" DE TOKENS (NUEVO)
# ==========================================
def obtener_token_fresco():
    print("-> [AUTH] Buscando un token fresco en la web del SMN...")
    try:
        # Entramos a la home del SMN para que nos tiren el HTML inicial
        res = requests.get('https://www.smn.gob.ar/', verify=False, timeout=10)
        
        # Buscamos el token JWT. Todos arrancan con "eyJhbGciOiJIUzI1NiJ9"
        match = re.search(r'(eyJhbGciOiJIUzI1NiJ9\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)', res.text)
        
        if match:
            token = match.group(1)
            print("-> [AUTH] ¡Llave maestra obtenida con éxito!")
            return token
        else:
            print("-> [AUTH] No se encontró el token en el HTML frontal. Puede que lo carguen por JS.")
            return None
    except Exception as e:
        print(f"-> [AUTH ERROR]: {e}")
        return None

# ==========================================
# 2. MÓDULO API JSON DIRECTA
# ==========================================
def test_api_json_smn(memoria):
    print("--------------------------------------------------")
    print("-> INICIANDO TEST API JSON VARELA...")
    
    # 1. Conseguimos la llave antes de abrir la puerta
    token = obtener_token_fresco()
    if token:
        sesion.headers.update({'Authorization': f'JWT {token}'})
    else:
        print("-> [ADVERTENCIA] Vamos a intentar entrar sin token a ver si pasa...")

    try:
        tiempo_actual = int(datetime.now().timestamp())
        res = sesion.get(f"{URL_API_SMN_VARELA}?nocache={tiempo_actual}", timeout=15)
        print(f"-> [HTTP STATUS]: {res.status_code}")
        
        if res.status_code != 200:
            print(f"-> [ERROR] Aún no logramos entrar. Código: {res.status_code}")
            enviar_mensaje(f"🚨 <b>TEST API FALLIDO</b>\n\nEl servidor respondió con código {res.status_code}.")
            return
            
        data = res.json()
        warnings = data.get("warnings", [])
        alertas_disparadas = 0

        mapa_niveles = {
            2: ("amarillo", "🟡", "*Riesgo meteorológico leve*"),
            3: ("naranja", "🟠", "*Riesgo meteorológico moderado*"),
            4: ("rojo", "🔴", "*Riesgo meteorológico alto*")
        }
        nombres_eventos = {37: "Viento", 39: "Lluvia", 41: "Tormenta", 54: "Zonda"}

        for warning in warnings:
            dia_alerta = warning.get("date")
            max_level = warning.get("max_level", 0)
            
            if max_level <= 1: continue
            
            for evento in warning.get("events", []):
                ev_id = evento.get("id")
                niveles_franjas = evento.get("levels", {})
                
                for franja, nivel_val in niveles_franjas.items():
                    if nivel_val and nivel_val > 1:
                        id_unico = f"TEST_API_{dia_alerta}_{ev_id}_{franja}_{nivel_val}"
                        print(f"     => ¡ALERTA ENCONTRADA! {dia_alerta} - Franja: {franja} | Nivel: {nivel_val}")
                        
                        if id_unico not in memoria:
                            nombre_evento = nombres_eventos.get(ev_id, f"Fenómeno {ev_id}")
                            nivel_str, emoji, riesgo = mapa_niveles.get(nivel_val, ("alerta", "⚠️", ""))
                            
                            msg = (f"🌐 <b>ALERTA OFICIAL (API JSON VARELA)</b>\n\n"
                                   f"‼️⚠️ Alerta por <b>\"{tg_safe(nombre_evento.upper())}\"</b>\n"
                                   f"📅 <b>Fecha:</b> {dia_alerta} ({franja})\n"
                                   f"📍 Nivel {nivel_str.upper()} para <b>*Florencio Varela*</b>.\n\n"
                                   f"{emoji} {riesgo}")
                            
                            enviar_mensaje(msg)
                            guardar_memoria(id_unico, memoria)
                            alertas_disparadas += 1

        if alertas_disparadas == 0:
            print("\n-> [INFO] Conexión 200 OK. No hay alertas nivel Amarillo o superior.")
            enviar_mensaje("🤖 <b>TEST API JSON EXITOSO</b>\n\n¡La extracción automática de la llave funcionó! Me conecté al SMN y obtuve los datos (Status 200).")
                            
    except Exception as e:
        print(f"-> [ERROR CRÍTICO]: {e}")

if __name__ == '__main__':
    memoria_actual = cargar_memoria()
    test_api_json_smn(memoria_actual)
    print("--------------------------------------------------")
