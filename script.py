import requests
import re
import os
from shapely.geometry import Point, Polygon

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
TIPO_EJECUCION = os.environ.get('GITHUB_EVENT_NAME')

URL_ACP_GEORSS = 'https://ssl.smn.gob.ar/feeds/avisocorto_GeoRSS.xml'
URL_ALERTAS_CAP = 'https://ssl.smn.gob.ar/feeds/CAP/rss_alertaCAP_nuevo_2026.xml'

# Coordenadas de interés (La Plata: Longitud, Latitud)
PUNTO_INTERES = Point(-57.9500, -34.9333)

def enviar_telegram(mensaje, imagen_url=None):
    if imagen_url:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        payload = {'chat_id': CHAT_ID, 'photo': imagen_url, 'caption': mensaje, 'parse_mode': 'HTML'}
        res = requests.post(url, data=payload)
        if res.status_code == 200:
            return
            
    # Si no hay imagen o falla el envío de la foto, envía texto plano
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}
    requests.post(url, data=payload)

def procesar_acp_georss():
    try:
        res = requests.get(URL_ACP_GEORSS, timeout=10)
        res.raise_for_status()
        items = re.findall(r'<item>(.*?)</item>', res.text, re.DOTALL)
        
        for item in items:
            # 1. Extraer Polígono GeoRSS (-lat -lon -lat -lon ...)
            poly_match = re.search(r'<georss:polygon>(.*?)</georss:polygon>', item)
            afectado = False
            
            if poly_match:
                valores = poly_match.group(1).strip().split()
                coords = []
                for i in range(0, len(valores), 2):
                    lat = float(valores[i])
                    lon = float(valores[i+1])
                    coords.append((lon, lat))  # Shapely usa (longitud, latitud)
                
                poligono = Polygon(coords)
                if poligono.contains(PUNTO_INTERES):
                    afectado = True
            elif "La Plata" in item:
                afectado = True
                
            if afectado:
                # 2. Fenómeno meteorológico
                fenomeno_match = re.search(r'por ocurrencia de\s*([^<]+)</b>', item, re.IGNORECASE)
                fenomeno = fenomeno_match.group(1).strip() if fenomeno_match else "TORMENTAS FUERTES"
                
                # 3. Zonas afectadas
                zonas_matches = re.findall(r'<p><b>([A-ZÁÉÍÓÚÑ\s]+):</b>\s*(.*?)</p>', item)
                if zonas_matches:
                    zonas = " - ".join([f"{prov.strip()}: {deptos.strip()}" for prov, deptos in zonas_matches])
                else:
                    zonas = "Consultar detalle en SMN"
                
                # 4. Fecha de emisión
                titulo_match = re.search(r'<title>(.*?)</title>', item, re.DOTALL)
                titulo = titulo_match.group(1) if titulo_match else ""
                fecha_match = re.search(r'(\d{2}-\d{2}-\d{4})\s+a las\s+(\d{2}:\d{2})', titulo)
                
                if fecha_match:
                    dia_mes_anio = fecha_match.group(1).replace('-', '/')
                    hora = fecha_match.group(2)
                    fecha_str = f"{dia_mes_anio} a las {hora}h."
                else:
                    fecha_str = "No especificada"
                    
                # 5. Imagen de radar asociada
                img_match = re.search(r'<img src="(https://ssl\.smn\.gob\.ar/pronosticos/avisomet/datos_aviso/.*?/aviso\.gif)"', item)
                imagen_url = img_match.group(1) if img_match else None
                
                mensaje = (
                    f"‼️ AVISO A CORTO PLAZO DEL SMN POR \"{fenomeno}\".\n\n"
                    f"📍 <b>Zonas:</b> {zonas}\n"
                    f"📅 <b>Fecha de emisión:</b> {fecha_str}\n"
                    f"⏳ <b>Validez hasta:</b> Dos (2) horas desde la emisión."
                )
                
                enviar_telegram(mensaje, imagen_url)
                
    except Exception as e:
        print(f"Error procesando GeoRSS: {e}")

def chequear_alertas():
    if TIPO_EJECUCION == 'workflow_dispatch':
        enviar_telegram("✅ <b>¡Sistema iniciado!</b>\nEscaneando con el nuevo feed GeoRSS...")
        
    procesar_acp_georss()

if __name__ == '__main__':
    chequear_alertas()
