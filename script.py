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
    enviado_con_foto = False
    
    if imagen_url:
        try:
            # 1. Tu script descarga la imagen haciéndose pasar por un navegador humano
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            img_res = requests.get(imagen_url, headers=headers, timeout=10)
            
            if img_res.status_code == 200:
                # 2. Sube el archivo físicamente a Telegram
                url_tg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
                files = {'photo': ('aviso.gif', img_res.content)}
                data = {'chat_id': CHAT_ID, 'caption': mensaje, 'parse_mode': 'HTML'}
                
                res_tg = requests.post(url_tg, data=data, files=files)
                if res_tg.status_code == 200:
                    enviado_con_foto = True
        except Exception as e:
            print(f"Error al descargar o enviar la foto: {e}")
            
    # 3. Fallback: Si no había imagen o falló el paso anterior, manda texto plano
    if not enviado_con_foto:
        url_tg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}
        requests.post(url_tg, data=payload)

def procesar_acp_georss():
    try:
        res = requests.get(URL_ACP_GEORSS, timeout=10)
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
                    lat = float(valores[i])
                    lon = float(valores[i+1])
                    coords.append((lon, lat))
                
                poligono = Polygon(coords)
                if poligono.contains(PUNTO_INTERES):
                    afectado = True
            
            # Si la matemática falla, validamos por texto
            if not afectado and "La Plata" in item:
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
                    
                # 5. Imagen del mapa del polígono
                img_match = re.search(r'src="(https://[^"]*?/aviso\.gif)"', item)
                imagen_url = img_match.group(1) if img_match else None
                
                if not imagen_url:
                    img_match_alt = re.search(r'src="(https://[^"]*?/avi_gral\.gif)"', item)
                    imagen_url = img_match_alt.group(1) if img_match_alt else None
                
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
        enviar_telegram("✅ <b>¡Sistema iniciado!</b>\nEscaneando con descarga manual de imágenes...")
        
    procesar_acp_georss()

if __name__ == '__main__':
    chequear_alertas()
