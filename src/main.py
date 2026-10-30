import os
import re
import urllib3
import requests
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TOKEN_ALERTAS = os.environ.get('TELEGRAM_TOKEN_ALERTAS') or os.environ.get('TELEGRAM_TOKEN')
CHAT_ID_ALERTAS = os.environ.get('CHAT_ID_ALERTAS') or os.environ.get('CHAT_ID')

URL_API_SMN_VARELA = 'https://ws1.smn.gob.ar/v1/warning/alert/area/766'

# Sesión base con disfraz de navegador real
sesion = requests.Session()
sesion.verify = False
headers_navegador = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'es-AR,es;q=0.9,en-US;q=0.8,en;q=0.7',
    'Origin': 'https://www.smn.gob.ar',
    'Referer': 'https://www.smn.gob.ar/'
}
sesion.headers.update(headers_navegador)

def tg_safe(texto):
    texto = re.sub(r'<[^>]+>', '', texto)
    return texto.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def enviar_mensaje(mensaje):
    if not TOKEN_ALERTAS or not CHAT_ID_ALERTAS:
        return
    try:
        url = f"https://api.telegram.org/bot{TOKEN_ALERTAS}/sendMessage"
        sesion.post(url, data={'chat_id': CHAT_ID_ALERTAS, 'text': mensaje, 'parse_mode': 'HTML'}, timeout=10)
    except:
        pass

# ==========================================
# EL EXTRACTOR DE TOKENS (VERSIÓN BLINDADA)
# ==========================================
def obtener_token_fresco():
    print("-> [AUTH] Entrando a la web del SMN como navegador para capturar el token...")
    try:
        res = requests.get('https://www.smn.gob.ar/', headers=headers_navegador, verify=False, timeout=15)
        
        # Búsqueda exacta del comando Javascript
        match = re.search(r"localStorage\.setItem\(\s*['\"]token['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)", res.text)
        
        if match:
            token = match.group(1)
            print(f"-> [AUTH] ¡Llave extraída exitosamente! (Primeros caracteres: {token[:15]}...)")
            return token
            
        # Plan B por si sacaron el LocalStorage: Buscar cualquier JWT puro
        match_b = re.search(r'(eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)', res.text)
        if match_b:
            token = match_b.group(1)
            print(f"-> [AUTH] Llave encontrada por Plan B (Primeros caracteres: {token[:15]}...)")
            return token
            
        print("-> [AUTH] FRACASO: El token ya no está visible en el HTML.")
        return None
    except Exception as e:
        print(f"-> [AUTH ERROR CRÍTICO]: {e}")
        return None

# ==========================================
# TEST API JSON
# ==========================================
def test_api_json_smn():
    print("--------------------------------------------------")
    print("-> INICIANDO INFILTRACIÓN A LA API SMN...")
    
    token = obtener_token_fresco()
    if token:
        sesion.headers.update({'Authorization': f'JWT {token}'})
    else:
        print("-> [ADVERTENCIA] Intentando entrar sin token... es casi seguro un 401.")

    try:
        tiempo_actual = int(datetime.now().timestamp())
        res = sesion.get(f"{URL_API_SMN_VARELA}?nocache={tiempo_actual}", timeout=15)
        print(f"-> [HTTP STATUS]: {res.status_code}")
        
        if res.status_code != 200:
            print("-> [ERROR] ¡Nos rechazaron en la puerta de nuevo!")
            enviar_mensaje(f"🚨 <b>TEST API FALLIDO</b>\n\nStatus: {res.status_code}.")
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
                        print(f"     => ¡ALERTA ENCONTRADA! {dia_alerta} - Franja: {franja} | Nivel: {nivel_val}")
                        
                        nombre_evento = nombres_eventos.get(ev_id, f"Fenómeno {ev_id}")
                        nivel_str, emoji, riesgo = mapa_niveles.get(nivel_val, ("alerta", "⚠️", ""))
                        
                        msg = (f"🌐 <b>ALERTA OFICIAL (API JSON VARELA)</b>\n\n"
                               f"‼️⚠️ Alerta por <b>\"{tg_safe(nombre_evento.upper())}\"</b>\n"
                               f"📅 <b>Fecha:</b> {dia_alerta} ({franja})\n"
                               f"📍 Nivel {nivel_str.upper()} para <b>*Florencio Varela*</b>.\n\n"
                               f"{emoji} {riesgo}")
                        
                        enviar_mensaje(msg)
                        alertas_disparadas += 1

        if alertas_disparadas == 0:
            print("\n-> [INFO] Status 200 OK. Entramos y bajamos el JSON vacío de alertas.")
            enviar_mensaje("🤖 <b>¡VICTORIA! TEST API JSON EXITOSO</b>\n\nLogramos evadir la seguridad, robar el token y leer el JSON oficial del SMN. En este momento no hay alertas marcadas en Varela en la API.")
                            
    except Exception as e:
        print(f"-> [ERROR CRÍTICO]: {e}")

if __name__ == '__main__':
    test_api_json_smn()
    print("--------------------------------------------------")
