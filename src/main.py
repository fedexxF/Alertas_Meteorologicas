import os
import io
import re
import html
import hashlib
import urllib3
import requests
import numpy as np
from PIL import Image, ImageDraw
from datetime import datetime, timedelta
from shapely.geometry import Point, Polygon
from bs4 import BeautifulSoup

# --- CONFIGURACIÓN GLOBAL Y CONSTANTES ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Visualización de zonas de radar
DIBUJAR_ANILLOS = True        # Superpone las 3 zonas y el centro de Varela sobre la imagen
ENVIAR_PREVIEW_RADAR = True   # Envía la imagen dibujada en cada corrida (cambiar a False tras verificar)

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

# Fuentes alternativas de Radar Ezeiza (RMA2) sin bloqueo de Cloudflare
URL_RADAR_CLIMASUR = "https://www.climasurgba.com.ar/radar/ezeiza"
URL_RADAR_METEOGBA = "https://meteogba.com.ar/radar"

NOMBRE_LOCALIDAD = "Varela"
PUNTO_INTERES = Point(-58.27, -34.79)
AREA_INTERES = PUNTO_INTERES.buffer(0.054)  # Equivalente exacto a 6 km de radio

ARCHIVO_MEMORIA = "data/memoria_bot.txt"

# Sesión HTTP con cabecera de navegador estándar
sesion = requests.Session()
sesion.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8"
})

# Sesión limpia estándar para Telegram
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

def enviar_foto(caption, photo_url=None, img_bytes=None, tipo="alerta"):
    token = TOKEN_ALERTAS if tipo == "alerta" else TOKEN_RADAR
    chat_id = CHAT_ID_ALERTAS if tipo == "alerta" else CHAT_ID_RADAR
    if not token or not chat_id: return
    
    try:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        
        if not img_bytes and photo_url:
            try:
                r_img = sesion.get(photo_url, timeout=20)
                if r_img.status_code == 200 and 'image' in r_img.headers.get('content-type', '').lower():
                    img_bytes = r_img.content
            except Exception:
                pass
                
        if img_bytes:
            ext = "png" if img_bytes[:8] == b'\x89PNG\r\n\x1a\n' else "webp"
            mime = f"image/{ext}"
            files = {'photo': (f'radar.{ext}', img_bytes, mime)}
            data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}
            res = telegram_sesion.post(url, data=data, files=files, timeout=30)
        elif photo_url:
            data = {'chat_id': chat_id, 'caption': caption, 'photo': photo_url, 'parse_mode': 'HTML'}
            res = telegram_sesion.post(url, data=data, timeout=15)
        else:
            return
            
        if res.status_code != 200:
            data['caption'] = re.sub(r'<[^>]+>', '', caption)
            data.pop('parse_mode', None)
            if img_bytes:
                ext = "png" if img_bytes[:8] == b'\x89PNG\r\n\x1a\n' else "webp"
                telegram_sesion.post(url, data=data, files={'photo': (f'radar.{ext}', img_bytes, f'image/{ext}')}, timeout=30)
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
    texto_limpio = texto.replace('\n', '  ').strip()
    data = {"files": {"estado.txt": {"content": f"{color}|{texto_limpio}"}}}
    try:
        sesion.patch(url, headers=headers, json=data, timeout=10)
    except Exception as e:
        print(f"Error actualizando Gist: {e}")

# ==========================================
# RECOLECCIÓN DE RADAR (CLIMASURGBA + METEOGBA)
# ==========================================
def obtener_desde_climasurgba():
    try:
        print("Consultando fuente principal: ClimaSurGBA...")
        res = sesion.get(URL_RADAR_CLIMASUR, headers={"Referer": "https://www.climasurgba.com.ar/"}, timeout=15)
        if res.status_code != 200:
            print(f"⚠️ ClimaSurGBA devolvió status {res.status_code}")
            return None, None

        soup = BeautifulSoup(res.text, 'html.parser')
        candidatos = []

        for img in soup.find_all('img'):
            src = img.get('src', '')
            if 'ezeiza-' in src and src.endswith('.png'):
                candidatos.append(src)

        if not candidatos:
            candidatos = re.findall(r'["\']([^"\']*ezeiza-\d{8}-\d{6}\.png)["\']', res.text)

        if not candidatos:
            print("⚠️ No se encontraron URLs de Ezeiza en ClimaSurGBA.")
            return None, None

        ultima_rel = sorted(set(candidatos), reverse=True)[0]
        if ultima_rel.startswith('http'):
            url_img = ultima_rel
        elif ultima_rel.startswith('/'):
            url_img = f"https://www.climasurgba.com.ar{ultima_rel}"
        else:
            url_img = f"https://www.climasurgba.com.ar/radar/{ultima_rel.lstrip('/')}"

        r_img = sesion.get(url_img, headers={"Referer": URL_RADAR_CLIMASUR}, timeout=20)
        if r_img.status_code == 200 and 'image' in r_img.headers.get('content-type', '').lower() and len(r_img.content) > 5000:
            print(f"✅ Imagen PNG descargada desde ClimaSurGBA: {url_img}")
            return url_img, r_img.content

        print(f"⚠️ ClimaSurGBA falló al bajar la imagen (Status: {r_img.status_code})")
        return None, None
    except Exception as e:
        print(f"⚠️ Error consultando ClimaSurGBA: {e}")
        return None, None

def obtener_desde_meteogba():
    try:
        print("Consultando respaldo: MeteoGBA...")
        headers_mgba = {"Referer": "https://meteogba.com.ar/radar"}
        res = sesion.get(URL_RADAR_METEOGBA, headers=headers_mgba, timeout=15)
        if res.status_code != 200:
            print(f"⚠️ MeteoGBA devolvió status {res.status_code}")
            return None, None

        texto_busqueda = res.text
        try:
            res_lista = sesion.get("https://meteogba.com.ar/radares", headers=headers_mgba, timeout=10)
            if res_lista.status_code == 200:
                texto_busqueda += "\n" + res_lista.text
        except Exception:
            pass

        archivos = re.findall(r'(rma2_\d{8}_\d{6}\.(?:webp|png|jpg))', texto_busqueda, re.I)
        if not archivos:
            print("⚠️ No se encontraron archivos rma2_ en MeteoGBA.")
            return None, None

        ultimo_archivo = sorted(set(archivos), reverse=True)[0]
        rutas_posibles = [
            f"https://meteogba.com.ar/radar/{ultimo_archivo}",
            f"https://meteogba.com.ar/{ultimo_archivo}",
            f"https://meteogba.com.ar/imagenes/{ultimo_archivo}"
        ]

        for url_img in rutas_posibles:
            r_img = sesion.get(url_img, headers=headers_mgba, timeout=20)
            if r_img.status_code == 200 and 'image' in r_img.headers.get('content-type', '').lower() and len(r_img.content) > 5000:
                print(f"✅ Imagen descargada desde MeteoGBA: {url_img}")
                return url_img, r_img.content

        print("⚠️ Ninguna ruta de imagen en MeteoGBA devolvió datos válidos.")
        return None, None
    except Exception as e:
        print(f"⚠️ Error consultando MeteoGBA: {e}")
        return None, None

def obtener_radar_disponible():
    url_img, img_bytes = obtener_desde_climasurgba()
    if img_bytes:
        return url_img, img_bytes
    return obtener_desde_meteogba()

# ==========================================
# DIBUJO DE MÁSCARAS Y PROCESAMIENTO RADAR
# ==========================================
def obtener_borde_mascara(mask, grosor=3):
    interior = mask.copy()
    for _ in range(grosor):
        eroded = np.zeros_like(interior)
        eroded[1:-1, 1:-1] = (
            interior[:-2, 1:-1] & interior[2:, 1:-1] &
            interior[1:-1, :-2] & interior[1:-1, 2:]
        )
        interior = eroded
    return mask & ~interior

def dibujar_zonas_radar(img_base, centro_x, centro_y, anillo_25, anillo_60, anillo_100):
    base_rgba = img_base.convert("RGBA")
    overlay = np.zeros((base_rgba.height, base_rgba.width, 4), dtype=np.uint8)

    zonas = [
        (anillo_100, (255, 220, 0)),   # 60-100 km: Amarillo
        (anillo_60,  (0, 190, 255)),   # 25-60 km: Celeste
        (anillo_25,  (230, 0, 255)),   # 0-25 km: Magenta
    ]

    for mask, (r, g, b) in zonas:
        overlay[mask] = [r, g, b, 32]
        borde = obtener_borde_mascara(mask, grosor=3)
        overlay[borde] = [r, g, b, 240]

    combinada = Image.alpha_composite(base_rgba, Image.fromarray(overlay, mode="RGBA"))
    draw = ImageDraw.Draw(combinada)

    # Cruz central en Florencio Varela (centro_x, centro_y)
    largo = 14
    draw.line([(centro_x - largo, centro_y), (centro_x + largo, centro_y)], fill=(255, 50, 50, 255), width=3)
    draw.line([(centro_x, centro_y - largo), (centro_x, centro_y + largo)], fill=(255, 50, 50, 255), width=3)
    draw.ellipse([(centro_x - 4, centro_y - 4), (centro_x + 4, centro_y + 4)], fill=(255, 255, 255, 255), outline=(255, 0, 0, 255), width=2)

    salida = io.BytesIO()
    combinada.convert("RGB").save(salida, format="PNG")
    return salida.getvalue()

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

def procesar_radar(memoria, url_imagen_radar, img_bytes):
    if not img_bytes:
        print("⚠️ Sin imagen de radar disponible para analizar en este ciclo.")
        return
    
    fecha_actual = datetime.now() - timedelta(hours=3)
    bloque_30 = "00" if fecha_actual.minute < 30 else "30"
    base_id = f"ECOS_{fecha_actual.strftime('%Y-%m-%d_%H')}_{bloque_30}"
    
    id_25, id_60, id_100 = f"{base_id}_25", f"{base_id}_60", f"{base_id}_100"
    
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
        arr = np.array(img)
        alto, ancho, _ = arr.shape
        print(f"Analizando matriz de radar ({ancho}x{alto} px) | Bloque memoria: {base_id}")
        
        # Escala proporcional respecto a la imagen estándar SMN (1341x1336)
        escala_x = ancho / 1341.0
        escala_y = alto / 1336.0
        centro_x = (ancho // 2) - int(round(65 * escala_x))
        centro_y = (alto // 2) + int(round(45 * escala_y))
        radio_max = min(alto // 2, ancho // 2) * 0.95
        
        r_100, r_60, r_25 = radio_max * (100 / 240), radio_max * (60 / 240), radio_max * (25 / 240)
        
        Y, X = np.ogrid[:alto, :ancho]
        dist_sq = (X - centro_x)**2 + (Y - centro_y)**2
        
        # Anillo celeste (25-60 km): ampliado hacia el Este sobre La Plata y la franja costera
        F_PEND_60, F_OFF_60 = 0.35, 0.82
        mask_chord_60 = X <= (centro_x + r_60 * F_OFF_60 + (Y - centro_y) * F_PEND_60)
        
        # Anillo amarillo (60-100 km): recortado al Norte/NE (sin Uruguay) y ampliado al SE (con Verónica)
        F_PEND_100, F_OFF_100 = 0.65, 0.46
        mask_chord_100 = (
            (X <= (centro_x + r_100 * F_OFF_100 + (Y - centro_y) * F_PEND_100)) &
            (Y >= (centro_y - r_100 * 0.90))
        )
        
        anillo_25 = (dist_sq <= r_25**2)
        anillo_60 = (dist_sq > r_25**2) & (dist_sq <= r_60**2) & mask_chord_60
        anillo_100 = (dist_sq > r_60**2) & (dist_sq <= r_100**2) & mask_chord_100
        
        # Detección sobre la imagen original sin dibujar
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        es_35dbz = (r > 180) & (g < 160)
        
        px_25 = int(np.sum(es_35dbz & anillo_25))
        px_60 = int(np.sum(es_35dbz & anillo_60))
        px_100 = int(np.sum(es_35dbz & anillo_100))
        print(f"DEBUG PÍXELES -> 0-25km: {px_25} | 25-60km (recortado): {px_60} | 60-100km (recortado): {px_100}")
        
        img_salida = dibujar_zonas_radar(img, centro_x, centro_y, anillo_25, anillo_60, anillo_100) if DIBUJAR_ANILLOS else img_bytes
        
        mensajes = []
        if px_25 > 30 and id_25 not in memoria:
            mensajes.append("🟣 <b>¡ALERTA CERCANA! ECOS EN ZONA NÚCLEO (de <b>*Florencio Varela*</b>)</b> 🟣\nSe detectan celdas fuertes (>35 dBZ) a menos de <b>25 km</b>.\n<i>(Silenciado x 30m)</i>")
            guardar_memoria(id_25, memoria)
            actualizar_cartel("VIOLETA", "¡PELIGRO EXTREMO! ECOS EN ZONA NÚCLEO A MENOS DE 25 KM.")
            
        if px_60 > 30 and id_60 not in memoria:
            mensajes.append("🚨 <b>¡PELIGRO! ECOS FUERTES/SEVEROS CERCA</b> 🚨\nSe detectan celdas fuertes (>35 dBZ) en el anillo de <b>25 a 60 km</b> de <b>*Florencio Varela*</b>.\n<i>(Silenciado x 30m)</i>")
            guardar_memoria(id_60, memoria)
            actualizar_cartel("AZUL", "ATENCION: ECOS FUERTES CERCA (25 a 60 km).")
            
        if px_100 > 30 and id_100 not in memoria:
            mensajes.append("🟡 <b>AVISO: ECOS EN APROXIMACIÓN</b> 🟡\nSe detectan celdas fuertes (>35 dBZ) en el anillo de <b>60 a 100 km</b> de <b>*Florencio Varela*</b>.\n<i>(Silenciado x 30m)</i>")
            guardar_memoria(id_100, memoria)
            actualizar_cartel("CELESTE", "AVISO RADAR: Ecos detectados en aproximación (60 a 100 km).")
            
        if mensajes:
            for m in mensajes: enviar_mensaje(m, tipo="radar")
            enviar_foto(caption="📡 Radar del momento", photo_url=url_imagen_radar, img_bytes=img_salida, tipo="radar")
        elif ENVIAR_PREVIEW_RADAR:
            caption_debug = (
                f"🧭 <b>Vista de calibración (Florencio Varela)</b>\n"
                f"🟣 0–25 km: <code>{px_25} px</code>\n"
                f"🔵 25–60 km: <code>{px_60} px</code>\n"
                f"🟡 60–100 km: <code>{px_100} px</code>"
            )
            enviar_foto(caption=caption_debug, photo_url=url_imagen_radar, img_bytes=img_salida, tipo="radar")
            
    except Exception as e:
        print(f"Error procesando matriz de radar: {e}")
        err_id = f"ERR_RADAR_{hashlib.md5(str(e).encode()).hexdigest()[:8]}"
        if err_id not in memoria:
            enviar_mensaje(f"⚠️ <b>ALERTA DE SISTEMA:</b> Fallo escaneando Radar. Error: <code>{tg_safe(str(e))}</code>", tipo="radar")
            guardar_memoria(err_id, memoria)

def procesar_cap(memoria, url_radar, img_bytes):
    try:
        headers_anticache = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
        res = sesion.get(URL_ALERTAS, headers=headers_anticache, timeout=10)
        
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
                xml_raw = sesion.get(url_xml, headers=headers_anticache, timeout=15).text
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
                if img_bytes or url_radar:
                    enviar_foto("📡 Radar del momento", photo_url=url_radar, img_bytes=img_bytes, tipo="alerta")
            else:
                guardar_memoria(f"VARELA_NO_{id_xml}", memoria)

        if es_nuevo_boletin:
            if not hay_alerta:
                actualizar_cartel("VERDE", "Clima tranquilo. Sin alertas vigentes.")
            guardar_memoria(id_update, memoria)
            
    except Exception as e:
        print(f"Error en procesar_cap: {e}")

def procesar_acp(memoria, url_radar, img_bytes):
    try:
        headers_anticache = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
        res = sesion.get(URL_ACP, headers=headers_anticache, timeout=10)
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
                xml_raw = sesion.get(url_xml, headers=headers_anticache, timeout=15).text
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
                
                if img_bytes or url_radar:
                    enviar_foto("📡 Radar (ACP)", photo_url=url_radar, img_bytes=img_bytes, tipo="alerta")
            else:
                guardar_memoria(f"ACP_NO_{id_xml}", memoria)
                
    except Exception as e:
        print(f"Error en procesar_acp: {e}")

def procesar_shn(memoria):
    try:
        tiempo_actual = int(datetime.now().timestamp())
        try:
            res = sesion.get(f"{URL_SHN_XML}?nocache={tiempo_actual}", timeout=10)
        except Exception:
            res = sesion.get(f"{URL_SHN_XML.replace('https://', 'http://')}?nocache={tiempo_actual}", timeout=10)
        
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
    
    radar_url, radar_bytes = obtener_radar_disponible()
    print(f"URL del radar detectada: {radar_url}")
    
    print("Procesando Alertas CAP (SMN)...")
    procesar_cap(memoria_actual, radar_url, radar_bytes)
    
    print("Procesando Avisos a Corto Plazo (ACP)...")
    procesar_acp(memoria_actual, radar_url, radar_bytes)
    
    print("Procesando Avisos Hidrológicos (SHN)...")
    procesar_shn(memoria_actual)
    
    print("Procesando Escaneo de Radar...")
    procesar_radar(memoria_actual, radar_url, radar_bytes)
    
    print("--- EJECUCIÓN FINALIZADA ---")
