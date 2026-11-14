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
from curl_cffi import requests as cffi_requests

# --- CONFIGURACIÓN GLOBAL Y CONSTANTES ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Credenciales Bot 1: ALERTAS OFICIALES (SMN, SHN, ACP)
TOKEN_ALERTAS = os.environ.get('TELEGRAM_TOKEN_ALERTAS') or os.environ.get('TELEGRAM_TOKEN')
CHAT_ID_ALERTAS = os.environ.get('CHAT_ID_ALERTAS') or os.environ.get('CHAT_ID')

# Credenciales Bot 2: VIGILANCIA RADAR (Si no existen, usa el Bot 1 por defecto)
TOKEN_RADAR = os.environ.get('TELEGRAM_TOKEN_RADAR') or TOKEN_ALERTAS
CHAT_ID_RADAR = os.environ.get('CHAT_ID_RADAR') or CHAT_ID_ALERTAS

# Credenciales Gist (Puente con ESP32)
GIST_ID = os.environ.get('GIST_ID')
GH_TOKEN = os.environ.get('GH_TOKEN')

URL_ACP = 'https://ssl.smn.gob.ar/feeds/CAP/avisocortoplazo/rss_acpCAP.xml'
URL_ALERTAS = 'https://ssl.smn.gob.ar/feeds/CAP/rss_alertaCAP_nuevo_2026.xml'
URL_SHN_XML = 'https://www.hidro.gob.ar/cap/CapRP_xml.asp'
URL_RADAR_WEB = "https://meteo.deroweb.com.ar/imagen_radar-eze.php"

NOMBRE_LOCALIDAD = "Varela"
PUNTO_INTERES = Point(-58.27, -34.79)
AREA_INTERES = PUNTO_INTERES.buffer(0.054)  # Equivalente exacto a 6 km de radio

ARCHIVO_MEMORIA = "data/memoria_bot.txt"

# Sesión inteligente con curl_cffi simulando Google Chrome
radar_sesion = cffi_requests.Session(impersonate="chrome120")
telegram_sesion = requests.Session()

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
# MÓDULOS DE TELEGRAM Y GIST
# ==========================================
def enviar_mensaje(mensaje, tipo="alerta"):
    token = TOKEN_ALERTAS if tipo == "alerta" else TOKEN_RADAR
    chat_id = CHAT_ID_ALERTAS if tipo == "alerta" else CHAT_ID_RADAR
    if not token or not chat_id: return
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        res = telegram_sesion.post(url, data={'chat_id': chat_id, 'text': mensaje, 'parse_mode': 'HTML'}, timeout=10)
        if res.status_code != 200:
            texto_plano = re.sub(r'<[^>]+>', '', mensaje) 
            telegram_sesion.post(url, data={'chat_id': chat_id, 'text': texto_plano}, timeout=10)
    except Exception as e:
        print(f"Error Telegram MSG ({tipo}): {e}")

def enviar_foto(caption, photo_url=None, tipo="alerta"):
    token = TOKEN_ALERTAS if tipo == "alerta" else TOKEN_RADAR
    chat_id = CHAT_ID_ALERTAS if tipo == "alerta" else CHAT_ID_RADAR
    if not token or not chat_id: return
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        img_bytes = None
        if photo_url:
            try:
                r_img = radar_sesion.get(photo_url, timeout=25)
                if r_img.status_code == 200 and len(r_img.content) > 1000:
                    img_bytes = r_img.content
            except Exception:
                pass
                
        if img_bytes:
            files = {'photo': ('radar.png', img_bytes, 'image/png')}
            data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}
            res = telegram_sesion.post(url, data=data, files=files, timeout=30)
        else:
            data = {'chat_id': chat_id, 'caption': caption, 'photo': photo_url, 'parse_mode': 'HTML'}
            res = telegram_sesion.post(url, data=data, timeout=15)
            
        if res.status_code != 200:
            data['caption'] = re.sub(r'<[^>]+>', '', caption)
            data.pop('parse_mode', None)
            if img_bytes:
                telegram_sesion.post(url, data=data, files={'photo': ('radar.png', img_bytes, 'image/png')}, timeout=30)
            else:
                telegram_sesion.post(url, data=data, timeout=15)
    except Exception as e:
        print(f"Error Telegram FOTO ({tipo}): {e}")

def actualizar_cartel(color, texto):
    if not GH_TOKEN or not GIST_ID:
        return
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    texto_limpio = texto.replace('\n', ' ').strip()
    data = {"files": {"estado.txt": {"content": f"{color}|{texto_limpio}"}}}
    try:
        telegram_sesion.patch(url, headers=headers, json=data, timeout=10)
    except Exception as e:
        print(f"Error actualizando Gist: {e}")

# ==========================================
# RECOLECCIÓN DE DATOS Y PROCESAMIENTO
# ==========================================
def obtener_url_radar(memoria):
    try:
        tiempo_actual = int(datetime.now().timestamp())
        url_pagina = f"{URL_RADAR_WEB}?nocache={tiempo_actual}"
        
        res = radar_sesion.get(url_pagina, timeout=15)
        print(f"DEBUG: Status web deroweb: {res.status_code}")
        if res.status_code != 200:
            return None
            
        soup = BeautifulSoup(res.text, 'html.parser')
        for tag in soup.find_all(['img', 'source', 'script']):
            src = tag.get('src') or tag.get('data-src') or tag.string or ''
            match = re.search(r'https?://[^\s<>"]+?RMA2[^\s<>"]+?\.png|[^\s<>"]+?RMA2[^\s<>"]+?\.png', src)
            if match:
                path = match.group(0)
                full_url = path if path.startswith('http') else f"https://meteo.deroweb.com.ar/{path.lstrip('/')}"
                print(f"DEBUG: URL de radar detectada en Deroweb: {full_url}")
                return full_url
                
        match_regex = re.search(r'https?://[^\s<>"]+?RMA2[^\s<>"]+?\.png', res.text)
        if match_regex:
            full_url = match_regex.group(0)
            print(f"DEBUG: URL de radar encontrada por Regex global: {full_url}")
            return full_url
            
        return None
    except Exception as e:
        print(f"Error en obtener_url_radar: {e}")
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
    
    filename = url_imagen_radar.split('/')[-1]
    
    # Lista de fuentes candidatas para descargar la imagen sin sufrir el bloqueo 403 del SMN
    candidatas_urls = [
        url_imagen_radar,  # Original de estaticos.smn.gob.ar
        f"https://meteo.deroweb.com.ar/vmsr/radar/{filename}",
        f"https://meteo.deroweb.com.ar/radar/{filename}",
        f"https://meteo.deroweb.com.ar/{filename}"
    ]
    
    img_res = None
    for u in candidatas_urls:
        try:
            print(f"Intentando descargar imagen desde: {u}")
            res = radar_sesion.get(u, timeout=15)
            print(f" -> Status: {res.status_code}, Length: {len(res.content)}")
            if res.status_code == 200 and len(res.content) > 1000 and b'PNG' in res.content[:10]:
                img_res = res
                print(f" ¡Éxito descargando imagen desde: {u}!")
                break
        except Exception as e:
            continue
            
    if not img_res or img_res.status_code != 200:
        print(f"⚠️ No se pudo descargar la imagen de ninguna de las rutas probadas.")
        return
        
    base_id = f"ECOS_{filename.replace('.png', '')}"
    id_25, id_60, id_100, id_250 = f"{base_id}_25", f"{base_id}_60", f"{base_id}_100", f"{base_id}_250"
    
    try:
        img = Image.open(io.BytesIO(img_res.content)).convert('RGB')
        arr = np.array(img)
        alto, ancho, _ = arr.shape
        
        centro_x, centro_y = (ancho // 2) - 65, (alto // 2) + 45
        radio_max = min(alto // 2, ancho // 2) * 0.95
        
        r_250 = radio_max * (250 / 240)
        r_150 = radio_max * (150 / 240)
        r_60 = radio_max * (60 / 240)
        r_25 = radio_max * (25 / 240)
        
        Y, X = np.ogrid[:alto, :ancho]
        dist_sq = (X - centro_x)**2 + (Y - centro_y)**2
        
        anillo_25 = (dist_sq <= r_25**2)
        anillo_60 = (dist_sq > r_25**2) & (dist_sq <= r_60**2)
        anillo_150 = (dist_sq > r_60**2) & (dist_sq <= r_150**2)
        anillo_250 = (dist_sq > r_150**2) & (dist_sq <= r_250**2)
        
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        
        es_35dbz = (r > 180) & (b < 100)
        es_10dbz = (r > 100)
        
        mensajes = []
        
        if np.sum(es_35dbz & anillo_25) > 30 and id_25 not in memoria:
            mensajes.append("🟣 <b>¡ALERTA CERCANA! ECOS EN ZONA NÚCLEO (de <b>*Florencio Varela*</b>)</b> 🟣\nSe detectan celdas fuertes a menos de <b>25 km</b>.\n<i>(Silenciado x 30m)</i>")
            guardar_memoria(id_25, memoria)
            actualizar_cartel("VIOLETA", "¡PELIGRO EXTREMO! ECOS EN ZONA NÚCLEO A MENOS DE 25 KM.")
            
        if np.sum(es_35dbz & anillo_60) > 15 and id_60 not in memoria:
            mensajes.append("🚨 <b>¡PELIGRO! ECOS FUERTES CERCA</b> 🚨\nSe detectan celdas moderadas/fuertes en el anillo de <b>25 a 60 km</b> de <b>*Florencio Varela*</b>.\n<i>(Silenciado x 30m)</i>")
            guardar_memoria(id_60, memoria)
            actualizar_cartel("AZUL", "ATENCION: ECOS FUERTES CERCA (25 a 60 km).")
            
        if np.sum(es_35dbz & anillo_150) > 5 and id_100 not in memoria:
            mensajes.append("🟡 <b>AVISO: ECOS EN ZONA MEDIA</b> 🟡\nSe detectan celdas moderadas en el anillo de <b>60 a 150 km</b>.\n<i>(Silenciado x 30m)</i>")
            guardar_memoria(id_100, memoria)
            actualizar_cartel("CELESTE", "AVISO RADAR: Ecos detectados a distancia media (60 a 150 km).")
            
        if np.sum(es_10dbz & anillo_250) > 5 and id_250 not in memoria:
            mensajes.append("⚪ <b>DIAGNÓSTICO: ACTIVIDAD PERIFÉRICA DETECTADA</b> ⚪\nEl radar detecta ecos de reflectividad (10+ dBZ) en el margen exterior de <b>150 a 250 km</b>.\n<i>(Silenciado x 30m)</i>")
            guardar_memoria(id_250, memoria)
            actualizar_cartel("BLANCO", "MONITOREO RADAR: Actividad detectada a gran distancia (150+ km).")
            
        if mensajes:
            for m in mensajes: enviar_mensaje(m, tipo="radar")
            enviar_foto(caption="📡 Radar del momento", photo_url=url_imagen_radar, tipo="radar")
            
    except Exception as e:
        print(f"Error procesando matriz de radar: {e}")

def procesar_cap(memoria, url_radar):
    try:
        headers_anticache = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
        res = radar_sesion.get(URL_ALERTAS, headers=headers_anticache, timeout=10)
        
        if res.status_code != 200: return
        
        items = re.findall(r'<item>(.*?)</item>', res.text, re.DOTALL | re.I)
        links_validos = [obtener_link(i).split('/')[-1] for i in items if obtener_link(i)]
            
        id_update = f"SAT_UPDATE_{hashlib.md5(''.join(sorted(links_validos)).encode()).hexdigest()}"
        es_nuevo_boletin = id_update not in memoria
        hay_alerta = False
        
        for item in items:
            url_xml = obtener_link(item)
            if not url_xml: continue
            id_xml = url_xml.split('/')[-1]
            
            if f"VARELA_SI_{id_xml}" in memoria or id_xml in memoria:
                hay_alerta = True
                continue
            if f"VARELA_NO_{id_xml}" in memoria: continue
            
            try:
                xml_raw = radar_sesion.get(url_xml, headers=headers_anticache, timeout=15).text
                xml_raw = re.sub(r'<(/?)[a-zA-Z0-9_]+:([a-zA-Z0-9_]+)', r'<\1\2', xml_raw)
            except Exception:
                continue
            
            if intercepta_varela(xml_raw):
                hay_alerta = True
                guardar_memoria(f"VARELA_SI_{id_xml}", memoria)
                
                info = re.search(r'<info[^>]*>(.*?)</info>', xml_raw, re.DOTALL | re.I)
                info_txt = info.group(1) if info else xml_raw
                
                evento = limpiar_cdata((re.search(r'<event[^>]*>(.*?)</event>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: "FENÓMENO"})).group(1)).upper()
                desc = limpiar_cdata((re.search(r'<description[^>]*>(.*?)</description>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: ""})).group(1))
                sev = limpiar_cdata((re.search(r'<severity[^>]*>(.*?)</severity>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: ""})).group(1)).lower()
                
                dt_emis = parsear_fecha((re.search(r'<sent[^>]*>(.*?)</sent>', xml_raw, re.I) or type('obj', (object,), {'group': lambda x: None})).group(1))
                dt_ini = parsear_fecha((re.search(r'<onset[^>]*>(.*?)</onset>', info_txt, re.I) or re.search(r'<effective[^>]*>(.*?)</effective>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: None})).group(1), sumar_minuto=True)
                dt_fin = parsear_fecha((re.search(r'<expires[^>]*>(.*?)</expires>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: None})).group(1))
                
                if "extreme" in sev: nivel, emoji, riesgo = "rojo", "🔴", "*Riesgo meteorológico alto*"
                elif "severe" in sev: nivel, emoji, riesgo = "naranja", "🟠", "*Riesgo meteorológico moderado*"
                else: nivel, emoji, riesgo = "amarillo", "🟡", "*Riesgo meteorológico leve*"
                
                desc_texto = re.sub(r'<br\s*/?>', '\n', desc, flags=re.I)
                msg = (f"⚠️ El SMN actualizó su sistema a las {dt_emis[1]} hs.\n\n"
                       f"‼️⚠️ Alerta por \"{tg_safe(evento)}\" desde {dt_ini[0]} {dt_ini[1]}hs hasta {dt_fin[0]} {dt_fin[1]}hs.- nivel {nivel} para <b>*Florencio Varela*</b>.\n\n"
                       f"{tg_safe(desc_texto)}\n\n{emoji} {riesgo}\n\n🔗 <b>ID:</b> <code>{tg_safe(id_xml)}</code>")
                
                enviar_mensaje(msg, tipo="alerta")
                actualizar_cartel(nivel.upper(), f"ALERTA NIVEL {nivel.upper()} POR {evento}. Válido hasta {dt_fin[1]} hs.")
                if url_radar: enviar_foto("📡 Radar del momento", photo_url=url_radar, tipo="alerta")
            else:
                guardar_memoria(f"VARELA_NO_{id_xml}", memoria)

        if es_nuevo_boletin:
            if not hay_alerta:
                actualizar_cartel("VERDE", "Clima tranquilo. Sin alertas vigentes.")
            guardar_memoria(id_update, memoria)
            
    except Exception as e:
        print(f"Error en procesar_cap: {e}")

def procesar_acp(memoria, url_radar):
    try:
        headers_anticache = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
        res = radar_sesion.get(URL_ACP, headers=headers_anticache, timeout=10)
        if res.status_code != 200: return
            
        items = re.findall(r'<item>(.*?)</item>', res.text, re.DOTALL | re.I)
        for item in items:
            url_xml = obtener_link(item)
            if not url_xml: continue
                
            id_xml = url_xml.split('/')[-1]
            if f"ACP_SI_{id_xml}" in memoria or id_xml in memoria: continue
            if f"ACP_NO_{id_xml}" in memoria: continue
            
            titulo_match = re.search(r'<title>(.*?)</title>', item, re.I | re.DOTALL)
            fenomeno_full = limpiar_cdata(titulo_match.group(1)) if titulo_match else "TORMENTAS FUERTES"
            
            try:
                xml_raw = radar_sesion.get(url_xml, headers=headers_anticache, timeout=15).text
                xml_raw = re.sub(r'<(/?)[a-zA-Z0-9_]+:([a-zA-Z0-9_]+)', r'<\1\2', xml_raw)
            except Exception:
                continue
            
            if intercepta_varela(xml_raw):
                guardar_memoria(f"ACP_SI_{id_xml}", memoria)
                info = re.search(r'<info[^>]*>(.*?)</info>', xml_raw, re.DOTALL | re.I)
                info_txt = info.group(1) if info else xml_raw
                
                area_match = re.search(r'<areaDesc[^>]*>(.*?)</areaDesc>', info_txt, re.I)
                if not area_match:
                    area_match = re.search(r'<description[^>]*>(.*?)</description>', info_txt, re.I)
                
                zonas_raw = limpiar_cdata(area_match.group(1)) if area_match else "Ver detalle en SMN"
                zonas_raw = re.sub(r'(?i)Afectando parcialmente los siguientes [^:]+:\s*', '', zonas_raw).strip()
                zonas_raw = zonas_raw.title()
                
                dt_fin = parsear_fecha((re.search(r'<expires[^>]*>(.*?)</expires>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: None})).group(1))
                
                evento_safe = tg_safe(fenomeno_full.upper())
                zonas_safe = tg_safe(zonas_raw)
                zonas_formateadas = re.sub(r'(?i)(florencio varela)', r'<b>*\1*</b>', zonas_safe)
                
                msg = (f"‼️ <b>*Aviso a corto plazo del SMN por {evento_safe}.* que afecta a *Florencio Varela*</b>\n\n"
                       f"📍 Zonas:\n"
                       f"{zonas_formateadas}\n\n"
                       f"⏳ Validez hasta las {dt_fin[1]}hs del {dt_fin[0]}.")
                
                enviar_mensaje(msg, tipo="alerta")
                actualizar_cartel("NARANJA", f"AVISO A CORTO PLAZO: {evento_safe}. Válido hasta {dt_fin[1]}hs.")
                
                if url_radar: enviar_foto("📡 Radar (ACP)", photo_url=url_radar, tipo="alerta")
            else:
                guardar_memoria(f"ACP_NO_{id_xml}", memoria)
                
    except Exception as e:
        print(f"Error procesando procesar_acp: {e}")

def procesar_shn(memoria):
    try:
        tiempo_actual = int(datetime.now().timestamp())
        try:
            res = radar_sesion.get(f"{URL_SHN_XML}?nocache={tiempo_actual}", timeout=10)
        except Exception:
            res = radar_sesion.get(f"{URL_SHN_XML.replace('https://', 'http://')}?nocache={tiempo_actual}", timeout=10)
        
        if res.status_code != 200: return
        
        res.encoding = 'utf-8'
        xml_raw = re.sub(r'<(/?)[a-zA-Z0-9_]+:([a-zA-Z0-9_]+)', r'<\1\2', res.text)
        alertas = re.findall(r'<alert[^>]*>(.*?)</alert>', xml_raw, re.DOTALL | re.I)
        if not alertas and "<description" in xml_raw.lower(): alertas = [xml_raw]
            
        for alerta in alertas:
            desc = limpiar_cdata((re.search(r'<description[^>]*>(.*?)</description>', alerta, re.I | re.DOTALL) or type('obj', (object,), {'group': lambda x: ""})).group(1))
            if len(desc) < 5: continue
            
            id_alerta = limpiar_cdata((re.search(r'<identifier[^>]*>(.*?)</identifier>', alerta, re.I | re.DOTALL) or type('obj', (object,), {'group': lambda x: "SHN_"+hashlib.md5(desc.encode()).hexdigest()[:12]})).group(1))
            if id_alerta in memoria: continue
            
            head = limpiar_cdata((re.search(r'<headline[^>]*>(.*?)</headline>', alerta, re.I | re.DOTALL) or type('obj', (object,), {'group': lambda x: "Aviso Hidrológico"})).group(1))
            
            desc_texto = re.sub(r'<br\s*/?>', '\n', desc, flags=re.I)
            desc_texto = tg_safe(desc_texto).replace('&nbsp;', ' ')
            desc_texto = '\n\n'.join([linea.string.strip() if hasattr(linea, 'string') else linea.strip() for linea in desc_texto.split('\n') if linea.strip()])
            
            enviar_mensaje(f"⚠️ <b>Aviso de crecida del SHN</b>\n\n‼️ <b>{tg_safe(head.upper())}</b>\n\n{desc_texto}", tipo="alerta")
            guardar_memoria(id_alerta, memoria)
            
    except Exception as e:
        print(f"Error en procesar_shn: {e}")

# ==========================================
# PUNTO DE ENTRADA MAIN
# ==========================================
if __name__ == '__main__':
    print("--- INICIANDO SCRIPT METEOROLÓGICO ---")
    memoria_actual = cargar_memoria()
    print(f"Memoria cargada. Registros: {len(memoria_actual)}")
    
    radar_url = obtener_url_radar(memoria_actual)
    print(f"URL del radar detectada: {radar_url}")
    
    enviar_mensaje("🟢 <b>TEST DE ARRANQUE:</b> El bot se conectó y envió este mensaje correctamente.", tipo="alerta")
    
    print("Procesando Alertas CAP (SMN)...")
    procesar_cap(memoria_actual, radar_url)
    
    print("Procesando Avisos a Corto Plazo (ACP)...")
    procesar_acp(memoria_actual, radar_url)
    
    print("Procesando Avisos Hidrológicos (SHN)...")
    procesar_shn(memoria_actual)
    
    print("Procesando Escaneo de Radar...")
    procesar_radar(memoria_actual, radar_url)
    
    print("--- EJECUCIÓN FINALIZADA ---")
