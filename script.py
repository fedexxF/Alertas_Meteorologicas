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

def limpiar_html(texto):
    # Limpia etiquetas HTML y formato CDATA para poder leer el texto plano
    texto = texto.replace('<![CDATA[', '').replace(']]>', '')
    texto = re.sub(r'<[^>]+>', ' ', texto)
    # Reemplaza múltiples espacios en blanco por uno solo
    return re.sub(r'\s+', ' ', texto).strip()

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}
    requests.post(url, data=payload)

def chequear_alertas():
    # --- MENSAJE DE PRUEBA SOLO MANUAL ---
    if TIPO_EJECUCION == 'workflow_dispatch':
        enviar_telegram("✅ <b>¡Sistema iniciado!</b>\nBuscando alertas activas con el nuevo formato oficial para La Plata...")
    # -------------------------------------

    for url in URLS_SMN:
        try:
            respuesta = requests.get(url, timeout=10)
            respuesta.raise_for_status()
            texto_xml = respuesta.text
            
            items = re.findall(r'<item>(.*?)</item>', texto_xml, re.DOTALL)
            
            for item in items:
                # 1. Extraer título limpio
                titulo_match = re.search(r'<title>(.*?)</title>', item)
                titulo = titulo_match.group(1).replace('<![CDATA[', '').replace(']]>', '').strip() if titulo_match else "Fenómeno Meteorológico"
                
                # 2. Extraer descripción para sacar Zonas, Fecha y Validez
                desc_match = re.search(r'<description>(.*?)</description>', item, re.DOTALL)
                descripcion = limpiar_html(desc_match.group(1)) if desc_match else ""
                
                # Buscar las secciones usando expresiones regulares
                zonas_match = re.search(r'Zonas:\s*(.*?)(?:Fecha de emisi|Validez|$)', descripcion, re.IGNORECASE)
                fecha_match = re.search(r'Fecha de emisión:\s*(.*?)(?:Validez|Zonas|$)', descripcion, re.IGNORECASE)
                validez_match = re.search(r'Validez hasta:\s*(.*?)(?:Medidas|Los fen|$)', descripcion, re.IGNORECASE)
                
                zonas = zonas_match.group(1).strip() if zonas_match else "Ver link oficial"
                fecha = fecha_match.group(1).strip() if fecha_match else "No especificada"
                validez = validez_match.group(1).strip() if validez_match else "No especificada"
                
                # 3. Extraer polígono y evaluar si afecta a la localidad
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
                        
                # 4. Enviar mensaje con el nuevo formato
                if afectado:
                    tipo = "AVISO A CORTO PLAZO" if "avisocortoplazo" in url else "ALERTA"
                    
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
