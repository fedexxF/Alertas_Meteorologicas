import requests
import re
import os
from datetime import datetime, timedelta
from shapely.geometry import Point, Polygon

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

TIPO_EJECUCION = os.environ.get('GITHUB_EVENT_NAME')

URLS_SMN = [
    'https://ssl.smn.gob.ar/feeds/CAP/rss_alertaCAP_nuevo_2026.xml',
    'https://ssl.smn.gob.ar/feeds/CAP/avisocortoplazo/rss_acpCAP.xml'
]

PUNTO_INTERES = Point(-57.9500, -34.9333)

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}
    requests.post(url, data=payload)

def chequear_alertas():
    if TIPO_EJECUCION == 'workflow_dispatch':
        enviar_telegram("✅ <b>¡Sistema iniciado!</b>\nProbando extracción definitiva de fechas desde la URL para La Plata...")

    for url in URLS_SMN:
        try:
            respuesta = requests.get(url, timeout=10)
            respuesta.raise_for_status()
            texto_xml = respuesta.text
            
            items = re.findall(r'<item>(.*?)</item>', texto_xml, re.DOTALL)
            
            for item in items:
                titulo_match = re.search(r'<title>(.*?)</title>', item)
                titulo = titulo_match.group(1).replace('<![CDATA[', '').replace(']]>', '').strip() if titulo_match else "Fenómeno Meteorológico"
                
                desc_match = re.search(r'<description>(.*?)</description>', item, re.DOTALL)
                descripcion_cruda = desc_match.group(1) if desc_match else ""
                
                # 1. Limpiar el texto de Zonas
                zonas = descripcion_cruda.replace('Afectando parcialmente los siguientes Partidos y Departamentos:', '').strip()
                
                # 2. Extraer fecha UTC del nombre del archivo y pasar a hora Argentina
                link_match = re.search(r'<link>(.*?)</link>', item)
                fecha_str = "No especificada"
                
                if link_match:
                    link = link_match.group(1)
                    fecha_regex = re.search(r'(\d{4})_(\d{2})_(\d{2})_(\d{4})', link)
                    if fecha_regex:
                        anio, mes, dia, hora_min = fecha_regex.groups()
                        hora = hora_min[:2]
                        minuto = hora_min[2:]
                        
                        fecha_utc = datetime(int(anio), int(mes), int(dia), int(hora), int(minuto))
                        fecha_local = fecha_utc - timedelta(hours=3)
                        
                        fecha_str = fecha_local.strftime("%d/%m/%Y a las %H:%Mh")
                
                # 3. Asignar validez (los ACP son de 2 horas por estándar general)
                es_acp = "avisocortoplazo" in url
                tipo_alerta = "AVISO A CORTO PLAZO" if es_acp else "ALERTA"
                validez = "Dos (2) horas desde la emisión." if es_acp else "Consultar actualización oficial en SMN."
                
                # 4. Procesar polígonos o coincidencias por nombre
                poly_match = re.search(r'<polygon>(.*?)</polygon>', item) or re.search(r'<georss:polygon>(.*?)</georss:polygon>', item)
                afectado = False
                
                if poly_match:
                    coords_str = poly_match.group(1).split()
                    coords = []
                    for par in coords_str:
                        lat, lon = par.split(',')
                        coords.append((float(lon), float(lat)))
                    
                    poligono = Polygon(coords)
                    if poligono.contains(PUNTO_INTERES):
                        afectado = True
                else:
                    if "La Plata" in item:
                        afectado = True
                        
                # 5. Armar el mensaje final
                if afectado:
                    mensaje = (
                        f"‼️ {tipo_alerta} DEL SMN POR \"{titulo}\".\n\n"
                        f"📍 <b>Zonas:</b> {zonas}\n"
                        f"📅 <b>Fecha de emisión:</b> {fecha_str}\n"
                        f"⏳ <b>Validez hasta:</b> {validez}"
                    )
                    enviar_telegram(mensaje)
                    
        except Exception as e:
            print(f"Error procesando {url}: {e}")

if __name__ == '__main__':
    chequear_alertas()
