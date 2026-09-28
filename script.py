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

# Coordenadas exactas de La Plata
PUNTO_INTERES = Point(-57.9500, -34.9333)

def limpiar_texto(texto):
    # Elimina todo el HTML y limpia espacios extras
    texto_limpio = re.sub(r'<[^>]+>', ' ', texto)
    # Reemplaza saltos de línea por espacios para facilitar la búsqueda
    texto_limpio = texto_limpio.replace('\n', ' ').replace('\r', '')
    return re.sub(r'\s+', ' ', texto_limpio).strip()

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}
    requests.post(url, data=payload)

def chequear_alertas():
    if TIPO_EJECUCION == 'workflow_dispatch':
        enviar_telegram("✅ <b>¡Sistema iniciado!</b>\nRealizando prueba con extracción mejorada para La Plata...")

    for url in URLS_SMN:
        try:
            respuesta = requests.get(url, timeout=10)
            respuesta.raise_for_status()
            texto_xml = respuesta.text
            
            items = re.findall(r'<item>(.*?)</item>', texto_xml, re.DOTALL)
            
            for item in items:
                titulo_match = re.search(r'<title>(.*?)</title>', item)
                titulo = titulo_match.group(1).replace('<![CDATA[', '').replace(']]>', '').strip() if titulo_match else "Fenómeno Meteorológico"
                
                desc_match = re.search(r'<description><!\[CDATA\[(.*?)\]\]></description>', item, re.DOTALL)
                if not desc_match:
                    desc_match = re.search(r'<description>(.*?)</description>', item, re.DOTALL)
                
                descripcion_cruda = desc_match.group(1) if desc_match else ""
                
                # Imprimir en la consola para depurar si falla
                print(f"DEBUG - Título: {titulo}")
                print(f"DEBUG - Descripción cruda: {descripcion_cruda[:150]}...")
                
                descripcion = limpiar_texto(descripcion_cruda)
                
                # Búsquedas más robustas ignorando espacios extra
                zonas_match = re.search(r'Zonas\s*:\s*(.*?)(?:Fecha\s*de\s*emisi|Validez|$)', descripcion, re.IGNORECASE)
                fecha_match = re.search(r'Fecha\s*de\s*emisión\s*:\s*(.*?)(?:Validez|Zonas|$)', descripcion, re.IGNORECASE)
                validez_match = re.search(r'Validez\s*hasta\s*:\s*(.*?)(?:Medidas|Los\s*fen|$)', descripcion, re.IGNORECASE)
                
                zonas = zonas_match.group(1).strip() if zonas_match else "Ver link oficial"
                fecha = fecha_match.group(1).strip() if fecha_match else "No especificada"
                validez = validez_match.group(1).strip() if validez_match else "No especificada"
                
                # Si sigue fallando, al menos enviamos la descripción completa sin formato
                if zonas == "Ver link oficial" and descripcion:
                    # Intenta extraer un extracto útil si las regex fallan
                    extracto = descripcion.replace('Zonas:', '\nZonas:').replace('Fecha de emisión:', '\nFecha:').replace('Validez hasta:', '\nValidez:')
                    zonas = "Ver detalle abajo"
                    fecha = "Ver detalle abajo"
                    validez = extracto[:300] + "..." if len(extracto) > 300 else extracto
                
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
                    tipo = "AVISO A CORTO PLAZO" if "avisocortoplazo" in url else "ALERTA"
                    
                    if zonas == "Ver detalle abajo":
                         mensaje = (
                            f"‼️ {tipo} DEL SMN POR \"{titulo}\".\n\n"
                            f"<i>Detalle de la alerta:</i>\n{validez}"
                        )
                    else:
                        mensaje = (
                            f"‼️ {tipo} DEL SMN POR \"{titulo}\".\n\n"
                            f"📍 <b>Zonas:</b> {zonas}\n"
                            f"📅 <b>Fecha de emisión:</b> {fecha}\n"
                            f"⏳ <b>Validez hasta:</b> {validez}"
                        )
                    enviar_telegram(mensaje)
                    
        except Exception as e:
            print(f"Error procesando {url}: {e}")

if __name__ == '__main__':
    chequear_alertas()
