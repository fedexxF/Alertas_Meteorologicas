import os
import io
import re
import html
import hashlib
import urllib3
import requests
from datetime import datetime, timedelta

# --- CONFIGURACIÓN GLOBAL Y CONSTANTES ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TOKEN_ALERTAS = os.environ.get('TELEGRAM_TOKEN_ALERTAS') or os.environ.get('TELEGRAM_TOKEN')
CHAT_ID_ALERTAS = os.environ.get('CHAT_ID_ALERTAS') or os.environ.get('CHAT_ID')

# LA URL EXACTA DEL WEBSERVICE 
URL_API_SMN_VARELA = 'https://ws1.smn.gob.ar/v1/warning/alert/area/766'

ARCHIVO_MEMORIA = "data/memoria_bot.txt"

# Sesión persistente simulando navegación legítima
sesion = requests.Session()
sesion.verify = False
sesion.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36',
    'Origin': 'https://www.smn.gob.ar',
    'Referer': 'https://www.smn.gob.ar/'
})

# ==========================================
# MÓDULOS DE MEMORIA Y HELPERS
# ==========================================
def cargar_memoria():
    tiempo_actual = int(datetime.now().timestamp())
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
# MÓDULO API JSON DIRECTA (ZONA 766) - MODO TEST
# ==========================================
def test_api_json_smn(memoria):
    print("--------------------------------------------------")
    print("-> INICIANDO TEST DE API JSON PARA VARELA...")
    print(f"-> Conectando a {URL_API_SMN_VARELA}")
    
    try:
        tiempo_actual = int(datetime.now().timestamp())
        res = sesion.get(f"{URL_API_SMN_VARELA}?nocache={tiempo_actual}", timeout=15)
        
        print(f"-> [HTTP STATUS]: {res.status_code}")
        if res.status_code != 200:
            print("-> [ERROR] El servidor no respondió 200 OK.")
            enviar_mensaje(f"🚨 <b>TEST API FALLIDO</b>\n\nEl servidor respondió con código {res.status_code}.")
            return
            
        data = res.json()
        warnings = data.get("warnings", [])
        reports = data.get("reports", [])
        
        print(f"-> [DATOS] Se encontraron {len(warnings)} días de pronóstico.")
        
        mapa_niveles = {
            1: ("verde", "🟢", "Tranquilidad"),
            2: ("amarillo", "🟡", "*Riesgo meteorológico leve*"),
            3: ("naranja", "🟠", "*Riesgo meteorológico moderado*"),
            4: ("rojo", "🔴", "*Riesgo meteorológico alto*")
        }
        
        nombres_eventos = {37: "Viento", 39: "Lluvia", 41: "Tormenta", 54: "Zonda"}
        alertas_disparadas = 0

        for warning in warnings:
            dia_alerta = warning.get("date")
            max_level = warning.get("max_level", 0)
            
            print(f"\n  -> Revisando día: {dia_alerta} (Nivel máximo general: {max_level})")
            
            if max_level <= 1: 
                print("     (Sin alertas relevantes este día, saltando...)")
                continue
            
            for evento in warning.get("events", []):
                ev_id = evento.get("id")
                niveles_franjas = evento.get("levels", {})
                
                for franja, nivel_val in niveles_franjas.items():
                    if nivel_val and nivel_val > 1: # Solo amarillos, naranjas o rojos
                        id_unico = f"TEST_API_{dia_alerta}_{ev_id}_{franja}_{nivel_val}"
                        print(f"     => ¡ALERTA ENCONTRADA! Franja: {franja} | Nivel: {nivel_val}")
                        
                        if id_unico not in memoria:
                            nombre_evento = nombres_eventos.get(ev_id, f"Fenómeno {ev_id}")
                            nivel_str, emoji, riesgo = mapa_niveles.get(nivel_val, ("alerta", "⚠️", ""))
                            
                            msg = (f"🌐 <b>ALERTA OFICIAL (API JSON)</b>\n\n"
                                   f"‼️⚠️ Alerta por <b>\"{tg_safe(nombre_evento.upper())}\"</b>\n"
                                   f"📅 <b>Fecha:</b> {dia_alerta} ({franja})\n"
                                   f"📍 Nivel {nivel_str.upper()} para <b>*Florencio Varela*</b>.\n\n"
                                   f"{emoji} {riesgo}")
                            
                            enviar_mensaje(msg)
                            guardar_memoria(id_unico, memoria)
                            alertas_disparadas += 1
                        else:
                            print("     (Esta alerta ya estaba en la memoria, no se envía).")

        # Mensaje de diagnóstico si la conexión funciona pero no hay alertas fuertes
        if alertas_disparadas == 0:
            print("\n-> [INFO] Conexión exitosa, pero no se encontraron alertas nivel Amarillo o superior para enviar.")
            enviar_mensaje("🤖 <b>TEST API JSON EXITOSO</b>\n\nMe conecté perfectamente al servidor interno del SMN (Status 200). En este exacto momento no hay alertas Amarillas, Naranjas o Rojas para las próximas horas en Florencio Varela, por eso no mandaba nada.")
                            
    except Exception as e:
        print(f"-> [ERROR CRÍTICO EXCEPCIÓN]: {e}")
        enviar_mensaje(f"🚨 <b>TEST API FALLIDO POR ERROR:</b>\n<code>{e}</code>")

# ==========================================
# PUNTO DE ENTRADA MAIN (SOLO TEST API)
# ==========================================
if __name__ == '__main__':
    memoria_actual = cargar_memoria()
    
    # ÚNICA FUNCIÓN HABILITADA PARA TESTEAR
    test_api_json_smn(memoria_actual)
    
    # RSS Y RESTO DE FUNCIONES MUTEADAS (COMENTADAS)
    # procesar_cap(memoria_actual, None)
    # procesar_acp(memoria_actual, None)
    # procesar_shn(memoria_actual)
    # procesar_radar(memoria_actual, None)
    
    print("--------------------------------------------------")
