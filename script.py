import requests
import re
import os
import urllib3
from datetime import datetime, timedelta
from shapely.geometry import Point, Polygon

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
TIPO_EJECUCION = os.environ.get('GITHUB_EVENT_NAME')

URL_ACP = 'https://ssl.smn.gob.ar/feeds/avisocorto_GeoRSS.xml'
URL_ALERTAS = 'https://ssl.smn.gob.ar/feeds/CAP/rss_alertaCAP_nuevo_2026.xml'

PUNTO_INTERES = Point(-53.70, -26.50)
AREA_INTERES = PUNTO_INTERES.buffer(0.45) 
NOMBRE_LOCALIDAD = "Villa Huidobro"
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
            
            # --- FILTRO ANTI-SPAM DE PRODUCCIÓN ---
            if xml_id_archivo in memoria_actual:
                continue
            
            try:
                cap_res = sesion.get(link_xml_cap, timeout=10)
                if cap_res.status_code != 200: continue
                xml_raw = cap_res.text
            except:
                continue
            
            xml_raw = re.sub(r'<(/?)[a-zA-Z0-9_]+:([a-zA-Z0-9_]+)', r'<\1\2', xml_raw)
            
            # EMISIÓN LITERAL (Intocable)
            sent_match = re.search(r'<sent[^>]*>(.*?)</sent>', xml_raw, re.IGNORECASE | re.DOTALL)
            dt_emision = parsear_dt(sent_match.group(1)) if sent_match else None
            _, hora_emision = formatear_dt(dt_emision)

            info_blocks = re.findall(r'<info[^>]*>(.*?)</info>', xml_raw, re.DOTALL | re.IGNORECASE)
            if not info_blocks:
                info_blocks = [xml_raw] 
                
            for info in info_blocks:
                afectado = False
                
                poly_matches = re.findall(r'<[^>]*polygon[^>]*>(.*?)</[^>]*polygon>', info, re.IGNORECASE | re.DOTALL)
                if not poly_matches:
                    poly_matches = re.findall(r'<[^>]*polygon[^>]*>(.*?)</[^>]*polygon>', xml_raw, re.IGNORECASE | re.DOTALL)
                
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
                
                if not afectado and NOMBRE_LOCALIDAD.lower() in info.lower(): 
                    afectado = True
                    
                if not afectado: 
                    continue
                
                evento_match = re.search(r'<event[^>]*>(.*?)</event>', info, re.IGNORECASE | re.DOTALL)
                evento = limpiar_cdata(evento_match.group(1)).upper() if evento_match else "FENÓMENO"
                
                desc_match = re.search(r'<description[^>]*>(.*?)</description>', info, re.IGNORECASE | re.DOTALL)
                desc = limpiar_cdata(desc_match.group(1)) if desc_match else "Sin descripción adicional."
                desc = desc.replace('<', ' menor a ').replace('>', ' mayor a ')
                
                sev_match = re.search(r'<severity[^>]*>(.*?)</severity>', info, re.IGNORECASE | re.DOTALL)
                severidad = limpiar_cdata(sev_match.group(1)).lower() if sev_match else "unknown"
                
                nivel, emoji, riesgo = "desconocido", "⚠️", "Riesgo no especificado"
                if "moderate" in severidad:
                    nivel, emoji, riesgo = "amarillo", "🟡", "Riesgo meteorológico leve"
                elif "severe" in severidad:
                    nivel, emoji, riesgo = "naranja", "🟠", "Riesgo meteorológico moderado a alto"
                elif "extreme" in severidad:
                    nivel, emoji, riesgo = "rojo", "🔴", "Riesgo meteorológico extremo"
                    
                # INICIO ADAPTADO AL FORMATO GRÁFICO (00, 06, 12, 18)
                inicio_match = re.search(r'<onset[^>]*>(.*?)</onset>', info, re.IGNORECASE | re.DOTALL)
                if not inicio_match:
                    inicio_match = re.search(r'<effective[^>]*>(.*?)</effective>', info, re.IGNORECASE | re.DOTALL)
                dt_inicio = parsear_dt(inicio_match.group(1)) if inicio_match else dt_emision
                
                if dt_inicio:
                    bloque_hora = (dt_inicio.hour // 6) * 6
                    dt_inicio = dt_inicio.replace(hour=bloque_hora, minute=0, second=0)
                
                fin_match = re.search(r'<expires[^>]*>(.*?)</expires>', info, re.IGNORECASE | re.DOTALL)
                dt_fin = parsear_dt(fin_match.group(1), es_fin=True) if fin_match else None
                
                fecha_dia, hora_inicio = formatear_dt(dt_inicio)
                fecha_fin_dia, hora_fin = formatear_dt(dt_fin)
                
                mensaje = (
                    f"⚠️ Nuevamente el SMN actualizó su sistema de alerta temprana a las {hora_emision} hs "
                    f"dejando bajo alerta meteorológica nivel {nivel} a {NOMBRE_LOCALIDAD.title()}, se copia la misma:\n\n"
                    f"‼️⚠️ Alerta meteorológica del SMN por \"{evento}\" desde el {fecha_dia} a las {hora_inicio} hs hasta el {fecha_fin_dia} a las {hora_fin} hs.- nivel {nivel}\n\n"
                    f"{desc}\n\n"
                    f"{emoji} {riesgo}\n\n"
                    f"🔗 <b>ID Archivo:</b> <code>{xml_id_archivo}</code>\n"
                    f"🌐 <a href='{link_xml_cap}'>Ver XML fuente directo</a>"
                )
                enviar_telegram(mensaje)
                
                # Se guarda en memoria y se envía
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
            
            if not afectado and NOMBRE_LOCALIDAD.lower() in item.lower(): 
                afectado = True
                
            if afectado:
                tit_match = re.search(r'<title>(.*?)</title>', item, re.IGNORECASE | re.DOTALL)
                titulo = limpiar_cdata(tit_match.group(1)) if tit_match else "ACP_DESCONOCIDO"
                
                id_acp = f"ACP_{titulo.replace(' ', '_')}"
                if id_acp in memoria_actual:
                    continue

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
                guardar_memoria(id_acp)
                memoria_actual.add(id_acp)
                
    except Exception as e:
        print(f"Error procesando ACP: {e}")

def chequear_alertas():
    memoria_actual = cargar_memoria()
    if TIPO_EJECUCION == 'workflow_dispatch':
        enviar_telegram(f"✅ <b>¡Sistema activado manualmente!</b>\nMonitoreando Alertas y Avisos a Corto Plazo para {NOMBRE_LOCALIDAD.title()}.")
    procesar_alertas_cap(memoria_actual)
    procesar_acp_georss(memoria_actual)

if __name__ == '__main__':
    chequear_alertas()
