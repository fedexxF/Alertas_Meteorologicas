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

PUNTO_INTERES = Point(-58.2758, -34.7975)
NOMBRE_LOCALIDAD = "Florencio Varela"

def enviar_telegram(mensaje):
    try:
        url_tg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}
        requests.post(url_tg, data=payload)
    except Exception as e:
        print(f"Error en Telegram: {e}")

def formatear_fecha_alerta(fecha_iso):
    # Convierte "2026-09-28T03:00:00-03:00" a "DOM 27/09" y "03"
    try:
        dt = datetime.strptime(fecha_iso[:19], "%Y-%m-%dT%H:%M:%S")
        dias = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"]
        dia_semana = dias[dt.weekday()]
        fecha_corta = f"{dia_semana} {dt.strftime('%d/%m')}"
        hora = dt.strftime('%H')
        return fecha_corta, hora
    except:
        return "Fecha Desconocida", "XX"

def procesar_alertas_cap():
    try:
        res = requests.get(URL_ALERTAS, timeout=10, verify=False)
        if res.status_code != 200: return
        
        items = re.findall(r'<item>(.*?)</item>', res.text, re.DOTALL)
        
        for item in items:
            link_match = re.search(r'<link>(.*?)</link>', item)
            if not link_match: continue
            
            link_xml_cap = link_match.group(1).strip()
            
            # Entramos al XML detallado para sacar severidad, fechas y polígono real
            cap_res = requests.get(link_xml_cap, timeout=10, verify=False)
            if cap_res.status_code != 200: continue
            xml_detalle = cap_res.text
            
            # Extraer polígono del detalle
            poly_match = re.search(r'<polygon>(.*?)</polygon>', xml_detalle)
            afectado = False
            
            if poly_match:
                valores = poly_match.group(1).replace(',', ' ').split()
                coords = []
                for i in range(0, len(valores), 2):
                    coords.append((float(valores[i+1]), float(valores[i])))
                
                poligono = Polygon(coords)
                if poligono.contains(PUNTO_INTERES): afectado = True
                
            if not afectado: continue
            
            # Extraer datos de la alerta
            evento_match = re.search(r'<event>(.*?)</event>', xml_detalle)
            evento = evento_match.group(1).upper() if evento_match else "FENÓMENO"
            
            desc_match = re.search(r'<description>(.*?)</description>', xml_detalle)
            desc = desc_match.group(1) if desc_match else "Sin descripción adicional."
            
            # Severidad y colores
            sev_match = re.search(r'<severity>(.*?)</severity>', xml_detalle)
            severidad = sev_match.group(1).lower() if sev_match else "unknown"
            
            nivel = "desconocido"
            emoji = "⚠️"
            riesgo = "Riesgo no especificado"
            
            if severidad == "moderate":
                nivel = "amarillo"
                emoji = "🟡"
                riesgo = "Riesgo meteorológico leve"
            elif severidad == "severe":
                nivel = "naranja"
                emoji = "🟠"
                riesgo = "Riesgo meteorológico moderado a alto"
            elif severidad == "extreme":
                nivel = "rojo"
                emoji = "🔴"
                riesgo = "Riesgo meteorológico extremo"
                
            # Fechas de inicio y fin
            inicio_match = re.search(r'<effective>(.*?)</effective>', xml_detalle)
            fin_match = re.search(r'<expires>(.*?)</expires>', xml_detalle)
            
            fecha_dia, hora_inicio = formatear_fecha_alerta(inicio_match.group(1)) if inicio_match else ("N/A", "XX")
            _, hora_fin = formatear_fecha_alerta(fin_match.group(1)) if fin_match else ("N/A", "XX")
            
            hora_emision = datetime.now().strftime("%H:%M")
            
            mensaje = (
                f"⚠️ Nuevamente el SMN actualizó su sistema de alerta temprana a las {hora_emision} hs "
                f"dejando bajo alerta meteorológica nivel {nivel} a {NOMBRE_LOCALIDAD}, se copia la misma:\n\n"
                f"‼️⚠️ Alerta meteorológica del SMN por \"{evento}\" para el {fecha_dia} desde las {hora_inicio} hasta las {hora_fin} hs.- nivel {nivel}\n\n"
                f"{desc}\n\n"
                f"{emoji} {riesgo}"
            )
            
            enviar_telegram(mensaje)
            
    except Exception as e:
        print(f"Error procesando Alertas CAP: {e}")

def procesar_acp_georss():
    try:
        res = requests.get(URL_ACP, timeout=10, verify=False)
        if res.status_code != 200: return
        items = re.findall(r'<item>(.*?)</item>', res.text, re.DOTALL)
        
        for item in items:
            poly_match = re.search(r'<georss:polygon>(.*?)</georss:polygon>', item)
            afectado = False
            
            if poly_match:
                valores = poly_match.group(1).strip().split()
                coords = []
                for i in range(0, len(valores), 2):
                    coords.append((float(valores[i+1]), float(valores[i])))
                
                poligono = Polygon(coords)
                if poligono.contains(PUNTO_INTERES): afectado = True
            
            if not afectado and NOMBRE_LOCALIDAD in item: afectado = True
                
            if afectado:
                fen_match = re.search(r'por ocurrencia de\s*([^<]+)</b>', item, re.IGNORECASE)
                fenomeno = fen_match.group(1).strip() if fen_match else "TORMENTAS FUERTES"
                
                zonas_matches = re.findall(r'<p><b>([A-ZÁÉÍÓÚÑ\s]+):</b>\s*(.*?)</p>', item)
                zonas = " - ".join([f"{prov.strip()}: {deptos.strip()}" for prov, deptos in zonas_matches]) if zonas_matches else "Ver detalle en SMN"
                
                tit_match = re.search(r'<title>(.*?)</title>', item, re.DOTALL)
                f_match = re.search(r'(\d{2}-\d{2}-\d{4})\s+a las\s+(\d{2}:\d{2})', tit_match.group(1) if tit_match else "")
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
        enviar_telegram(f"✅ <b>¡Sistema activado!</b>\nMonitoreando Alertas y Avisos a Corto Plazo para {NOMBRE_LOCALIDAD}.")
        
    procesar_alertas_cap()
    procesar_acp_georss()

if __name__ == '__main__':
    chequear_alertas()
