import requests
import re
import os
import urllib3
import hashlib
import io
import numpy as np
from PIL import Image, ImageDraw
from datetime import datetime, timedelta
from shapely.geometry import Point, Polygon
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
TIPO_EJECUCION = os.environ.get('GITHUB_EVENT_NAME')

URL_ACP = 'https://ssl.smn.gob.ar/feeds/avisocorto_GeoRSS.xml'
URL_ALERTAS = 'https://ssl.smn.gob.ar/feeds/CAP/rss_alertaCAP_nuevo_2026.xml'
URL_SHN_XML = 'https://www.hidro.gob.ar/cap/CapRP_xml.asp'

PUNTO_INTERES = Point(-58.27, -34.79)
AREA_INTERES = PUNTO_INTERES.buffer(0.036) 
NOMBRE_LOCALIDAD = "Florencio Varela"
ARCHIVO_MEMORIA = "memoria_bot.txt"

sesion = requests.Session()
sesion.verify = False
sesion.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36'
})

def cargar_memoria():
    if os.path.exists(ARCHIVO_MEMORIA):
        with open(ARCHIVO_MEMORIA, 'r') as f:
            return set(f.read().splitlines())
    return set()

def guardar_memoria(id_alerta):
    with open(ARCHIVO_MEMORIA, 'a') as f:
        f.write(f"{id_alerta}\n")

def enviar_telegram(mensaje):
    try:
        url_tg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}
        sesion.post(url_tg, data=payload)
    except Exception as e:
        print(f"Error en Telegram: {e}")

def enviar_radar_telegram():
    url_pagina = "https://www.climasurgba.com.ar/radar/ezeiza"
    try:
        res = sesion.get(url_pagina, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        img_tag = soup.find('img', src=re.compile(r'radar|ezeiza', re.IGNORECASE))
        
        if img_tag and 'src' in img_tag.attrs:
            img_url = img_tag['src']
            if not img_url.startswith('http'):
                img_url = "https://www.climasurgba.com.ar" + img_url
                
            url_tg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            payload = {'chat_id': CHAT_ID, 'photo': img_url, 'caption': '📡 Última imagen de reflectividad (Radar Ezeiza).'}
            sesion.post(url_tg, data=payload)
    except Exception as e:
        print(f"Error extrayendo radar: {e}")

def escanear_ecos_radar(memoria_actual):
    fecha_hora_actual = datetime.now() - timedelta(hours=3)
    str_hora = fecha_hora_actual.strftime('%Y-%m-%d_%H')
    
    id_60 = f"ECOS_60_{str_hora}"
    id_100 = f"ECOS_100_{str_hora}"
    id_150 = f"ECOS_150_{str_hora}"
    
    url_pagina = "https://www.climasurgba.com.ar/radar/ezeiza"
    try:
        res = sesion.get(url_pagina, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        img_tag = soup.find('img', src=re.compile(r'radar|ezeiza', re.IGNORECASE))
        
        if img_tag and 'src' in img_tag.attrs:
            img_url = img_tag['src']
            if not img_url.startswith('http'):
                img_url = "https://www.climasurgba.com.ar" + img_url
                
            img_res = sesion.get(img_url, timeout=10)
            img = Image.open(io.BytesIO(img_res.content)).convert('RGB')
            arr = np.array(img)
            
            alto, ancho, _ = arr.shape
            
            # --- CALIBRACIÓN MANUAL HACIA FLORENCIO VARELA ---
            # Valores negativos en X mueven los anillos al OESTE (Izquierda).
            # Valores negativos en Y mueven los anillos al NORTE (Arriba).
            AJUSTE_X = -70  # Arrancamos probando moverlo 80 píxeles al oeste
            AJUSTE_Y = 25   # Ajustá este valor para subir o bajar
            
            centro_x = (ancho // 2) + AJUSTE_X
            centro_y = (alto // 2) + AJUSTE_Y
            
            # El radio se sigue calculando en base al tamaño original de la imagen
            radio_max_px = min(alto // 2, ancho // 2) * 0.95
            r_150 = radio_max_px * (150 / 240)
            r_100 = radio_max_px * (100 / 240)
            r_60  = radio_max_px * (60 / 240)
            
            Y, X = np.ogrid[:alto, :ancho]
            dist = np.sqrt((X - centro_x)**2 + (Y - centro_y)**2)
            
            anillo_60 = dist <= r_60
            anillo_100 = (dist > r_60) & (dist <= r_100)
            anillo_150 = (dist > r_100) & (dist <= r_150)
            
            es_severo = (arr[:, :, 0] > 180) & (arr[:, :, 1] < 100)
            es_moderado = (arr[:, :, 0] > 180) & ((arr[:, :, 1] < 200) | (arr[:, :, 2] < 100))
            
            severos_en_60 = np.sum(es_severo & anillo_60)
            severos_en_100 = np.sum(es_severo & anillo_100)
            moderados_en_150 = np.sum(es_moderado & anillo_150)
            
            mensajes_a_enviar = []
            
            if severos_en_60 > 30 and id_60 not in memoria_actual:
                mensajes_a_enviar.append(
                    "🚨 <b>¡PELIGRO INMINENTE! ECOS SEVEROS MUY CERCA</b> 🚨\n\n"
                    "Se detectan celdas severas (>50 dBZ) a menos de <b>60 km</b> de distancia.\n"
                    "<i>(Aviso silenciado por 1 hora para este radio)</i>"
                )
                guardar_memoria(id_60)
                memoria_actual.add(id_60)
                
            if severos_en_100 > 30 and id_100 not in memoria_actual:
                mensajes_a_enviar.append(
                    "🔴 <b>ATENCIÓN: ECOS SEVEROS EN APROXIMACIÓN</b> 🔴\n\n"
                    "Se detectan celdas severas (>50 dBZ) en el anillo de <b>60 a 100 km</b> de distancia.\n"
                    "<i>(Aviso silenciado por 1 hora para este radio)</i>"
                )
                guardar_memoria(id_100)
                memoria_actual.add(id_100)
                
            if moderados_en_150 > 30 and id_150 not in memoria_actual:
                mensajes_a_enviar.append(
                    "🟡 <b>AVISO: ECOS A LA DISTANCIA</b> 🟡\n\n"
                    "Se detectan precipitaciones moderadas a fuertes (>30 dBZ) en el anillo de <b>100 a 150 km</b>.\n"
                    "<i>(Aviso silenciado por 1 hora para este radio)</i>"
                )
                guardar_memoria(id_150)
                memoria_actual.add(id_150)
            
            if mensajes_a_enviar:
                for m in mensajes_a_enviar:
                    enviar_telegram(m)
                
                # Dibujar anillos sobre el mapa
                draw = ImageDraw.Draw(img)
                draw.ellipse([centro_x - r_150, centro_y - r_150, centro_x + r_150, centro_y + r_150], outline="cyan", width=2)
                draw.ellipse([centro_x - r_100, centro_y - r_100, centro_x + r_100, centro_y + r_100], outline="yellow", width=2)
                draw.ellipse([centro_x - r_60, centro_y - r_60, centro_x + r_60, centro_y + r_60], outline="red", width=2)
                draw.point((centro_x, centro_y), fill="white")
                draw.rectangle([centro_x - 3, centro_y - 3, centro_x + 3, centro_y + 3], outline="white")

                output = io.BytesIO()
                img.save(output, format="PNG")
                output.seek(0)
                
                url_tg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
                payload = {'chat_id': CHAT_ID}
                files = {'photo': ('radar_anillos.png', output, 'image/png')}
                sesion.post(url_tg, data=payload, files=files)
                
    except Exception as e:
        print(f"Error escaneando pixeles del radar: {e}")

def limpiar_cdata(texto):
    if not texto: return ""
    return re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', texto, flags=re.DOTALL).strip()

def parsear_dt(fecha_iso, es_fin=False):
    if not fecha_iso: return None
    fecha_iso = limpiar_cdata(fecha_iso)
    try:
        dt = datetime.strptime(fecha_iso[:19], "%Y-%m-%dT%H:%M:%S")
        dt = dt - timedelta(hours=3)
        if es_fin:
            dt = dt + timedelta(minutes=1)
            dt = dt.replace(second=0)
        return dt
    except:
        return None

def formatear_dt(dt):
    if not dt: return "N/A", "XX:XX"
    dias = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"]
    dia_semana = dias[dt.weekday()]
    return f"{dia_semana} {dt.strftime('%d/%m')}", dt.strftime('%H:%M')

def procesar_alertas_cap(memoria_actual):
    try:
        res = sesion.get(URL_ALERTAS, timeout=10)
        if res.status_code != 200: return
        items = re.findall(r'<item>(.*?)</item>', res.text, re.DOTALL | re.IGNORECASE)
        for item in items:
            link_match = re.search(r'<link[^>]*href=["\'](.*?)["\']', item, re.IGNORECASE) or re.search(r'<link>(.*?)</link>', item, re.IGNORECASE | re.DOTALL)
            if not link_match: continue
            link_xml_cap = link_match.group(1).strip()
            xml_id_archivo = link_xml_cap.split('/')[-1]
            if xml_id_archivo in memoria_actual: continue
            try:
                cap_res = sesion.get(link_xml_cap, timeout=10)
                if cap_res.status_code != 200: continue
                xml_raw = cap_res.text
            except:
                continue
            xml_raw = re.sub(r'<(/?)[a-zA-Z0-9_]+:([a-zA-Z0-9_]+)', r'<\1\2', xml_raw)
            sent_match = re.search(r'<sent[^>]*>(.*?)</sent>', xml_raw, re.IGNORECASE | re.DOTALL)
            dt_emision = parsear_dt(sent_match.group(1)) if sent_match else None
            _, hora_emision = formatear_dt(dt_emision)
            info_blocks = re.findall(r'<info[^>]*>(.*?)</info>', xml_raw, re.DOTALL | re.IGNORECASE)
            if not info_blocks: info_blocks = [xml_raw] 
            for info in info_blocks:
                afectado = False
                poly_matches = re.findall(r'<[^>]*polygon[^>]*>(.*?)</[^>]*polygon>', info, re.IGNORECASE | re.DOTALL)
                if not poly_matches: poly_matches = re.findall(r'<[^>]*polygon[^>]*>(.*?)</[^>]*polygon>', xml_raw, re.IGNORECASE | re.DOTALL)
                for poly_str in poly_matches:
                    valores = limpiar_cdata(poly_str).replace(',', ' ').split()
                    coords = []
                    for j in range(0, len(valores)-1, 2):
                        try:
                            coords.append((float(valores[j+1]), float(valores[j])))
                        except ValueError:
                            continue
                    if len(coords) >= 3:
                        poligono = Polygon(coords)
                        if poligono.intersects(AREA_INTERES): 
                            afectado = True
                            break
                if not afectado and NOMBRE_LOCALIDAD.lower() in info.lower(): afectado = True
                if not afectado: continue
                evento_match = re.search(r'<event[^>]*>(.*?)</event>', info, re.IGNORECASE | re.DOTALL)
                evento = limpiar_cdata(evento_match.group(1)).upper() if evento_match else "FENÓMENO"
                desc_match = re.search(r'<description[^>]*>(.*?)</description>', info, re.IGNORECASE | re.DOTALL)
                desc = limpiar_cdata(desc_match.group(1)) if desc_match else "Sin descripción adicional."
                desc = desc.replace('<', ' menor a ').replace('>', ' mayor a ')
                sev_match = re.search(r'<severity[^>]*>(.*?)</severity>', info, re.IGNORECASE | re.DOTALL)
                severidad = limpiar_cdata(sev_match.group(1)).lower() if sev_match else "unknown"
                nivel, emoji, riesgo = "desconocido", "⚠️", "Riesgo no especificado"
                if "moderate" in severidad: nivel, emoji, riesgo = "amarillo", "🟡", "Riesgo meteorológico leve"
                elif "severe" in severidad: nivel, emoji, riesgo = "naranja", "🟠", "Riesgo meteorológico moderado a alto"
                elif "extreme" in severidad: nivel, emoji, riesgo = "rojo", "🔴", "Riesgo meteorológico extremo"
                inicio_match = re.search(r'<onset[^>]*>(.*?)</onset>', info, re.IGNORECASE | re.DOTALL)
                if not inicio_match: inicio_match = re.search(r'<effective[^>]*>(.*?)</effective>', info, re.IGNORECASE | re.DOTALL)
                dt_inicio = parsear_dt(inicio_match.group(1)) if inicio_match else dt_emision
                fin_match = re.search(r'<expires[^>]*>(.*?)</expires>', info, re.IGNORECASE | re.DOTALL)
                dt_fin = parsear_dt(fin_match.group(1), es_fin=True) if fin_match else None
                fecha_dia, hora_inicio = formatear_dt(dt_inicio)
                fecha_fin_dia, hora_fin = formatear_dt(dt_fin)
                mensaje = (
                    f"⚠️ Nuevamente el SMN actualizó su sistema de alerta temprana a las {hora_emision} hs "
                    f"dejando bajo alerta meteorológica nivel {nivel} a {NOMBRE_LOCALIDAD.title()}:\n\n"
                    f"‼️⚠️ Alerta meteorológica del SMN por \"{evento}\" desde el {fecha_dia} a las {hora_inicio} hs hasta el {fecha_fin_dia} a las {hora_fin} hs.- nivel {nivel}\n\n"
                    f"{desc}\n\n{emoji} {riesgo}\n\n"
                    f"🔗 <b>ID Archivo:</b> <code>{xml_id_archivo}</code>\n"
                    f"🌐 <a href='{link_xml_cap}'>Ver XML fuente directo</a>"
                )
                enviar_telegram(mensaje)
                enviar_radar_telegram()
                guardar_memoria(xml_id_archivo)
                memoria_actual.add(xml_id_archivo)
    except Exception as e:
        print(f"Error procesando Alertas CAP: {e}")

def procesar_acp_georss(memoria_actual):
    try:
        res = sesion.get(URL_ACP, timeout=10)
        if res.status_code != 200: return
        items = re.findall(r'<item>(.*?)</item>', res.text, re.IGNORECASE | re.DOTALL)
        for item in items:
            poly_matches = re.findall(r'<[^>]*polygon[^>]*>(.*?)</[^>]*polygon>', item, re.IGNORECASE | re.DOTALL)
            afectado = False
            for poly_str in poly_matches:
                valores = limpiar_cdata(poly_str).replace(',', ' ').split()
                coords = []
                for i in range(0, len(valores)-1, 2):
                    try:
                        coords.append((float(valores[i+1]), float(valores[i])))
                    except ValueError:
                        continue
                if len(coords) >= 3:
                    poligono = Polygon(coords)
                    if poligono.intersects(AREA_INTERES): 
                        afectado = True
                        break
            if not afectado and NOMBRE_LOCALIDAD.lower() in item.lower(): afectado = True
            if afectado:
                tit_match = re.search(r'<title>(.*?)</title>', item, re.IGNORECASE | re.DOTALL)
                titulo = limpiar_cdata(tit_match.group(1)) if tit_match else "ACP_DESCONOCIDO"
                id_acp = f"ACP_{titulo.replace(' ', '_')}"
                if id_acp in memoria_actual: continue
                fen_match = re.search(r'por ocurrencia de\s*([^<]+)</b>', item, re.IGNORECASE)
                fenomeno = fen_match.group(1).strip() if fen_match else "TORMENTAS FUERTES"
                zonas_matches = re.findall(r'<p><b>([A-ZÁÉÍÓÚÑ\s]+):</b>\s*(.*?)</p>', item, re.IGNORECASE | re.DOTALL)
                zonas = " - ".join([f"{prov.strip()}: {deptos.strip()}" for prov, deptos in zonas_matches]) if zonas_matches else "Ver detalle en SMN"
                f_match = re.search(r'(\d{2}-\d{2}-\d{4})\s+a las\s+(\d{2}:\d{2})', titulo)
                fecha_str = f"{f_match.group(1).replace('-', '/')} a las {f_match.group(2)}h." if f_match else "No especificada"
                mensaje = (
                    f"‼️ AVISO A CORTO PLAZO DEL SMN POR \"{fenomeno}\".\n\n"
                    f"📍 <b>Zonas:</b> {zonas}\n"
                    f"📅 <b>Fecha de emisión:</b> {fecha_str}\n"
                    f"⏳ <b>Validez hasta:</b> Dos (2) horas desde la emisión."
                )
                enviar_telegram(mensaje)
                enviar_radar_telegram()
                guardar_memoria(id_acp)
                memoria_actual.add(id_acp)
    except Exception as e:
        print(f"Error procesando ACP: {e}")

def procesar_alertas_shn(memoria_actual):
    try:
        res = sesion.get(URL_SHN_XML, timeout=10)
        if res.status_code != 200: return
        xml_raw = res.text
        alertas = re.findall(r'<alert[^>]*>(.*?)</alert>', xml_raw, re.DOTALL | re.IGNORECASE)
        if not alertas:
            alertas = [xml_raw] if "<info" in xml_raw.lower() or "<description" in xml_raw.lower() else []
        for alerta in alertas:
            id_match = re.search(r'<identifier[^>]*>(.*?)</identifier>', alerta, re.IGNORECASE | re.DOTALL)
            id_alerta = limpiar_cdata(id_match.group(1)) if id_match else None
            desc_match = re.search(r'<description[^>]*>(.*?)</description>', alerta, re.IGNORECASE | re.DOTALL)
            desc = limpiar_cdata(desc_match.group(1)) if desc_match else ""
            if not desc or len(desc) < 5: continue
            if not id_alerta: id_alerta = "SHN_" + hashlib.md5(desc.encode()).hexdigest()[:12]
            if id_alerta in memoria_actual: continue
            head_match = re.search(r'<headline[^>]*>(.*?)</headline>', alerta, re.IGNORECASE | re.DOTALL)
            headline = limpiar_cdata(head_match.group(1)) if head_match else "Aviso Hidrológico"
            mensaje = (
                f"🌊 <b>¡NUEVO AVISO HIDROLÓGICO DEL SHN!</b>\n\n"
                f"‼️ <b>{headline.upper()}</b>\n\n{desc}\n\n"
                f"🔗 <b>Fuente XML:</b> <a href='https://www.hidro.gob.ar/cap/CapRP_xml.asp'>Ver alerta cruda</a>\n"
                f"🌐 <b>Chequeo manual:</b> <a href='https://www.hidro.gob.ar/oceanografia/AACRIOPLA.asp'>Ver mapa y avisos del Río de la Plata</a>"
            )
            enviar_telegram(mensaje)
            guardar_memoria(id_alerta)
            memoria_actual.add(id_alerta)
    except Exception as e:
        print(f"Error procesando Alertas SHN: {e}")

def chequear_alertas():
    memoria_actual = cargar_memoria()
    
    procesar_alertas_cap(memoria_actual)
    procesar_acp_georss(memoria_actual)
    procesar_alertas_shn(memoria_actual) 
    escanear_ecos_radar(memoria_actual)

if __name__ == '__main__':
    chequear_alertas()
