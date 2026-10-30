import os
import re
import urllib3
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TOKEN_ALERTAS = os.environ.get('TELEGRAM_TOKEN_ALERTAS') or os.environ.get('TELEGRAM_TOKEN')
CHAT_ID_ALERTAS = os.environ.get('CHAT_ID_ALERTAS') or os.environ.get('CHAT_ID')

URL_API_SMN_VARELA = 'https://ws1.smn.gob.ar/v1/warning/alert/area/766'
ARCHIVO_MEMORIA = "data/memoria_bot.txt"

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
        print("-> [ERROR TELEGRAM] Faltan las credenciales de Telegram.")
        return
    try:
        url = f"https://api.telegram.org/bot{TOKEN_ALERTAS}/sendMessage"
        res = requests.post(url, data={'chat_id': CHAT_ID_ALERTAS, 'text': mensaje, 'parse_mode': 'HTML'}, timeout=10, verify=False)
        print(f"-> [TELEGRAM STATUS]: {res.status_code}")
    except Exception as e:
        print(f"-> [ERROR ENVIANDO A TELEGRAM]: {e}")

# ==========================================
# EXTRACTOR AUTOMÁTICO CON DIAGNÓSTICO
# ==========================================
def obtener_token_automatico():
    print("-> [AUTO-AUTH] Iniciando interceptor de red con Playwright...")
    token_jwt = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            def interceptar_peticion(request):
                nonlocal token_jwt
                if "ws1.smn.gob.ar" in request.url:
                    auth_header = request.headers.get("authorization")
                    if auth_header and not token_jwt:
                        token_jwt = auth_header
                        print("-> [AUTO-AUTH] ¡Token interceptado al vuelo desde la red!")

            page.on("request", interceptar_peticion)
            page.goto("https://www.smn.gob.ar/mapas/alertas", timeout=40000)
            page.wait_for_timeout(8000) # Damos un poco más de margen
            browser.close()
            
            if token_jwt:
                token_puro = token_jwt.replace("JWT ", "").replace("jwt ", "").strip()
                return token_puro
            else:
                print("-> [AUTO-AUTH] No se interceptó ninguna cabecera de autorización.")
                return None
    except Exception as e:
        print(f"-> [AUTO-AUTH ERROR CRÍTICO]: {e}")
        enviar_mensaje(f"🚨 <b>ERROR EN PLAYWRIGHT</b>\n<code>{e}</code>")
        return None

# ==========================================
# PROCESAMIENTO DE LA API DE VARELA
# ==========================================
def procesar_api_json_smn(memoria):
    print("--------------------------------------------------")
    print("-> INICIANDO CONSULTA AUTOMATIZADA...")
    
    token = obtener_token_automatico()
    if not token:
        enviar_mensaje("⚠️ <b>Bot Clima:</b> Falló la obtención automática del Token.")
        print("-> [ERROR] No se pudo avanzar sin token.")
        return

    sesion = requests.Session()
    sesion.verify = False
    sesion.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36',
        'Origin': 'https://www.smn.gob.ar',
        'Referer': 'https://www.smn.gob.ar/',
        'Authorization': f'JWT {token}'
    })

    try:
        res = sesion.get(URL_API_SMN_VARELA, timeout=15)
        print(f"-> [HTTP STATUS DE VARELA]: {res.status_code}")
        
        if res.status_code != 200:
            enviar_mensaje(f"⚠️ <b>Bot Clima:</b> El SMN rechazó el token automático (Status {res.status_code}).")
            print(f"-> [ERROR] El servidor rechazó la llave automática. Código: {res.status_code}")
            return
            
        data = res.json()
        warnings = data.get("warnings", [])
        reports = data.get("reports", []) 
        alertas_disparadas = 0

        mapa_niveles = {
            2: ("amarillo", "🟡", "*Riesgo meteorológico leve*"),
            3: ("amarillo", "🟡", "*Riesgo meteorológico leve*"),
            4: ("naranja", "🟠", "*Riesgo meteorológico moderado*"),
            5: ("rojo", "🔴", "*Riesgo meteorológico alto*")
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
                        id_unico = f"AUTO_API_{dia_alerta}_{ev_id}_{franja}_{nivel_val}"
                        
                        if id_unico not in memoria:
                            nombre_evento = nombres_eventos.get(ev_id, f"Fenómeno {ev_id}")
                            nivel_str, emoji, riesgo = mapa_niveles.get(nivel_val, ("alerta", "⚠️", ""))
                            
                            desc_texto = "Sin descripción detallada."
                            for rep in reports:
                                if rep.get("event_id") == ev_id:
                                    for lev_info in rep.get("levels", []):
                                        if lev_info.get("level") == nivel_val:
                                            desc_texto = lev_info.get("description", desc_texto)
                                            break
                            
                            msg = (f"🌐 <b>ALERTA OFICIAL (API JSON VARELA)</b>\n\n"
                                   f"‼️⚠️ Alerta por <b>\"{tg_safe(nombre_evento.upper())}\"</b>\n"
                                   f"📅 <b>Fecha:</b> {dia_alerta} ({franja})\n"
                                   f"📍 Nivel {nivel_str.upper()} para <b>*Florencio Varela*</b>.\n\n"
                                   f"📝 <b>Detalle:</b>\n{tg_safe(desc_texto)}\n\n"
                                   f"{emoji} {riesgo}")
                            
                            enviar_mensaje(msg)
                            guardar_memoria(id_unico, memoria)
                            alertas_disparadas += 1

        if alertas_disparadas == 0:
            print("-> 200 OK. Ciclo completo sin nuevas alertas a disparar.")
            # Opcional: descomentá la siguiente línea si querés que te avise en Telegram que funcionó pero no hay alertas nuevas
            # enviar_mensaje("🟢 <b>Bot Clima:</b> Ejecución exitosa (Status 200). Sin alertas nuevas en Varela.")
                            
    except Exception as e:
        print(f"-> [ERROR CRÍTICO EN PETICIÓN]: {e}")
        enviar_mensaje(f"🚨 <b>Error crítico en el bot:</b> <code>{e}</code>")

if __name__ == '__main__':
    memoria_actual = cargar_memoria()
    procesar_api_json_smn(memoria_actual)
    print("--------------------------------------------------")
