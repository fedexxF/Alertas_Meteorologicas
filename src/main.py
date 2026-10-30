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
    tiempo_actual = int(datetime.now().timestamp())
    if os.path.exists(ARCHIVO_MEMORIA):
        with open(ARCHIVO_MEMORIA, 'r') as f:
            lineas = f.read().splitlines()

        # Buscamos la marca de tiempo de inicio
        for linea in lineas:
            if linea.startswith("INIT_"):
                tiempo_init = int(linea.split("_")[1])
                # Si pasaron más de 72 horas (259200 segundos)
                if tiempo_actual - tiempo_init > 259200:
                    with open(ARCHIVO_MEMORIA, 'w') as f:
                        f.write(f"INIT_{tiempo_actual}\n")
                    return set([f"INIT_{tiempo_actual}"])

        # Si el archivo es viejo y no tenía marca, se la agregamos
        if not any(l.startswith("INIT_") for l in lineas):
            with open(ARCHIVO_MEMORIA, 'a') as f:
                f.write(f"INIT_{tiempo_actual}\n")
            lineas.append(f"INIT_{tiempo_actual}")
        return set(lineas)

    # Si el archivo no existe, lo creamos con la marca de tiempo actual
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

def parsear_fecha(fecha_iso):
    try:
        # Se elimina el redondeo de fin de evento para respetar el horario textual (ej: 23:59)
        dt = datetime.strptime(limpiar_cdata(fecha_iso)[:19], "%Y-%m-%dT%H:%M:%S") - timedelta(hours=3)


        dias = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"]
        return f"{dias[dt.weekday()]} {dt.strftime('%d/%m')}", dt.strftime('%H:%M')
    except:
@@ -275,153 +277,153 @@
                sev = limpiar_cdata((re.search(r'<severity[^>]*>(.*?)</severity>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: ""})).group(1)).lower()

                dt_emis = parsear_fecha((re.search(r'<sent[^>]*>(.*?)</sent>', xml_raw, re.I) or type('obj', (object,), {'group': lambda x: None})).group(1))
                dt_ini = parsear_fecha((re.search(r'<onset[^>]*>(.*?)</onset>', info_txt, re.I) or re.search(r'<effective[^>]*>(.*?)</effective>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: None})).group(1))
                dt_fin = parsear_fecha((re.search(r'<expires[^>]*>(.*?)</expires>', info_txt, re.I) or type('obj', (object,), {'group': lambda x: None})).group(1))

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
        headers_anticache = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
        res = sesion.get(URL_ACP, headers=headers_anticache, timeout=10)
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
        tiempo_actual = int(datetime.now().timestamp())
        try:
            res = sesion.get(f"{URL_SHN_XML}?nocache={tiempo_actual}", timeout=10)
        except Exception:
            http_url = URL_SHN_XML.replace("https://", "http://")
            res = sesion.get(f"{http_url}?nocache={tiempo_actual}", timeout=10)

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
        err_id = "ERR_SHN_SERVER_CAIDO"
        if err_id not in memoria:
            enviar_mensaje("⚠️ <b>ALERTA DE SISTEMA:</b> La página del SHN está caída o saturada. El bot seguirá intentando en silencio sin volver a molestarte.", tipo="alerta")
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
