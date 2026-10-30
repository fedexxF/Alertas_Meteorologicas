import os
import io
import re
import html
import hashlib
import urllib3
import requests
import numpy as np
from PIL import Image
from datetime import datetime, timedelta
from shapely.geometry import Point, Polygon
from bs4 import BeautifulSoup

# --- CONFIGURACIÓN GLOBAL Y CONSTANTES ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Credenciales Bot 1: ALERTAS OFICIALES (SMN, SHN, ACP)
TOKEN_ALERTAS = os.environ.get('TELEGRAM_TOKEN_ALERTAS') or os.environ.get('TELEGRAM_TOKEN')
CHAT_ID_ALERTAS = os.environ.get('CHAT_ID_ALERTAS') or os.environ.get('CHAT_ID')

# Credenciales Bot 2: VIGILANCIA RADAR (Si no existen, usa el Bot 1 por defecto)
TOKEN_RADAR = os.environ.get('TELEGRAM_TOKEN_RADAR') or TOKEN_ALERTAS
CHAT_ID_RADAR = os.environ.get('CHAT_ID_RADAR') or CHAT_ID_ALERTAS

URL_ACP = 'https://ssl.smn.gob.ar/feeds/avisocorto_GeoRSS.xml'
URL_ALERTAS = 'https://ssl.smn.gob.ar/feeds/CAP/rss_alertaCAP_nuevo_2026.xml'
URL_SHN_XML = 'https://www.hidro.gob.ar/cap/CapRP_xml.asp'
URL_RADAR_WEB = "https://www.climasurgba.com.ar/radar/ezeiza"

# URL OFICIAL DE LA API JSON DEL SMN PARA VARELA (ID 766)
URL_API_SMN_VARELA = 'https://www.smn.gob.ar/api/v1/alertas/766'

NOMBRE_LOCALIDAD = "Varela"
PUNTO_INTERES = Point(-58.27, -34.79)
AREA_INTERES = PUNTO_INTERES.buffer(0.054) 

ARCHIVO_MEMORIA = "data/memoria_bot.txt"

# Sesión persistente para reciclar conexiones SSL
sesion = requests.Session()
sesion.verify = False
sesion.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36'})

# ==========================================
# MÓDULOS DE MEMORIA Y HELPERS
# ==========================================
def cargar_memoria():
    tiempo_actual = int(datetime.now().timestamp())
    if os.path.exists(ARCHIVO_MEMORIA):
        with open(ARCHIVO_MEMORIA, 'r') as f:
            lineas = f.read().splitlines()
        
        for linea in lineas:
            if linea.startswith("INIT_"):
                tiempo_init = int(linea.split("_")[1])
                if tiempo_actual - tiempo_init > 259200:
                    with open(ARCHIVO_MEMORIA, 'w') as f:
                        f.write(f"INIT_{tiempo_actual}\n")
                    return set([f"INIT_{tiempo_actual}"])
        
        if not any(l.startswith("INIT_") for l in lineas):
            with open(ARCHIVO_MEMORIA, 'a') as f:
                f.write(f"INIT_{tiempo_actual}\n")
            lineas.append(f"INIT_{tiempo_actual}")
        return set(lineas)
        
    os.makedirs(os.path.dirname(ARCHIVO_MEMORIA), exist_ok=True)
    with open(ARCHIVO_MEMORIA, 'w') as f:
        f.write(f"INIT_{tiempo_actual}\n")
    return set([f"INIT_{tiempo_actual}"])

def guardar_memoria(id_alerta, memoria_set):
    memoria_set.add(id_alerta)
    with open(ARCHIVO_MEMORIA, 'a') as f:
        f.write(f"{id_alerta}\n")
        
def limpiar_cdata(texto):
    texto = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', texto or "", flags=re.DOTALL)
    return html.unescape(texto).strip()

def tg_safe(texto):
    texto = re.sub(r'<[^>]+>', '', texto)
    return texto.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

# ==========================================
# MÓDULOS DE TELEGRAM
# ==========================================
def enviar_mensaje(mensaje, tipo="alerta"):
    token = TOKEN_ALERTAS if tipo == "alerta" else TOKEN_RADAR
    chat_id = CHAT_ID_ALERTAS if tipo == "alerta" else CHAT_ID_RADAR
    if not token or not chat_id: 
        print("-> [TELEGRAM] Token o Chat ID no configurados.")
        return
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        res = sesion.post(url, data={'chat_id': chat_id, 'text': mensaje, 'parse_mode': 'HTML'}, timeout=10)
        print(f"-> [TELEGRAM] Código respuesta de envío: {res.status_code}")
        if res.status_code != 200:
            texto_plano = re.sub(r'<[^>]+>', '', mensaje) 
            sesion.post(url, data={'chat_id': chat_id, 'text': texto_plano}, timeout=10)
    except Exception as e:
        print(f"Error Telegram MSG ({tipo}): {e}")

# ==========================================
# 1. MÓDULO API JSON DIRECTA (ZONA 766 - VARELA)
# ==========================================
def procesar_api_json_smn(memoria):
    print("-> [API Varela] Consultando endpoint JSON directo...")
    try:
        tiempo_actual = int(datetime.now().timestamp())
        res = sesion.get(f"{URL_API_SMN_VARELA}?nocache={tiempo_actual}", timeout=15)
        
        print(f"-> [API Varela] Código HTTP recibido: {res.status_code}")
        if res.status_code != 200: 
            print("-> [API Varela] Error en la respuesta HTTP.")
            return
            
        data = res.json()
        updated_str = data.get("updated", "")
        dt_actualizacion = updated_str[11:16] if len(updated_str) >= 16 else "N/A"
        
        warnings = data.get("warnings", [])
        reports = data.get("reports", [])
        print(f"-> [API Varela] Bloques de advertencias encontrados: {len(warnings)}")
        
        mapa_niveles = {
            1: ("amarillo", "🟡", "*Riesgo meteorológico leve*"),
            2: ("naranja", "🟠", "*Riesgo meteorológico moderado*"),
            3: ("rojo", "🔴", "*Riesgo meteorológico alto*")
        }

        nombres_eventos = {
            37: "Viento",
            39: "Lluvia",
            41: "Tormenta",
            54: "Zonda"
        }

        for warning in warnings:
            dia_alerta = warning.get("date")
            max_level = warning.get("max_level", 0)
            print(f"-> [API Varela] Fecha: {dia_alerta} | Nivel Máximo: {max_level}")
            
            if max_level == 0: continue
            
            for evento in warning.get("events", []):
                ev_id = evento.get("id")
                niveles_franjas = evento.get("levels", {})
                
                for franja, nivel_val in niveles_franjas.items():
                    if nivel_val and nivel_val > 0:
                        id_unico_alerta = f"API_JSON_{dia_alerta}_{ev_id}_{franja}_{nivel_val}"
                        print(f"-> [API Varela] -> Evento ID {ev_id} | Franja: {franja} | Nivel: {nivel_val}")
                        
                        if id_unico_alerta not in memoria:
                            nombre_evento = nombres_eventos.get(ev_id, f"Fenómeno ID {ev_id}")
                            nivel_str, emoji, riesgo = mapa_niveles.get(nivel_val, ("amarillo", "🟡", ""))
                            
                            desc_texto = "Sin descripción detallada."
                            for rep in reports:
                                if rep.get("event_id") == ev_id:
                                    niveles_rep = rep.get("levels", [])
                                    if niveles_rep:
                                        desc_texto = next((l.get("description") for l in niveles_rep if l.get("level") == nivel_val), niveles_rep[0].get("description", "Sin descripción"))
                            
                            msg = (f"🌐 <b>ALERTA OFICIAL (API VARELA)</b>\n\n"
                                   f"⚠️ Actualizado a las: <b>{dt_actualizacion} hs</b>\n\n"
                                   f"‼️⚠️ Alerta por <b>\"{tg_safe(nombre_evento.upper())}\"</b>\n"
                                   f"📅 <b>Fecha:</b> {dia_alerta} ({franja})\n"
                                   f"📍 Nivel {nivel_str.upper()} para <b>*Florencio Varela*</b>.\n\n"
                                   f"{tg_safe(desc_texto)}\n\n"
                                   f"{emoji} {riesgo}")
                            
                            print("-> [API Varela] ¡Disparando mensaje a Telegram!")
                            enviar_mensaje(msg, tipo="alerta")
                            guardar_memoria(id_unico_alerta, memoria)
                        else:
                            print("-> [API Varela] Esta alerta ya figuraba en la memoria del bot.")
                            
    except Exception as e:
        print(f"-> [ERROR API Varela]: {e}")

# ==========================================
# PUNTO DE ENTRADA MAIN (RSS COMENTADO)
# ==========================================
if __name__ == '__main__':
    print("=== INICIANDO BOT (SOLO API JSON DE VARELA) ===")
    memoria_actual = cargar_memoria()
    
    # 1. Ejecutamos únicamente la consulta directa a la API JSON del SMN para Varela
    procesar_api_json_smn(memoria_actual)
    
    # 2. RSS y otros canales desactivados para esta prueba:
    # procesar_cap(memoria_actual, radar_url)  <-- COMENTADO A PROPÓSITO
    
    print("=== CICLO FINALIZADO ===")
