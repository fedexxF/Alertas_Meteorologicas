import requests
import re
import os
import urllib3
from shapely.geometry import Point, Polygon

# Desactivamos advertencias SSL si el SMN tiene el certificado desactualizado
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
TIPO_EJECUCION = os.environ.get('GITHUB_EVENT_NAME')

URL_ACP_GEORSS = 'https://ssl.smn.gob.ar/feeds/avisocorto_GeoRSS.xml'

# Configuración de tu localidad
#PUNTO_INTERES = Point(-58.2758, -34.7975) # Longitud, Latitud de Florencio Varela
#NOMBRE_LOCALIDAD = "Florencio Varela"
PUNTO_INTERES = Point(-57.9500, -34.9333) # Longitud, Latitud de Florencio Varela
NOMBRE_LOCALIDAD = "La Plata"

def enviar_telegram(mensaje):
    try:
        url_tg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}
        requests.post(url_tg, data=payload)
    except Exception as e:
        print(f"Error al enviar mensaje a Telegram: {e}")

def procesar_acp_georss():
    try:
        res = requests.get(URL_ACP_GEORSS, timeout=10, verify=False)
        res.raise_for_status()
        items = re.findall(r'<item>(.*?)</item>', res.text, re.DOTALL)
        
        for item in items:
            # 1. Extraer Polígono GeoRSS
            poly_match = re.search(r'<georss:polygon>(.*?)</georss:polygon>', item)
            afectado = False
            
            if poly_match:
                valores = poly_match.group(1).strip().split()
                coords = []
                for i in range(0, len(valores), 2):
                    # Shapely usa el orden (Longitud, Latitud)
                    coords.append((float(valores[i+1]), float(valores[i])))
                
                poligono = Polygon(coords)
                if poligono.contains(PUNTO_INTERES):
                    afectado = True
            
            # 2. Respaldo por nombre: Si la matemática falla, busca en el texto
            if not afectado and NOMBRE_LOCALIDAD in item:
                afectado = True
                
            if afectado:
                # 3. Extraer Fenómeno
                fenomeno_match = re.search(r'por ocurrencia de\s*([^<]+)</b>', item, re.IGNORECASE)
                fenomeno = fenomeno_match.group(1).strip() if fenomeno_match else "TORMENTAS FUERTES"
                
                # 4. Extraer Zonas afectadas
                zonas_matches = re.findall(r'<p><b>([A-ZÁÉÍÓÚÑ\s]+):</b>\s*(.*?)</p>', item)
                if zonas_matches:
                    zonas = " - ".join([f"{prov.strip()}: {deptos.strip()}" for prov, deptos in zonas_matches])
                else:
                    zonas = "Consultar detalle en SMN"
                
                # 5. Extraer Fecha de emisión
                titulo_match = re.search(r'<title>(.*?)</title>', item, re.DOTALL)
                titulo = titulo_match.group(1) if titulo_match else ""
                fecha_match = re.search(r'(\d{2}-\d{2}-\d{4})\s+a las\s+(\d{2}:\d{2})', titulo)
                
                if fecha_match:
                    dia_mes_anio = fecha_match.group(1).replace('-', '/')
                    hora = fecha_match.group(2)
                    fecha_str = f"{dia_mes_anio} a las {hora}h."
                else:
                    fecha_str = "No especificada"
                    
                # 6. Armar y enviar el mensaje
                mensaje = (
                    f"‼️ AVISO A CORTO PLAZO DEL SMN POR \"{fenomeno}\".\n\n"
                    f"📍 <b>Zonas:</b> {zonas}\n"
                    f"📅 <b>Fecha de emisión:</b> {fecha_str}\n"
                    f"⏳ <b>Validez hasta:</b> Dos (2) horas desde la emisión."
                )
                
                enviar_telegram(mensaje)
                
    except Exception as e:
        print(f"Error procesando el feed del SMN: {e}")

def chequear_alertas():
    # Solo envía el mensaje de bienvenida si se arranca a mano desde GitHub
    if TIPO_EJECUCION == 'workflow_dispatch':
        enviar_telegram(f"✅ <b>¡Sistema activado!</b>\nMonitoreando alertas para la localidad de {NOMBRE_LOCALIDAD} las 24hs.")
        
    procesar_acp_georss()

if __name__ == '__main__':
    chequear_alertas()
