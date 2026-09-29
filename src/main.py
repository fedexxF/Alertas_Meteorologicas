import os
import io
import re
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

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

URL_ACP = 'https://ssl.smn.gob.ar/feeds/avisocorto_GeoRSS.xml'
URL_ALERTAS = 'https://ssl.smn.gob.ar/feeds/CAP/rss_alertaCAP_nuevo_2026.xml'
URL_SHN_XML = 'https://www.hidro.gob.ar/cap/CapRP_xml.asp'
URL_RADAR_WEB = "https://www.climasurgba.com.ar/radar/ezeiza"

NOMBRE_LOCALIDAD = "Florencio Varela"
PUNTO_INTERES = Point(-58.27, -34.79)
AREA_INTERES = PUNTO_INTERES.buffer(0.036) 

# --- NUEVA RUTA PARA LA ESTRUCTURA DEL REPOSITORIO ---
ARCHIVO_MEMORIA = "data/memoria_bot.txt"

# Sesión persistente para reciclar conexiones SSL
sesion = requests.Session()
sesion.verify = False
sesion.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0'})

# ==========================================
# MÓDULOS DE MEMORIA
# ==========================================
def cargar_memoria():
    if os.path.exists(ARCHIVO_MEMORIA):
        with open(ARCHIVO_MEMORIA, 'r') as f:
            return set(f.read().splitlines())
    return set()

def guardar_memoria(id_alerta, memoria_set):
    memoria_set.add(id_alerta)
    # Crea el directorio si no existe (para evitar errores en la nueva estructura)
    os.makedirs(os.path.dirname(ARCHIVO_MEMORIA), exist_ok=True)
    with open(ARCHIVO_MEMORIA, 'a') as f:
        f.write(f"{id_alerta}\n")

# ==========================================
# MÓDULOS DE TELEGRAM (DRY)
# ==========================================
def enviar_mensaje(mensaje):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        sesion.post(url, data={'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}, timeout=10)
    except Exception as e:
        print(f"Error Telegram MSG: {e}")

def enviar_foto(caption, photo_url=None):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        data = {'chat_id': CHAT_ID, 'caption': caption, 'photo': photo_url}
        sesion.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"Error Telegram FOTO: {e}")

# ==========================================
# MÓDULOS HELPERS (UTILERÍA)
# ==========================================
def limpiar_cdata(texto):
    return re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', texto or "", flags=re.DOTALL).strip()

def parsear_fecha(fecha_iso, es_fin=False):
    try:
        dt = datetime.strptime(limpiar_cdata(fecha_iso)[:19], "%Y-%m-%dT%H:%M:%S") - timedelta(hours=3)
        if es_fin:
            dt = (dt + timedelta(minutes=1)).replace(second=0)
        dias = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"]
        return f"{dias[dt.weekday()]} {dt.strftime('%d/%m')}", dt.strftime('%H:%M')
    except:
        return "N/A", "XX:XX"

def obtener_url_radar():
    """Descarga la web una sola vez por ejecución para ahorrar red."""
    try:
        res = sesion.get(URL_RADAR_WEB, timeout=10)
        img_tag = BeautifulSoup(res.text, 'html.parser').find('img', src=re.compile(r'radar|ezeiza', re.I))
        if img_tag and 'src' in img_tag.attrs:
            url = img_tag['src']
            return url if url.startswith('http') else f"https://www.climasurgba.com.ar{url}"
    except Exception as e:
        print(f"Error obteniendo URL radar: {e}")
    return None

def intercepta_varela(xml_raw):
    """Lógica unificada para detectar si un bloque XML intersecta la ciudad."""
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

# ==========================================
# MÓDULO PRINCIPAL: RADAR
# ==========================================
def procesar_radar(memoria, url_imagen_radar):
    if not url_imagen_radar: return
    
    fecha_actual = datetime.now() - timedelta(hours=3)
    bloque_30 = "00" if fecha_actual.minute < 30 else "30"
    base_id = f"ECOS_{fecha_actual.strftime('%Y-%m-%d_%H')}_{bloque_30}"
    
    id_25, id_60, id_100 = f"{base_id}_25", f"{base_id}_60", f"{base_id}_100"
    
    try:
        img_res = sesion.get(url_imagen_radar, timeout=10)
        img = Image.open(io.BytesIO(img_res.content)).convert('RGB')
        arr = np.array(img)
        alto, ancho, _ = arr.shape
        
        # Calibración
        centro_x, centro_y = (ancho // 2) - 65, (alto // 2) + 45
        radio_max = min(alto // 2, ancho // 2) * 0.95
        
        r_100 = radio_max * (100 / 240)
        r_60 = radio_max * (60 / 240)
        r_25 = radio_max * (25 / 240)
        
        Y, X = np.ogrid[:alto, :ancho]
        
        # OPTIMIZACIÓN CPU: Distancia al cuadrado (Evita np.sqrt)
        dist_sq = (X - centro_x)**2 + (Y - centro_y)**2
        
        # Máscaras matemáticas invisibles
        F_PEND, F_OFF = 0.1763, 0.5824
        mask_chord_60 = X <= (centro_x + r_60 * F_OFF + (Y - centro_y) * F_PEND)
        mask_chord_100 = X <= (centro_x + r_100 * F_OFF + (Y - centro_y) * F_PEND)
        
        anillo_25 = (dist_sq <= r_25**2)
        anillo_60 = (dist_sq > r_25**2) & (dist_sq <= r_60**2) & mask_chord_60
        anillo_100 = (dist_sq > r_60**2) & (dist_sq <= r_100**2) & mask_chord_100
        
        # Umbrales
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        es_30dbz = (r > 180) & ((g < 220) | (b < 100))
        es_35dbz = (r > 180) & (g < 160)
        
        mensajes = []
        if np.sum(es_35dbz & anillo_25) > 30 and id_25 not in memoria:
            mensajes.append("🟣 <b>¡ALERTA CERCANA! ECOS EN ZONA NÚCLEO</b> 🟣\nSe detectan celdas fuertes (>35 dBZ) a menos de <b>25 km</b>.\n<i>(Silenciado x 30m)</i>")
            guardar_memoria(id_25, memoria)
            
        if np.sum(es_35dbz & anillo_60) > 30 and id_60 not in memoria:
            mensajes.append("🚨 <b>¡PELIGRO! ECOS FUERTES/SEVEROS CERCA</b> 🚨\nSe detectan celdas fuertes (>35 dBZ) en el anillo de <b>25 a 60 km</b>.\n<i>(Silenciado x 30m)</i>")
            guardar_memoria(id_60, memoria)
            
        if np.sum(es_30dbz & anillo_100) > 30 and id_100 not in memoria:
            mensajes.append("🟡 <b>AVISO: ECOS EN APROXIMACIÓN</b> 🟡\nSe detectan precipitaciones moderadas (>30 dBZ) en el anillo de <b>60 a 100 km</b>.\n<i>(Silenciado x 30m)</i>")
            guardar_memoria(id_100, memoria)
            
        if mensajes:
            for m in mensajes: enviar_mensaje(m)
            # Envia la foto web original y limpia directamente al chat
            enviar_foto(caption="📡 Radar del momento", photo_url=url_imagen_radar)
            
    except Exception as e:
        print(f"Error procesando Radar: {e}")

# ==========================================
# MÓDULOS DE ALERTAS SMN/SHN
# ==========================================
def procesar_cap(memoria, url_radar):
    try:
        res = sesion.get(URL_ALERTAS, timeout=10)
        if res.status_code != 200: return
        
        items = re.findall(r'<item>(.*?)</item>', res.text, re.DOTALL | re.I)
        hash_feed = hashlib.md5("".join(sorted([re.search(r'<link[^>]*href=["\'](.*?)["\']', i, re.I).group(1) for i in items if re.search(r'<link[^>]*href=["\'](.*?)["\']', i, re.I)])).encode()).hexdigest()
        id_update = f"SAT_UPDATE_{hash_feed}"
        
        es_nuevo_boletin = id_update not in memoria
        hay_alerta = False
        
        for item in items:
            link = re.search(r'<link[^>]*href=["\'](.*?)["\']', item, re.I)
            if not link: continue
            
            url_xml = link.group(1).strip()
            id_xml = url_xml.split('/')[-1]
            
            if f"VARELA_SI_{id_xml}" in memoria or id_xml in memoria:
                hay_alerta = True
                continue
            if f"VARELA_NO_{id_xml}" in memoria: continue
            
            try:
                xml_raw = sesion.get(url_xml, timeout=10).text
                xml_raw = re.sub(r'<(/?)[a-zA-Z0-9_]+:([a-zA-Z0-9_]+)', r'<\1\2', xml_raw)
            except: continue
            
            if intercepta_varela(xml_raw):
                hay_alerta = True
                guardar_memoria(f"VARELA_SI_{id_xml}", memoria)
                
                info = re.search(r'<info[^>]*>(.*?)</info>', xml_raw, re.DOTALL | re.I)
                info_txt = info.group(1) if info else xml_raw
                
                evento = limpiar_cdata((re.search(r'<event[^>]*>(.*?)</event>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: "FENÓMENO"})).group(1)).upper()
                desc = limpiar_cdata((re.search(r'<description[^>]*>(.*?)</description>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: ""})).group(1))
                sev = limpiar_cdata((re.search(r'<severity[^>]*>(.*?)</severity>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: ""})).group(1)).lower()
                
                dt_emis = parsear_fecha((re.search(r'<sent[^>]*>(.*?)</sent>', xml_raw, re.I) or type('obj', (object,), {'group': lambda x: None})).group(1))
                dt_ini = parsear_fecha((re.search(r'<onset[^>]*>(.*?)</onset>', info_txt, re.I) or re.search(r'<effective[^>]*>(.*?)</effective>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: None})).group(1))
                dt_fin = parsear_fecha((re.search(r'<expires[^>]*>(.*?)</expires>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: None})).group(1), es_fin=True)
                
                nivel, emoji = ("rojo", "🔴") if "extreme" in sev else ("naranja", "🟠") if "severe" in sev else ("amarillo", "🟡")
                
                msg = (f"⚠️ El SMN actualizó su sistema a las {dt_emis[1]} hs.\n\n"
                       f"‼️⚠️ Alerta por \"{evento}\" desde {dt_ini[0]} {dt_ini[1]}hs hasta {dt_fin[0]} {dt_fin[1]}hs.- nivel {nivel}\n\n"
                       f"{desc}\n\n{emoji}\n\n🔗 <b>ID:</b> <code>{id_xml}</code>")
                
                enviar_mensaje(msg)
                if url_radar: enviar_foto("📡 Radar del momento", photo_url=url_radar)
            else:
                guardar_memoria(f"VARELA_NO_{id_xml}", memoria)

        if es_nuevo_boletin:
            if not hay_alerta:
                enviar_mensaje(f"✅ <b>SISTEMA ACTUALIZADO</b> ✅\n\nEl SMN actualizó el mapa nacional.\n🔹 <b>{NOMBRE_LOCALIDAD}</b> NO se encuentra bajo alertas oficiales.")
            guardar_memoria(id_update, memoria)
            
    except Exception as e: print(f"Error CAP: {e}")

def procesar_acp(memoria, url_radar):
    try:
        res = sesion.get(URL_ACP, timeout=10)
        if res.status_code != 200: return
        for item in re.findall(r'<item>(.*?)</item>', res.text, re.I | re.DOTALL):
            if intercepta_varela(item):
                titulo = limpiar_cdata((re.search(r'<title>(.*?)</title>', item, re.I | re.DOTALL) or type('obj', (object,), {'group': lambda x: "ACP_DESC"})).group(1))
                id_acp = f"ACP_{titulo.replace(' ', '_')}"
                if id_acp in memoria: continue
                
                fenomeno = (re.search(r'por ocurrencia de\s*([^<]+)</b>', item, re.I) or type('obj', (object,), {'group': lambda x: "TORMENTAS"})).group(1).strip()
                zonas = " - ".join([f"{p.strip()}: {d.strip()}" for p, d in re.findall(r'<p><b>([A-ZÁÉÍÓÚÑ\s]+):</b>\s*(.*?)</p>', item, re.I)])
                
                msg = f"‼️ AVISO A CORTO PLAZO POR \"{fenomeno}\".\n\n📍 <b>Zonas:</b> {zonas}\n⏳ <b>Validez:</b> 2 hs desde emisión."
                enviar_mensaje(msg)
                if url_radar: enviar_foto("📡 Radar (ACP)", photo_url=url_radar)
                guardar_memoria(id_acp, memoria)
    except Exception as e: print(f"Error ACP: {e}")

def procesar_shn(memoria):
    try:
        res = sesion.get(URL_SHN_XML, timeout=10)
        if res.status_code != 200: return
        for alerta in re.findall(r'<alert[^>]*>(.*?)</alert>', res.text, re.DOTALL | re.I):
            desc = limpiar_cdata((re.search(r'<description[^>]*>(.*?)</description>', alerta, re.I | re.DOTALL) or type('obj', (object,), {'group': lambda x: ""})).group(1))
            if len(desc) < 5: continue
            
            id_alerta = limpiar_cdata((re.search(r'<identifier[^>]*>(.*?)</identifier>', alerta, re.I | re.DOTALL) or type('obj', (object,), {'group': lambda x: "SHN_"+hashlib.md5(desc.encode()).hexdigest()[:12]})).group(1))
            if id_alerta in memoria: continue
            
            head = limpiar_cdata((re.search(r'<headline[^>]*>(.*?)</headline>', alerta, re.I | re.DOTALL) or type('obj', (object,), {'group': lambda x: "Aviso Hidrológico"})).group(1))
            enviar_mensaje(f"🌊 <b>¡AVISO HIDROLÓGICO SHN!</b>\n\n‼️ <b>{head.upper()}</b>\n\n{desc}")
            guardar_memoria(id_alerta, memoria)
    except Exception as e: print(f"Error SHN: {e}")

# ==========================================
# PUNTO DE ENTRADA MAIN
# ==========================================
if __name__ == '__main__':
    memoria_actual = cargar_memoria()
    radar_url = obtener_url_radar() # 1 sola descarga de red centralizada
    
    procesar_cap(memoria_actual, radar_url)
    procesar_acp(memoria_actual, radar_url)
    procesar_shn(memoria_actual)
    procesar_radar(memoria_actual, radar_url)
          
