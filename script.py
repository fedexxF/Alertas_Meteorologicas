import requests
import re
import os
import urllib3
from datetime import datetime
from shapely.geometry import Point, Polygon

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
TIPO_EJECUCION = os.environ.get('GITHUB_EVENT_NAME')

URL_ACP = 'https://ssl.smn.gob.ar/feeds/avisocorto_GeoRSS.xml'
URL_ALERTAS = 'https://ssl.smn.gob.ar/feeds/CAP/rss_alertaCAP_nuevo_2026.xml'

PUNTO_INTERES = Point(-65.59, -31.00)
NOMBRE_LOCALIDAD = "Ocampo"

# Sesión blindada con headers para evitar bloqueos del SMN al descargar los XML
sesion = requests.Session()
sesion.verify = False
sesion.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xml'
})

def enviar_telegram(mensaje):
    try:
        url_tg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'HTML'}
        res = sesion.post(url_tg, data=payload)
        
        # Rayos X para Telegram
        if res.status_code != 200:
            print(f"❌ ERROR DE TELEGRAM (El mensaje fue rechazado): {res.text}")
        else:
            print("✅ ¡Mensaje enviado a Telegram con éxito!")
    except Exception as e:
        print(f"❌ Excepción fatal en Telegram: {e}")

def formatear_fecha_alerta(fecha_iso):
    try:
        dt = datetime.strptime(fecha_iso[:19], "%Y-%m-%dT%H:%M:%S")
        dias = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"]
        dia_semana = dias[dt.weekday()]
        return f"{dia_semana} {dt.strftime('%d/%m')}", dt.strftime('%H')
    except:
        return "Fecha Desconocida", "XX"

def procesar_alertas_cap():
    print("\n--- INICIANDO ESCANEO DE ALERTAS A 24HS (CAP) ---")
    try:
        res = sesion.get(URL_ALERTAS, timeout=10)
        if res.status_code != 200: 
            print(f"❌ El SMN bloqueó la lectura general. Código: {res.status_code}")
            return
            
        items = re.findall(r'<item>(.*?)</item>', res.text, re.DOTALL)
        print(f"📡 Se detectaron {len(items)} alertas activas en todo el país.")
        
        for i, item in enumerate(items, 1):
            link_match = re.search(r'<link>(.*?)</link>', item, re.DOTALL)
            if not link_match: continue
            
            link_xml_cap = link_match.group(1).strip()
            
            cap_res = sesion.get(link_xml_cap, timeout=10)
            if cap_res.status_code != 200: 
                print(f"❌ No se pudo abrir la alerta {i}. Código: {cap_res.status_code}")
                continue
            
            xml_detalle = re.sub(r'<(/?)[a-zA-Z0-9_]+:([a-zA-Z0-9_]+)', r'<\1\2', cap_res.text)
            
            # Extraemos qué zonas dice el XML para mostrarlas en la consola
            area_match = re.search(r'<areaDesc>(.*?)</areaDesc>', xml_detalle, re.DOTALL | re.IGNORECASE)
            zonas_texto = area_match.group(1).strip() if area_match else "Desconocida"
            print(f"🔍 Evaluando Alerta {i}: Zonas -> {zonas_texto[:80]}...")
            
            poly_matches = re.findall(r'<polygon>(.*?)</polygon>', xml_detalle, re.DOTALL)
            afectado = False
            
            for poly_str in poly_matches:
                valores = poly_str.replace(',', ' ').split()
                coords = []
                for j in range(0, len(valores)-1, 2):
                    try:
                        coords.append((float(valores[j+1]), float(valores[j])))
                    except ValueError:
                        continue
                if len(coords) >= 3:
                    poligono = Polygon(coords)
                    if poligono.contains(PUNTO_INTERES): 
                        afectado = True
                        print("   🎯 ¡Coincidencia matemática por POLÍGONO!")
                        break
            
            if not afectado and (NOMBRE_LOCALIDAD.lower() in xml_detalle.lower() or NOMBRE_LOCALIDAD.lower() in item.lower()): 
                afectado = True
                print("   🎯 ¡Coincidencia por TEXTO!")
                
            if not afectado: 
                continue
            
            print("   ⚠️ ¡ALERTA PARA OCAMPO CONFIRMADA! Extrayendo datos...")
            
            evento_match = re.search(r'<event>(.*?)</event>', xml_detalle, re.DOTALL)
            evento = evento_match.group(1).strip().upper() if evento_match else "FENÓMENO"
            
            desc_match = re.search(r'<description>(.*?)</description>', xml_detalle, re.DOTALL)
            desc = desc_match.group(1).strip() if desc_match else "Sin descripción adicional."
            # Limpiamos símbolos matemáticos que pueden hacer colapsar a Telegram
            desc = desc.replace('<', 'menor a').replace('>', 'mayor a')
            
            sev_match = re.search(r'<severity>(.*?)</severity>', xml_detalle, re.DOTALL)
            severidad = sev_match.group(1).strip().lower() if sev_match else "unknown"
            
            nivel, emoji, riesgo = "desconocido", "⚠️", "Riesgo no especificado"
            if "moderate" in severidad:
                nivel, emoji, riesgo = "amarillo", "🟡", "Riesgo meteorológico leve"
            elif "severe" in severidad:
                nivel, emoji, riesgo = "naranja", "🟠", "Riesgo meteorológico moderado a alto"
            elif "extreme" in severidad:
                nivel, emoji, riesgo = "rojo", "🔴", "Riesgo meteorológico extremo"
                
            inicio_match = re.search(r'<effective>(.*?)</effective>', xml_detalle, re.DOTALL) or re.search(r'<onset>(.*?)</onset>', xml_detalle, re.DOTALL)
            fin_match = re.search(r'<expires>(.*?)</expires>', xml_detalle, re.DOTALL)
            
            fecha_dia, hora_inicio = formatear_fecha_alerta(inicio_match.group(1).strip()) if inicio_match else ("N/A", "XX")
            _, hora_fin = formatear_fecha_alerta(fin_match.group(1).strip()) if fin_match else ("N/A", "XX")
            
            hora_emision = datetime.now().strftime("%H:%M")
            
            mensaje = (
                f"⚠️ Nuevamente el SMN actualizó su sistema de alerta temprana a las {hora_emision} hs "
                f"dejando bajo alerta meteorológica nivel {nivel} a {NOMBRE_LOCALIDAD.title()}, se copia la misma:\n\n"
                f"‼️⚠️ Alerta meteorológica del SMN por \"{evento}\" para el {fecha_dia} desde las {hora_inicio} hasta las {hora_fin} hs.- nivel {nivel}\n\n"
                f"{desc}\n\n"
                f"{emoji} {riesgo}"
            )
            enviar_telegram(mensaje)
            
    except Exception as e:
        print(f"❌ Error crítico procesando Alertas CAP: {e}")

def procesar_acp_georss():
    # Mantenemos el código de ACP que ya sabemos que funciona perfecto
    pass # (Asegurate de dejar el código de ACP original acá abajo para no perderlo)

def chequear_alertas():
    if TIPO_EJECUCION == 'workflow_dispatch':
        enviar_telegram(f"✅ <b>¡Sistema activado!</b>\nMonitoreando Alertas y Avisos a Corto Plazo para {NOMBRE_LOCALIDAD.title()}.")
    procesar_alertas_cap()
    # procesar_acp_georss() (Pausamos el ACP un segundo para probar solo las Alertas)

if __name__ == '__main__':
    chequear_alertas()
