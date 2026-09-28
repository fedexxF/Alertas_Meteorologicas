import requests
import re
import os
import urllib3
from shapely.geometry import Point, Polygon

# Desactivar advertencias de seguridad SSL si el SMN tiene el certificado desactualizado
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
TIPO_EJECUCION = os.environ.get('GITHUB_EVENT_NAME')

URL_ACP_GEORSS = 'https://ssl.smn.gob.ar/feeds/avisocorto_GeoRSS.xml'

PUNTO_INTERES = Point(-57.9500, -34.9333)

def enviar_telegram(mensaje, imagen_url=None):
    enviado_con_foto = False
    
    if imagen_url:
        print(f"🔍 DEBUG: Intentando descargar imagen desde: {imagen_url}")
        try:
            # Camuflaje avanzado de navegador
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
                'Accept-Language': 'es-AR,es;q=0.9,en-US;q=0.8,en;q=0.7'
            }
            # verify=False salta errores de certificados SSL del gobierno
            img_res = requests.get(imagen_url, headers=headers, timeout=15, verify=False)
            
            print(f"📡 DEBUG: Estado de descarga del SMN: {img_res.status_code}")
            
            if img_res.status_code == 200:
                print("🚀 DEBUG: Imagen descargada. Subiendo archivo físico a Telegram...")
                url_tg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
                files = {'photo': ('aviso.gif', img_res.content)}
                data = {'chat_id': CHAT_ID, 'caption': mensaje, 'parse_mode': 'HTML'}
                
                res_tg = requests.post(url_tg, data=data, files=files)
                print(f"📱 DEBUG: Respuesta de Telegram: {res_tg.status_code} - {res_tg.text}")
                
                if res_tg.status_code == 200:
                    enviado_con_foto = True
            else:
                print("❌ DEBUG: El SMN no entregó la imagen (Código distinto a 200).")
                
        except Exception as e:
            print(f"❌ DEBUG: Error fatal al procesar la foto: {e}")
            
    if not enviado_con_foto:
        print("⚠️ DEBUG: Falló la foto. Enviando mensaje en formato texto plano como respaldo.")
        url_tg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}
        requests.post(url_tg, data=payload)

def procesar_acp_georss():
    try:
        res = requests.get(URL_ACP_GEORSS, timeout=10, verify=False)
        res.raise_for_status()
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
                if poligono.contains(PUNTO_INTERES):
                    afectado = True
            
            if not afectado and "La Plata" in item:
                afectado = True
                
            if afectado:
                fenomeno_match = re.search(r'por ocurrencia de\s*([^<]+)</b>', item, re.IGNORECASE)
                fenomeno = fenomeno_match.group(1).strip() if fenomeno_match else "TORMENTAS FUERTES"
                
                zonas_matches = re.findall(r'<p><b>([A-ZÁÉÍÓÚÑ\s]+):</b>\s*(.*?)</p>', item)
                zonas = " - ".join([f"{prov.strip()}: {deptos.strip()}" for prov, deptos in zonas_matches]) if zonas_matches else "Consultar en SMN"
                
                titulo_match = re.search(r'<title>(.*?)</title>', item, re.DOTALL)
                fecha_match = re.search(r'(\d{2}-\d{2}-\d{4})\s+a las\s+(\d{2}:\d{2})', titulo_match.group(1) if titulo_match else "")
                fecha_str = f"{fecha_match.group(1).replace('-', '/')} a las {fecha_match.group(2)}h." if fecha_match else "No especificada"
                    
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

# Esta es la función principal que faltaba definir en el bloque anterior
def chequear_alertas():
    if TIPO_EJECUCION == 'workflow_dispatch':
        enviar_telegram("✅ <b>¡Sistema iniciado!</b>\nEscaneando con descarga manual de imágenes y diagnóstico avanzado...")
        
    procesar_acp_georss()

if __name__ == '__main__':
    chequear_alertas()
