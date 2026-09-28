import requests
import xml.etree.ElementTree as ET
import os
from shapely.geometry import Point, Polygon

# 1. Credenciales (Configuradas como variables de entorno en GitHub)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# URLs de Alertas y de Avisos a Corto Plazo
URLS_SMN = [
    'https://ssl.smn.gob.ar/feeds/CAP/rss_alertaCAP_nuevo_2026.xml',
    'https://ssl.smn.gob.ar/feeds/CAP/avisocortoplazo/rss_acpCAP.xml'
]

# 2. Configuración de tu localidad de interés

# Coordenadas exactas de Florencio Varela (Longitud, Latitud)
PUNTO_VARELA = Point(-58.2758, -34.7975)

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}
    requests.post(url, data=payload)

def chequear_alertas():
    for url in URLS_SMN:
        try:
            respuesta = requests.get(url, timeout=10)
            respuesta.raise_for_status()
            texto_xml = respuesta.text
            
            # Buscar todos los avisos dentro del archivo
            items = re.findall(r'<item>(.*?)</item>', texto_xml, re.DOTALL)
            
            for item in items:
                titulo_match = re.search(r'<title>(.*?)</title>', item)
                titulo = titulo_match.group(1) if titulo_match else "Aviso Meteorológico"
                
                # Extraer la geometría del aviso
                poly_match = re.search(r'<polygon>(.*?)</polygon>', item) or re.search(r'<georss:polygon>(.*?)</georss:polygon>', item)
                
                afectado = False
                
                if poly_match:
                    # El SMN envía los puntos como "lat,lon lat,lon"
                    coords_str = poly_match.group(1).split()
                    coords = []
                    for par in coords_str:
                        lat, lon = par.split(',')
                        coords.append((float(lon), float(lat)))
                    
                    poligono = Polygon(coords)
                    
                    # Evaluar matemáticamente si Florencio Varela está en el polígono
                    if poligono.contains(PUNTO_VARELA):
                        afectado = True
                else:
                    # Alternativa si el aviso no tiene polígono definido pero nombra la ciudad
                    if "Florencio Varela" in item:
                        afectado = True
                        
                if afectado:
                    mensaje = f"⚠️ <b>NUEVO AVISO / ALERTA</b> ⚠️\n\n<b>{titulo}</b>\n\n<i>Las coordenadas ingresadas se encuentran dentro del área afectada.</i>"
                    enviar_telegram(mensaje)
                    
        except Exception as e:
            print(f"Error procesando {url}: {e}")

if __name__ == '__main__':
    chequear_alertas()
