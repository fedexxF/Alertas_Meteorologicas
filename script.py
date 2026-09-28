import requests
import xml.etree.ElementTree as ET
import os

# 1. Credenciales (Configuradas como variables de entorno en GitHub)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
URL_SMN = 'https://ssl.smn.gob.ar/feeds/CAP/aviso_corto_plazo/rss_acpCAP.xml' # Ejemplo Avisos a Corto Plazo

# 2. Configuración de tu localidad de interés
# Para un filtro básico usamos texto. Para mayor precisión, podrías extraer 
# el <georss:polygon> del XML y usar geopandas/shapely para evaluar la intersección.
LOCALIDAD_INTERES = "Florencio Varela" 

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}
    requests.post(url, data=payload)

def chequear_alertas():
    try:
        respuesta = requests.get(URL_SMN, timeout=10)
        respuesta.raise_for_status()
        root = ET.fromstring(respuesta.content)
        
        # 3. Iterar sobre cada alerta en el feed RSS
        for item in root.findall('.//item'):
            titulo = item.find('title').text
            descripcion = item.find('description').text
            
            # 4. Filtrar por la localidad
            if LOCALIDAD_INTERES.lower() in titulo.lower() or LOCALIDAD_INTERES.lower() in descripcion.lower():
                # IMPORTANTE: En producción, deberías guardar el ID de la alerta en un archivo
                # o variable para no volver a enviar el mismo mensaje en la siguiente ejecución.
                mensaje = f"⚠️ <b>ALERTA METEOROLÓGICA</b> ⚠️\n\n<b>{titulo}</b>\n\n{descripcion}"
                enviar_telegram(mensaje)
                
    except Exception as e:
        print(f"Error al procesar las alertas: {e}")

if __name__ == '__main__':
    chequear_alertas()
