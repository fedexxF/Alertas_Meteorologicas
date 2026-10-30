import os
import re
import time
import urllib3
import requests
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

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
        return
    try:
        url = f"https://api.telegram.org/bot{TOKEN_ALERTAS}/sendMessage"
        requests.post(url, data={'chat_id': CHAT_ID_ALERTAS, 'text': mensaje, 'parse_mode': 'HTML'}, timeout=10, verify=False)
    except:
        pass

# ==========================================
# EXTRACTOR AUTOMÁTICO DE TOKEN CON SELENIUM
# ==========================================
def obtener_token_automatico():
    print("-> [AUTO-AUTH] Abriendo navegador oculto para obtener token fresco...")
    options = Options()
    options.add_argument("--headless")  # Ejecuta sin abrir ventana visual
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    driver = webdriver.Chrome(options=options)
    try:
        driver.get("https://www.smn.gob.ar/")
        time.sleep(3)  # Espera a que cargue la web y ejecute el JS
        
        # Extrae el token directamente del localStorage del navegador
        token = driver.execute_script("return localStorage.getItem('token');")
        if token:
            print(f"-> [AUTO-AUTH] ¡Token obtenido con éxito de forma autónoma!")
            return token
        else:
            print("-> [AUTO-AUTH] No se encontró el token en el almacenamiento local.")
            return None
    except Exception as e:
        print(f"-> [AUTO-AUTH ERROR]: {e}")
        return None
    finally:
        driver.quit()

# ==========================================
# PROCESAMIENTO DE LA API DE VARELA
# ==========================================
def procesar_api_json_smn(memoria):
    print("--------------------------------------------------")
    print("-> INICIANDO CONSULTA AUTOMATIZADA A LA API...")
    
    # 1. Obtenemos el token fresco de forma automática
    token = obtener_token_automatico()
    if not token:
        print("-> [ERROR] No se pudo automatizar la obtención del token.")
        return

    # 2. Configuramos la sesión con el token recién robado por el bot
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
        print(f"-> [HTTP STATUS]: {res.status_code}")
        
        if res.status_code != 200:
            print("-> [ERROR] El servidor rechazó el token automático (Status code distinto de 200).")
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
            print("-> 200 OK. El bot se conectó solo, obtuvo la llave y verificó que no hay alertas nuevas.")
                            
    except Exception as e:
        print(f"-> [ERROR CRÍTICO]: {e}")

if __name__ == '__main__':
    memoria_actual = cargar_memoria()
    procesar_api_json_smn(memoria_actual)
    print("--------------------------------------------------")
