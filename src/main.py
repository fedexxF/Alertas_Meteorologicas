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
    if os.path.exists(ARCHIVO_MEMORIA):
        with open(ARCHIVO_MEMORIA, 'r') as f:
            return set(f.read().splitlines())
    return set()

def guardar_memoria(id_alerta, memoria_set):
    memoria_set.add(id_alerta)
    os.makedirs(os.path.dirname(ARCHIVO_MEMORIA), exist_ok=True)
    with open(ARCHIVO_MEMORIA, 'a') as f:
        f.write(f"{id_alerta}\n")

def limpiar_cdata(texto):
    texto = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', texto or "", flags=re.DOTALL)
    return html.unescape(texto).strip()

def tg_safe(texto):
    texto = re.sub(r'<[^>]+>', '', texto)
    return texto.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def parsear_fecha(fecha_iso, es_fin=False):
    try:
        dt = datetime.strptime(limpiar_cdata(fecha_iso)[:19], "%Y-%m-%dT%H:%M:%S") - timedelta(hours=3)
        if es_fin:
            dt = (dt + timedelta(minutes=1)).replace(second=0)
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
# RECOLECCIÓN DE DATOS Y PROCESAMIENTO
# ==========================================
def obtener_url_radar(memoria):
    try:
        res = sesion.get(URL_RADAR_WEB, timeout=15)
        img_tag = BeautifulSoup(res.text, 'html.parser').find('img', src=re.compile(r'radar|ezeiza', re.I))
        if img_tag and 'src' in img_tag.attrs:
            url = img_tag['src']
            return url if url.startswith('http') else f"https://www.climasurgba.com.ar{url}"
    except Exception as e:
        err_id = f"ERR_URLRADAR_{hashlib.md5(str(e).encode()).hexdigest()[:8]}"
        if err_id not in memoria:
            enviar_mensaje(f"⚠️ <b>ALERTA DE SISTEMA:</b> No se pudo conectar a la web del Radar Ezeiza. Error: <code>{tg_safe(str(e))}</code>", tipo="radar")
            guardar_memoria(err_id, memoria)
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
        img_res = sesion.get(url_imagen_radar, timeout=15)
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
            mensajes.append("🟣 <b>¡ALERTA CERCANA! ECOS EN ZONA NÚCLEO (de <b>*Florencio Varela*</b>)</b> 🟣\nSe detectan celdas fuertes (>35 dBZ) a menos de <b>25 km</b>.\n<i>(Silenciado x 30m)</i>")
            guardar_memoria(id_25, memoria)
            
        if np.sum(es_35dbz & anillo_60) > 30 and id_60 not in memoria:
            mensajes.append("🚨 <b>¡PELIGRO! ECOS FUERTES/SEVEROS CERCA</b> 🚨\nSe detectan celdas fuertes (>35 dBZ) en el anillo de <b>25 a 60 km</b> de <b>*Florencio Varela*</b>.\n<i>(Silenciado x 30m)</i>")
            guardar_memoria(id_60, memoria)
            
        if np.sum(es_35dbz & anillo_100) > 30 and id_100 not in memoria:
            mensajes.append("🟡 <b>AVISO: ECOS EN APROXIMACIÓN</b> 🟡\nSe detectan celdas fuertes (>35 dBZ) en el anillo de <b>60 a 100 km</b> de <b>*Florencio Varela*</b>.\n<i>(Silenciado x 30m)</i>")
            guardar_memoria(id_100, memoria)
            
        if mensajes:
            for m in mensajes: enviar_mensaje(m, tipo="radar")
            enviar_foto(caption="📡 Radar del momento", photo_url=url_imagen_radar, tipo="radar")
            
    except Exception as e:
        err_id = f"ERR_RADAR_{hashlib.md5(str(e).encode()).hexdigest()[:8]}"
        if err_id not in memoria:
            enviar_mensaje(f"⚠️ <b>ALERTA DE SISTEMA:</b> Fallo escaneando la imagen del Radar Ezeiza. Error: <code>{tg_safe(str(e))}</code>", tipo="radar")
            guardar_memoria(err_id, memoria)

def procesar_cap(memoria, url_radar):
    try:
        res = sesion.get(URL_ALERTAS, timeout=15)
        if res.status_code != 200: return
        
        items = re.findall(r'<item>(.*?)</item>', res.text, re.DOTALL | re.I)
        
        links_validos = []
        for i in items:
            lnk = obtener_link(i)
            if lnk: links_validos.append(lnk.split('/')[-1])
            
        hash_feed = hashlib.md5("".join(sorted(links_validos)).encode()).hexdigest()
        id_update = f"SAT_UPDATE_{hash_feed}"
        
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
                xml_raw = sesion.get(url_xml, timeout=15).text
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
                
                if "extreme" in sev: nivel, emoji, riesgo = "rojo", "🔴", "Riesgo meteorológico alto"
                elif "severe" in sev: nivel, emoji, riesgo = "naranja", "🟠", "Riesgo meteorológico moderado"
                else: nivel, emoji, riesgo = "amarillo", "🟡", "Riesgo meteorológico leve"
                
                desc_texto = re.sub(r'<br\s*/?>', '\n', desc, flags=re.I)
                
                msg = (f"⚠️ El SMN actualizó su sistema a las {dt_emis[1]} hs.\n\n"
                       f"‼️⚠️ Alerta por \"{tg_safe(evento)}\" desde {dt_ini[0]} {dt_ini[1]}hs hasta {dt_fin[0]} {dt_fin[1]}hs.- nivel {nivel} para <b>*Florencio Varela*</b>.\n\n"
                       f"{tg_safe(desc_texto)}\n\n{emoji} {riesgo}\n\n🔗 <b>ID:</b> <code>{tg_safe(id_xml)}</code>")
                
                enviar_mensaje(msg, tipo="alerta")
                if url_radar: enviar_foto("📡 Radar del momento", photo_url=url_radar, tipo="alerta")
            else:
                guardar_memoria(f"VARELA_NO_{id_xml}", memoria)

        if es_nuevo_boletin:
            if not hay_alerta:
                enviar_mensaje(f"✅ <b>SISTEMA ACTUALIZADO</b> ✅\n\nEl SMN actualizó el mapa nacional.\n🔹 <b>*Florencio Varela*</b> NO se encuentra bajo alertas oficiales.", tipo="alerta")
            guardar_memoria(id_update, memoria)
            
    except Exception as e:
        err_id = f"ERR_CAP_{hashlib.md5(str(e).encode()).hexdigest()[:8]}"
        if err_id not in memoria:
            enviar_mensaje(f"⚠️ <b>ALERTA DE SISTEMA:</b> Fallo conectando al XML de Alertas del SMN. Error: <code>{tg_safe(str(e))}</code>", tipo="alerta")
            guardar_memoria(err_id, memoria)

def procesar_acp(memoria, url_radar):
    try:
        res = sesion.get(URL_ACP, timeout=15)
        if res.status_code != 200: return
        for item in re.findall(r'<item>(.*?)</item>', res.text, re.I | re.DOTALL):
            if intercepta_varela(item):
                titulo = limpiar_cdata((re.search(r'<title>(.*?)</title>', item, re.I | re.DOTALL) or type('obj', (object,), {'group': lambda x: "ACP_DESC"})).group(1))
                id_acp = f"ACP_{titulo.replace(' ', '_')}"
                if id_acp in memoria: continue
                
                fenomeno = (re.search(r'por ocurrencia de\s*([^<]+)</b>', item, re.I) or type('obj', (object,), {'group': lambda x: "TORMENTAS"})).group(1).strip()
                zonas_matches = re.findall(r'<p><b>([A-ZÁÉÍÓÚÑ\s]+):</b>\s*(.*?)</p>', item, re.I | re.DOTALL)
                zonas_brutas = " - ".join([f"{p.strip()}: {d.strip()}" for p, d in zonas_matches]) if zonas_matches else "Ver detalle en SMN"
                
                zonas_texto = re.sub(r'<br\s*/?>', '\n', zonas_brutas, flags=re.I)
                
                validez_str = "Validez hasta 2 horas desde su emisión"
                f_match = re.search(r'(\d{2}[-/]\d{2}[-/]\d{2,4}).*?(\d{1,2}:\d{2})', titulo, re.I)
                if f_match:
                    try:
                        fecha_raw = f_match.group(1).replace('/', '-')
                        hora_raw = f_match.group(2)
                        anio_largo = "%Y" if len(fecha_raw.split('-')[-1]) == 4 else "%y"
                        dt_emision = datetime.strptime(f"{fecha_raw} {hora_raw}", f"%d-%m-{anio_largo} %H:%M")
                        dt_vence = dt_emision + timedelta(hours=2)
                        
                        dias_semana = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"]
                        dia_txt = dias_semana[dt_vence.weekday()]
                        validez_str = f"Validez hasta las {dt_vence.strftime('%H:%M')}hs del {dia_txt} {dt_vence.strftime('%d/%m')}"
                    except Exception: pass
                
                msg = (f"‼️ AVISO A CORTO PLAZO POR \"{tg_safe(fenomeno)}\" que afecta a <b>*Florencio Varela*</b>.\n\n"
                       f"📍 <b>Zonas:</b>\n{tg_safe(zonas_texto)}\n\n"
                       f"⏳ {tg_safe(validez_str)}.")
                
                enviar_mensaje(msg, tipo="alerta")
                if url_radar: enviar_foto("📡 Radar (ACP)", photo_url=url_radar, tipo="alerta")
                guardar_memoria(id_acp, memoria)
    except Exception as e:
        err_id = f"ERR_ACP_{hashlib.md5(str(e).encode()).hexdigest()[:8]}"
        if err_id not in memoria:
            enviar_mensaje(f"⚠️ <b>ALERTA DE SISTEMA:</b> Fallo conectando a los ACP. Error: <code>{tg_safe(str(e))}</code>", tipo="alerta")
            guardar_memoria(err_id, memoria)

def procesar_shn(memoria):
    try:
        try:
            res = sesion.get(URL_SHN_XML, timeout=25)
        except Exception:
            http_url = URL_SHN_XML.replace("https://", "http://")
            res = sesion.get(http_url, timeout=25)
            
        if res.status_code != 200: return
        
        # FORZADO DE DICCIONARIO: Arregla las letras rotas (RÃO -> RÍO)
        res.encoding = 'utf-8'
        
        xml_raw = re.sub(r'<(/?)[a-zA-Z0-9_]+:([a-zA-Z0-9_]+)', r'<\1\2', res.text)
        
        alertas = re.findall(r'<alert[^>]*>(.*?)</alert>', xml_raw, re.DOTALL | re.I)
        if not alertas and "<description" in xml_raw.lower():
            alertas = [xml_raw]
            
        for alerta in alertas:
            desc_match = re.search(r'<description[^>]*>(.*?)</description>', alerta, re.I | re.DOTALL)
            desc = limpiar_cdata(desc_match.group(1)) if desc_match else ""
            if len(desc) < 5: continue
            
            id_match = re.search(r'<identifier[^>]*>(.*?)</identifier>', alerta, re.I | re.DOTALL)
            id_alerta = limpiar_cdata(id_match.group(1)) if id_match else None
            if not id_alerta:
                id_alerta = "SHN_" + hashlib.md5(desc.encode()).hexdigest()[:12]
                
            if id_alerta in memoria: continue
            
            head_match = re.search(r'<headline[^>]*>(.*?)</headline>', alerta, re.I | re.DOTALL)
            head = limpiar_cdata(head_match.group(1)) if head_match else "Aviso Hidrológico"
            
            # 1. Transformación inicial de los saltos de línea web y protección HTML
            desc_texto = re.sub(r'<br\s*/?>', '\n', desc, flags=re.I)
            desc_texto = tg_safe(desc_texto)
            desc_texto = re.sub(r'&nbsp;', ' ', desc_texto, flags=re.I)
            
            # 2. Compactador de espacios vacíos excesivos
            lineas = [linea.strip() for linea in desc_texto.split('\n') if linea.strip()]
            desc_texto = '\n\n'.join(lineas)
            
            # 3. Búsqueda y destacado inteligente de la "Fecha de Emisión"
            desc_texto = re.sub(r'^(\d{2}\s+DE\s+[a-zA-Z]+\s+DE\s+\d{4}.*)$', r'<b>Fecha de Emisión:</b> \1', desc_texto, flags=re.I | re.MULTILINE)
            
            enviar_mensaje(
                f"⚠️ <b>Aviso de crecida del Río de La Plata emitido por el SHN</b>\n\n"
                f"‼️ <b>{tg_safe(head.upper())}</b>\n\n"
                f"{desc_texto}", 
                tipo="alerta"
            )
            guardar_memoria(id_alerta, memoria)
            
    except Exception as e:
        err_id = f"ERR_SHN_{hashlib.md5(str(e).encode()).hexdigest()[:8]}"
        if err_id not in memoria:
            enviar_mensaje(f"⚠️ <b>ALERTA DE SISTEMA:</b> Fallo conectando a las alertas hidrológicas del SHN. Error: <code>{tg_safe(str(e))}</code>", tipo="alerta")
            guardar_memoria(err_id, memoria)

# ==========================================
# PUNTO DE ENTRADA MAIN
# ==========================================
if __name__ == '__main__':
    memoria_actual = cargar_memoria()
    radar_url = obtener_url_radar(memoria_actual)
    
    procesar_cap(memoria_actual, radar_url)
    procesar_acp(memoria_actual, radar_url)
    procesar_shn(memoria_actual)
    procesar_radar(memoria_actual, radar_url)
