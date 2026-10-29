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
AREA_INTERES = PUNTO_INTERES.buffer(0.054) # Equivalente exacto a 6 km de radio

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

def parsear_fecha(fecha_iso, sumar_minuto=False):
    try:
        dt = datetime.strptime(limpiar_cdata(fecha_iso)[:19], "%Y-%m-%dT%H:%M:%S") - timedelta(hours=3)
        if sumar_minuto:
            dt += timedelta(minutes=1)
        dias = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"]
        return f"{dias[dt.weekday()]} {dt.strftime('%d/%m')}", dt.strftime('%H:%M')
    except:
        return "N/A", "XX:XX"

def obtener_link(item_xml):
    match = re.search(r'<link[^>]*href=["\'](.*?)["\']', item_xml, re.I) or re.search(r'<link>\s*(.*?)\s*</link>', item_xml, re.I | re.DOTALL)
    return match.group(1).strip() if match else None

# ==========================================
# MÓDULOS DE TELEGRAM (ENRUTAMIENTO DOBLE)
# ==========================================
def enviar_mensaje(mensaje, tipo="alerta"):
    token = TOKEN_ALERTAS if tipo == "alerta" else TOKEN_RADAR
    chat_id = CHAT_ID_ALERTAS if tipo == "alerta" else CHAT_ID_RADAR
    if not token or not chat_id: return
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        res = sesion.post(url, data={'chat_id': chat_id, 'text': mensaje, 'parse_mode': 'HTML'}, timeout=10)
        if res.status_code != 200:
            texto_plano = re.sub(r'<[^>]+>', '', mensaje) 
            sesion.post(url, data={'chat_id': chat_id, 'text': texto_plano}, timeout=10)
    except Exception as e:
        print(f"Error Telegram MSG ({tipo}): {e}")

def enviar_foto(caption, photo_url=None, tipo="alerta"):
    token = TOKEN_ALERTAS if tipo == "alerta" else TOKEN_RADAR
    chat_id = CHAT_ID_ALERTAS if tipo == "alerta" else CHAT_ID_RADAR
    if not token or not chat_id: return
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        data = {'chat_id': chat_id, 'caption': caption, 'photo': photo_url, 'parse_mode': 'HTML'}
        res = sesion.post(url, data=data, timeout=15)
        if res.status_code != 200:
            data['caption'] = re.sub(r'<[^>]+>', '', caption)
            data.pop('parse_mode', None)
            sesion.post(url, data=data, timeout=15)
    except Exception as e:
        print(f"Error Telegram FOTO ({tipo}): {e}")

# ==========================================
# 1. MÓDULO API JSON DIRECTA CON PRINTS DE CONTROL
# ==========================================
def procesar_api_json_smn(memoria):
    print("-> [API SMN] Conectando a https://www.smn.gob.ar/api/v1/alertas/766...")
    try:
        tiempo_actual = int(datetime.now().timestamp())
        res = sesion.get(f"{URL_API_SMN_VARELA}?nocache={tiempo_actual}", timeout=15)
        
        print(f"-> [API SMN] Código de estado HTTP recibido: {res.status_code}")
        if res.status_code != 200: 
            print("-> [API SMN] Error: La API no devolvió un código 200.")
            return
            
        data = res.json()
        print("-> [API SMN] Datos JSON descargados correctamente.")
        
        updated_str = data.get("updated", "")
        dt_actualizacion = "N/A"
        if updated_str:
            try:
                dt_obj = datetime.strptime(updated_str[:19], "%Y-%m-%dT%H:%M:%S") - timedelta(hours=3)
                dt_actualizacion = dt_obj.strftime("%d/%m a las %H:%M hs")
            except:
                dt_actualizacion = updated_str
                
        warnings = data.get("warnings", [])
        reports = data.get("reports", [])
        print(f"-> [API SMN] Días de advertencias encontrados en el JSON: {len(warnings)}")
        
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

        franjas_nombres = {
            "early_morning": "Madrugada",
            "morning": "Mañana",
            "afternoon": "Tarde",
            "night": "Noche"
        }

        for warning in warnings:
            dia_alerta = warning.get("date")
            max_level = warning.get("max_level", 0)
            print(f"-> [API SMN] Fecha evaluada: {dia_alerta} | Nivel máximo: {max_level}")
            
            if max_level == 0: 
                continue
            
            for evento in warning.get("events", []):
                ev_id = evento.get("id")
                niveles_franjas = evento.get("levels", {})
                
                for franja, nivel_val in niveles_franjas.items():
                    if nivel_val and nivel_val > 0:
                        id_unico_alerta = f"API_JSON_{dia_alerta}_{ev_id}_{franja}_{nivel_val}"
                        print(f"-> [API SMN] ¡Alerta detectada! Evento ID: {ev_id}, Franja: {franja}, Nivel: {nivel_val}")
                        
                        if id_unico_alerta not in memoria:
                            nombre_evento = nombres_eventos.get(ev_id, f"Fenómeno ID {ev_id}")
                            nivel_str, emoji, riesgo = mapa_niveles.get(nivel_val, ("amarillo", "🟡", ""))
                            
                            desc_texto = "Sin descripción detallada."
                            for rep in reports:
                                if rep.get("event_id") == ev_id:
                                    for lev_info in rep.get("levels", []):
                                        if lev_info.get("level") == nivel_val:
                                            desc_texto = lev_info.get("description", "")
                            
                            franja_amigable = franjas_nombres.get(franja, franja)
                            
                            msg = (f"🌐 <b>REPORTE DIRECTO WEB/API (Varela)</b>\n\n"
                                   f"⚠️ Actualizado a las: <b>{dt_actualizacion}</b>\n\n"
                                   f"‼️⚠️ Alerta por <b>\"{tg_safe(nombre_evento.upper())}\"</b>\n"
                                   f"📅 <b>Fecha:</b> {dia_alerta} ({franja_amigable})\n"
                                   f"📍 Nivel {nivel_str.upper()} para <b>*Florencio Varela*</b>.\n\n"
                                   f"{tg_safe(desc_texto)}\n\n"
                                   f"{emoji} {riesgo}")
                            
                            print("-> [API SMN] Enviando alerta a Telegram...")
                            enviar_mensaje(msg, tipo="alerta")
                            guardar_memoria(id_unico_alerta, memoria)
                        else:
                            print("-> [API SMN] La alerta ya estaba registrada en la memoria. Omitiendo envío.")
                            
    except Exception as e:
        print(f"-> [API SMN ERROR CRÍTICO]: {e}")
        err_id = f"ERR_APIJSON_{hashlib.md5(str(e).encode()).hexdigest()[:8]}"
        if err_id not in memoria:
            enviar_mensaje(f"⚠️ <b>ALERTA DE SISTEMA:</b> Fallo consultando la API JSON del SMN. Error: <code>{tg_safe(str(e))}</code>", tipo="alerta")
            guardar_memoria(err_id, memoria)

# ==========================================
# RESTO DE FUNCIONES (Radar, CAP, ACP, SHN)
# ==========================================
def obtener_url_radar(memoria):
    try:
        tiempo_actual = int(datetime.now().timestamp())
        res = sesion.get(f"{URL_RADAR_WEB}?nocache={tiempo_actual}", timeout=10)
        img_tag = BeautifulSoup(res.text, 'html.parser').find('img', src=re.compile(r'radar|ezeiza', re.I))
        if img_tag and 'src' in img_tag.attrs:
            url = img_tag['src']
            return url if url.startswith('http') else f"https://www.climasurgba.com.ar{url}"
    except Exception as e:
        pass
    return None

def intercepta_varela(xml_raw):
    poly_matches = re.findall(r'<[^>]*polygon[^>]*>(.*?)</[^>]*polygon>', xml_raw, re.I | re.DOTALL)
    for poly_str in poly_matches:
        valores = limpiar_cdata(poly_str).replace(',', ' ').split()
        coords = []
        for j in range(0, len(valores)-1, 2):
            try: coords.append((float(valores[j+1]), float(valores[j])))
            except ValueError: pass
        if len(coords) >= 3 and Polygon(coords).intersects(AREA_INTERES):
            return True
    return NOMBRE_LOCALIDAD.lower() in xml_raw.lower()

def procesar_radar(memoria, url_imagen_radar):
    if not url_imagen_radar: return
    fecha_actual = datetime.now() - timedelta(hours=3)
    bloque_30 = "00" if fecha_actual.minute < 30 else "30"
    base_id = f"ECOS_{fecha_actual.strftime('%Y-%m-%d_%H')}_{bloque_30}"
    id_25, id_60, id_100 = f"{base_id}_25", f"{base_id}_60", f"{base_id}_100"
    try:
        tiempo_actual = int(datetime.now().timestamp())
        separador = "&" if "?" in url_imagen_radar else "?"
        img_res = sesion.get(f"{url_imagen_radar}{separador}nocache={tiempo_actual}", timeout=10)
        img = Image.open(io.BytesIO(img_res.content)).convert('RGB')
        arr = np.array(img)
        alto, ancho, _ = arr.shape
        centro_x, centro_y = (ancho // 2) - 65, (alto // 2) + 45
        radio_max = min(alto // 2, ancho // 2) * 0.95
        r_100 = radio_max * (100 / 240)
        r_60 = radio_max * (60 / 240)
        r_25 = radio_max * (25 / 240)
        Y, X = np.ogrid[:alto, :ancho]
        dist_sq = (X - centro_x)**2 + (Y - centro_y)**2
        F_PEND, F_OFF = 0.1763, 0.5824
        mask_chord_60 = X <= (centro_x + r_60 * F_OFF + (Y - centro_y) * F_PEND)
        mask_chord_100 = X <= (centro_x + r_100 * F_OFF + (Y - centro_y) * F_PEND)
        anillo_25 = (dist_sq <= r_25**2)
        anillo_60 = (dist_sq > r_25**2) & (dist_sq <= r_60**2) & mask_chord_60
        anillo_100 = (dist_sq > r_60**2) & (dist_sq <= r_100**2) & mask_chord_100
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        es_35dbz = (r > 180) & (g < 160)
        mensajes = []
        if np.sum(es_35dbz & anillo_25) > 30 and id_25 not in memoria:
            mensajes.append("🟣 <b>¡ALERTA CERCANA! ECOS EN ZONA NÚCLEO (de <b>*Florencio Varela*</b>)</b> 🟣")
            guardar_memoria(id_25, memoria)
        if mensajes:
            for m in mensajes: enviar_mensaje(m, tipo="radar")
            enviar_foto(caption="📡 Radar del momento", photo_url=url_imagen_radar, tipo="radar")
    except Exception as e:
        pass

def procesar_cap(memoria, url_radar):
    try:
        headers_anticache = {"Cache-Control": "no-cache"}
        res = sesion.get(URL_ALERTAS, headers=headers_anticache, timeout=10)
        if res.status_code != 200: return
        items = re.findall(r'<item>(.*?)</item>', res.text, re.DOTALL | re.I)
        for item in items:
            url_xml = obtener_link(item)
            if not url_xml: continue
            id_xml = url_xml.split('/')[-1]
            if f"VARELA_SI_{id_xml}" in memoria or id_xml in memoria: continue
            if f"VARELA_NO_{id_xml}" in memoria: continue
            try:
                xml_raw = sesion.get(url_xml, headers=headers_anticache, timeout=15).text
                xml_raw = re.sub(r'<(/?)[a-zA-Z0-9_]+:([a-zA-Z0-9_]+)', r'<\1\2', xml_raw)
            except: continue
            if intercepta_varela(xml_raw):
                guardar_memoria(f"VARELA_SI_{id_xml}", memoria)
                info = re.search(r'<info[^>]*>(.*?)</info>', xml_raw, re.DOTALL | re.I)
                info_txt = info.group(1) if info else xml_raw
                evento = limpiar_cdata((re.search(r'<event[^>]*>(.*?)</event>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: "FENÓMENO"})).group(1)).upper()
                msg = f"📡 <b>REPORTE TRADICIONAL RSS (CAP)</b>\n\n‼️ Alerta por \"{tg_safe(evento)}\" para <b>*Florencio Varela*</b>."
                enviar_mensaje(msg, tipo="alerta")
            else:
                guardar_memoria(f"VARELA_NO_{id_xml}", memoria)
    except: pass

def procesar_acp(memoria, url_radar):
    pass

def procesar_shn(memoria):
    pass

# ==========================================
# PUNTO DE ENTRADA MAIN
# ==========================================
if __name__ == '__main__':
    print("--- INICIANDO CICLO DEL BOT ---")
    memoria_actual = cargar_memoria()
    radar_url = obtener_url_radar(memoria_actual)
    
    # 1. Ejecutamos la API JSON con rastreo en consola
    procesar_api_json_smn(memoria_actual)
    
    # 2. Ejecutamos el RSS tradicional
    procesar_cap(memoria_actual, radar_url)
    
    print("--- CICLO FINALIZADO ---")
