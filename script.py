import requests
import re
import os
from shapely.geometry import Point, Polygon

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

TIPO_EJECUCION = os.environ.get('GITHUB_EVENT_NAME')

URLS_SMN = [
    'https://ssl.smn.gob.ar/feeds/CAP/rss_alertaCAP_nuevo_2026.xml',
    'https://ssl.smn.gob.ar/feeds/CAP/avisocortoplazo/rss_acpCAP.xml'
]

# Coordenadas exactas de La Plata (Longitud, Latitud)
PUNTO_INTERES = Point(-57.9500, -34.9333)

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}
    requests.post(url, data=payload)

def chequear_alertas():
    # --- MENSAJE DE PRUEBA SOLO MANUAL ---
    if TIPO_EJECUCION == 'workflow_dispatch':
        enviar_telegram("✅ <b>¡Sistema iniciado!</b>\nBuscando alertas activas para La Plata...")
    # -------------------------------------

    for url in URLS_SMN:
        try:
            respuesta = requests.get(url, timeout=10)
            respuesta.raise_for_status()
            texto_xml = respuesta.text
            
            items = re.findall(r'<item>(.*?)</item>', texto_xml, re.DOTALL)
            
            for item in items:
                titulo_match = re.search(r'<title>(.*?)</title>', item)
                titulo = titulo_match.group(1) if titulo_match else "Aviso Meteorológico"
                
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
                        
                if afectado:
                    mensaje = f"⚠️ <b>ALERTA / AVISO DETECTADO</b> ⚠️\n\n<b>{titulo}</b>\n\n<i>El área de La Plata se encuentra dentro del polígono afectado.</i>"
                    enviar_telegram(mensaje)
                    
        except Exception as e:
            print(f"Error procesando {url}: {e}")

if __name__ == '__main__':
    chequear_alertas()
