import os
import re
import urllib3
import requests
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TOKEN_ALERTAS = os.environ.get('TELEGRAM_TOKEN_ALERTAS') or os.environ.get('TELEGRAM_TOKEN')
CHAT_ID_ALERTAS = os.environ.get('CHAT_ID_ALERTAS') or os.environ.get('CHAT_ID')

URL_API_SMN_VARELA = 'https://ws1.smn.gob.ar/v1/warning/alert/area/766'

sesion = requests.Session()
sesion.verify = False

# ACA ESTÁ TU TOKEN FRESCO INYECTADO (Válido por 1 hora)
headers_navegador = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36',
    'Origin': 'https://www.smn.gob.ar',
    'Referer': 'https://www.smn.gob.ar/',
    'Authorization': 'JWT eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ3ZWIiLCJzY29wZXMiOiJST0xFX1VTRVJfRk9SRUNBU1QsUk9MRV9VU0VSX0dFT1JFRixST0xFX1VTRVJfSElTVE9SWSxST0xFX1VTRVJfSU1BR0VTLFJPTEVfVVNFUl9NQVAsUk9MRV9VU0VSX01FU1NBR0VTLFJPTEVfVVNFUl9SQU5LSU5HLFJPTEVfVVNFUl9TVEFUSVNUSUNTLFJPTEVfVVNFUl9XQVJOSU5HLFJPTEVfVVNFUl9XRUFUSEVSIiwiaWF0IjoxNzkzMzIxMTIyLCJleHAiOjE3OTMzMjQ3MjJ9.2dMSv8PGJuPSAEtjWA_aCf3e2fhEtCVsg10jugUyFp8'
}
sesion.headers.update(headers_navegador)

def tg_safe(texto):
    texto = re.sub(r'<[^>]+>', '', texto)
    return texto.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def enviar_mensaje(mensaje):
    if not TOKEN_ALERTAS or not CHAT_ID_ALERTAS:
        return
    try:
        url = f"https://api.telegram.org/bot{TOKEN_ALERTAS}/sendMessage"
        sesion.post(url, data={'chat_id': CHAT_ID_ALERTAS, 'text': mensaje, 'parse_mode': 'HTML'}, timeout=10)
    except:
        pass

def test_api_json_smn(memoria):
    print("--------------------------------------------------")
    print("-> INICIANDO INFILTRACIÓN CON TOKEN MANUAL...")
    try:
        res = sesion.get(URL_API_SMN_VARELA, timeout=15)
        print(f"-> [HTTP STATUS]: {res.status_code}")
        
        if res.status_code != 200:
            print("-> [ERROR] Falló, el token debe haber expirado.")
            return
            
        data = res.json()
        warnings = data.get("warnings", [])
        reports = data.get("reports", []) # ¡Acá están los textos!
        alertas_disparadas = 0

        mapa_niveles = {
            2: ("amarillo", "🟡", "*Riesgo meteorológico leve*"),
            3: ("naranja", "🟠", "*Riesgo meteorológico moderado*"),
            4: ("rojo", "🔴", "*Riesgo meteorológico alto*")
        }
        nombres_eventos = {37: "Viento", 39: "Lluvia", 41: "Tormenta", 54: "Zonda"}

        for warning in warnings:
            dia_alerta = warning.get("date")
            max_level = warning.get("max_level", 0)
            
            if max_level <= 1: continue
            
            for evento in warning.get("events", []):
                ev_id = evento.get("id")
                niveles_franjas = evento.get("levels", {})
                
                for franja, nivel_val in niveles_franjas.items():
                    if nivel_val and nivel_val > 1:
                        id_unico = f"TEST_API_{dia_alerta}_{ev_id}_{franja}_{nivel_val}"
                        
                        if id_unico not in memoria:
                            nombre_evento = nombres_eventos.get(ev_id, f"Fenómeno {ev_id}")
                            nivel_str, emoji, riesgo = mapa_niveles.get(nivel_val, ("alerta", "⚠️", ""))
                            
                            # --- RESCATE DE LA DESCRIPCIÓN Y LAS INSTRUCCIONES ---
                            desc_texto = "Sin descripción detallada."
                            instrucciones = ""
                            for rep in reports:
                                if rep.get("event_id") == ev_id:
                                    for lev_info in rep.get("levels", []):
                                        if lev_info.get("level") == nivel_val:
                                            desc_texto = lev_info.get("description", desc_texto)
                                            instrucciones = lev_info.get("instruction", "")
                                            break
                            
                            # Armado del mensaje completo
                            msg = (f"🌐 <b>ALERTA OFICIAL (API JSON VARELA)</b>\n\n"
                                   f"‼️⚠️ Alerta por <b>\"{tg_safe(nombre_evento.upper())}\"</b>\n"
                                   f"📅 <b>Fecha:</b> {dia_alerta} ({franja})\n"
                                   f"📍 Nivel {nivel_str.upper()} para <b>*Florencio Varela*</b>.\n\n"
                                   f"📝 <b>Detalle:</b>\n{tg_safe(desc_texto)}\n\n")
                            
                            if instrucciones:
                                msg += f"🛡️ <b>Instrucciones:</b>\n{tg_safe(instrucciones)}\n\n"
                                
                            msg += f"{emoji} {riesgo}"
                            
                            enviar_mensaje(msg)
                            guardar_memoria(id_unico, memoria)
                            alertas_disparadas += 1

        if alertas_disparadas == 0:
            print("-> 200 OK. No hay alertas nuevas.")
                            
    except Exception as e:
        print(f"-> [ERROR CRÍTICO]: {e}")
