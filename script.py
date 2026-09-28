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

PUNTO_INTERES = Point(-64.35, -34.50)
AREA_INTERES = PUNTO_INTERES.buffer(0.45) 
NOMBRE_LOCALIDAD = "Villa Huidobro"

sesion = requests.Session()
sesion.verify = False
sesion.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36'
})

def enviar_telegram(mensaje):
    try:
        url_tg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}
        sesion.post(url_tg, data=payload)
    except Exception as e:
        print(f"Error en Telegram: {e}")

def parsear_dt(fecha_iso, es_fin=False):
    try:
        dt = datetime.strptime(fecha_iso[:19], "%Y-%m-%dT%H:%M:%S")
        dt = dt - timedelta(hours=3)
        if es_fin:
            dt = dt + timedelta(minutes=1)
        return dt
    except:
        return None

def formatear_dt(dt):
    if not dt: return "N/A", "XX:XX"
    dias = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"]
    dia_semana = dias[dt.weekday()]
    return f"{dia_semana} {dt.strftime('%d/%m')}", dt.strftime('%H:%M')

def procesar_alertas_cap():
    try:
        res = sesion.get(URL_ALERTAS, timeout=10)
        if res.status_code != 200: return
            
        items = re.findall(r'<item>(.*?)</item>', res.text, re.DOTALL | re.IGNORECASE)
        
        for item in items:
            link_match = re.search(r'<link[^>]*href=["\'](.*?)["\']', item, re.IGNORECASE) or re.search(r'<link>(.*?)</link>', item, re.IGNORECASE | re.DOTALL)
            if not link_match: continue
            
            link_xml_cap = link_match.group(1).strip()
            xml_id_archivo = link_xml_cap.split('/')[-1]
            
            try:
                cap_res = sesion.get(link_xml_cap, timeout=10)
                if cap_res.status_code != 200: continue
                xml_raw = cap_res.text
            except:
                continue
            
            xml_raw = re.sub(r'<(/?)[a-zA-Z0-9_]+:([a-zA-Z0-9_]+)', r'<\1\2', xml_raw)
            
            sent_match = re.search(r'<sent[^>]*>([^<]+)</sent>', xml_raw, re.IGNORECASE)
            dt_emision = parsear_dt(sent_match.group(1).strip()) if sent_match else None
            _, hora_emision = formatear_dt(dt_emision)

            info_blocks = re.findall(r'<info[^>]*>(.*?)</info>', xml_raw, re.DOTALL | re.IGNORECASE)
            if not info_blocks:
                info_blocks = [xml_raw] 
                
            for info in info_blocks:
                afectado = False
                
                poly_matches = re.findall(r'<[^>]*polygon[^>]*>([^<]+)</[^>]*polygon>', info, re.IGNORECASE)
                if not poly_matches:
                    poly_matches = re.findall(r'<[^>]*polygon[^>]*>([^<]+)</[^>]*polygon>', xml_raw, re.IGNORECASE)
                
                for poly_str in poly_matches:
                    valores = poly_str.replace(',', ' ').split()
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
                
                evento_match = re.search(r'<event[^>]*>([^<]+)</event>', info, re.IGNORECASE)
                evento = evento_match.group(1).strip().upper() if evento_match else "FENÓMENO"
                
                desc_match = re.search(r'<description[^>]*>([^<]+)</description>', info, re.IGNORECASE)
                desc = desc_match.group(1).strip() if desc_match else "Sin descripción adicional."
                desc = desc.replace('<', ' menor a ').replace('>', ' mayor a ')
                
                sev_match = re.search(r'<severity[^>]*>([^<]+)</severity>', info, re.IGNORECASE)
                severidad = sev_match.group(1).strip().lower() if sev_match else "unknown"
                
                nivel, emoji, riesgo = "desconocido", "⚠️", "Riesgo no especificado"
                if "moderate" in severidad:
                    nivel, emoji, riesgo = "amarillo", "🟡", "Riesgo meteorológico leve"
                elif "severe" in severidad:
                    nivel, emoji, riesgo = "naranja", "🟠", "Riesgo meteorológico moderado a alto"
                elif "extreme" in severidad:
                    nivel, emoji, riesgo = "rojo", "🔴", "Riesgo meteorológico extremo"
                    
                # Extraemos Inicio y Fin
                inicio_match = re.search(r'<onset[^>]*>([^<]+)</onset>', info, re.IGNORECASE)
                dt_inicio = parsear_dt(inicio_match.group(1).strip()) if inicio_match else None
                
                fin_match = re.search(r'<expires[^>]*>([^<]+)</expires>', info, re.IGNORECASE)
                dt_fin = parsear_dt(fin_match.group(1).strip(), es_fin=True) if fin_match else None
                
                # FILTRO ANTI-BASURA: Si la hora de inicio es anterior a la hora de emisión 
                # (es decir, el SMN escribió mal la fecha y puso que empezó en el pasado),
                # forzamos a que el inicio sea igual a la hora de emisión para que tenga sentido temporal.
                if dt_inicio and dt_emision and dt_inicio < dt_emision:
                    dt_inicio = dt_emision
                # Si el SMN se olvidó completamente de poner <onset>, usamos la emisión.
                elif not dt_inicio:
                    dt_inicio = dt_emision
                
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
            
    except Exception as e:
        print(f"Error procesando Alertas CAP: {e}")

def procesar_acp_georss():
    try:
        res = sesion.get(URL_ACP, timeout=10)
        if res.status_code != 200: return
        
        items = re.findall(r'<item>(.*?)</item>', res.text, re.IGNORECASE | re.DOTALL)
        
        for item in items:
            poly_matches = re.findall(r'<[^>]*polygon[^>]*>([^<]+)</[^>]*polygon>', item, re.IGNORECASE)
            afectado = False
            
            for poly_str in poly_matches:
                valores = poly_str.strip().split()
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
                fen_match = re.search(r'por ocurrencia de\s*([^<]+)</b>', item, re.IGNORECASE)
                fenomeno = fen_match.group(1).strip() if fen_match else "TORMENTAS FUERTES"
                
                zonas_matches = re.findall(r'<p><b>([A-ZÁÉÍÓÚÑ\s]+):</b>\s*(.*?)</p>', item, re.IGNORECASE | re.DOTALL)
                zonas = " - ".join([f"{prov.strip()}: {deptos.strip()}" for prov, deptos in zonas_matches]) if zonas_matches else "Ver detalle en SMN"
                
                tit_match = re.search(r'<title>(.*?)</title>', item, re.IGNORECASE | re.DOTALL)
                f_match = re.search(r'(\d{2}-\d{2}-\d{4})\s+a las\s+(\d{2}:\d{2})', tit_match.group(1).strip() if tit_match else "")
                fecha_str = f"{f_match.group(1).replace('-', '/')} a las {f_match.group(2)}h." if f_match else "No especificada"
                
                mensaje = (
                    f"‼️ AVISO A CORTO PLAZO DEL SMN POR \"{fenomeno}\".\n\n"
                    f"📍 <b>Zonas:</b> {zonas}\n"
                    f"📅 <b>Fecha de emisión:</b> {fecha_str}\n"
                    f"⏳ <b>Validez hasta:</b> Dos (2) horas desde la emisión."
                )
                enviar_telegram(mensaje)
                
    except Exception as e:
        print(f"Error procesando ACP: {e}")

def chequear_alertas():
    if TIPO_EJECUCION == 'workflow_dispatch':
        enviar_telegram(f"✅ <b>¡Sistema activado!</b>\nMonitoreando Alertas y Avisos a Corto Plazo para {NOMBRE_LOCALIDAD.title()}.")
    procesar_alertas_cap()
    procesar_acp_georss()

if __name__ == '__main__':
    chequear_alertas()
