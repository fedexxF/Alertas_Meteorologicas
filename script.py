def procesar_alertas_cap():
    try:
        res = sesion.get(URL_ALERTAS, timeout=10)
        if res.status_code != 200: return
            
        items = re.findall(r'<item>(.*?)</item>', res.text, re.DOTALL | re.IGNORECASE)
        
        for item in items:
            link_match = re.search(r'<link[^>]*href=["\'](.*?)["\']', item, re.IGNORECASE) or re.search(r'<link>(.*?)</link>', item, re.IGNORECASE | re.DOTALL)
            if not link_match: continue
            
            link_xml_cap = link_match.group(1).strip()
            # Extraemos la ID o nombre del archivo del link para mostrarlo en el mensaje
            xml_id_archivo = link_xml_cap.split('/')[-1]
            
            try:
                cap_res = sesion.get(link_xml_cap, timeout=10)
                if cap_res.status_code != 200: continue
                xml_raw = cap_res.text
            except:
                continue
            
            # Limpiamos los prefijos de las etiquetas XML
            xml_raw = re.sub(r'<(/?)[a-zA-Z0-9_]+:([a-zA-Z0-9_]+)', r'<\1\2', xml_raw)
            
            # Extraemos la hora de emisión general del mensaje
            sent_match = re.search(r'<sent>(.*?)</sent>', xml_raw, re.IGNORECASE | re.DOTALL)
            _, hora_emision = formatear_fecha_alerta(sent_match.group(1).strip()) if sent_match else ("N/A", "XX:XX")

            # Desglosamos el XML por cada bloque <info> independiente (mañana, tarde, etc.)
            info_blocks = re.findall(r'<info>(.*?)</info>', xml_raw, re.DOTALL | re.IGNORECASE)
            if not info_blocks:
                info_blocks = [xml_raw] 
                
            for info in info_blocks:
                afectado = False
                poly_matches = re.findall(r'<polygon>(.*?)</polygon>', info, re.IGNORECASE | re.DOTALL)
                
                if not poly_matches:
                    poly_matches = re.findall(r'<polygon>(.*?)</polygon>', xml_raw, re.IGNORECASE | re.DOTALL)
                
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
                        if poligono.intersects(AREA_INTERES): 
                            afectado = True
                            break
                
                if not afectado and NOMBRE_LOCALIDAD.lower() in info.lower(): 
                    afectado = True
                    
                if not afectado: 
                    continue
                
                # Extraemos los datos locales EXCLUSIVAMENTE de este bloque <info> específico
                evento_match = re.search(r'<event>(.*?)</event>', info, re.IGNORECASE | re.DOTALL)
                evento = evento_match.group(1).strip().upper() if evento_match else "FENÓMENO"
                
                desc_match = re.search(r'<description>(.*?)</description>', info, re.IGNORECASE | re.DOTALL)
                desc = desc_match.group(1).strip() if desc_match else "Sin descripción adicional."
                desc = desc.replace('<', ' menor a ').replace('>', ' mayor a ')
                
                sev_match = re.search(r'<severity>(.*?)</severity>', info, re.IGNORECASE | re.DOTALL)
                severidad = sev_match.group(1).strip().lower() if severidad else "unknown"
                
                nivel, emoji, riesgo = "desconocido", "⚠️", "Riesgo no especificado"
                if "moderate" in severidad:
                    nivel, emoji, riesgo = "amarillo", "🟡", "Riesgo meteorológico leve"
                elif "severe" in severidad:
                    nivel, emoji, riesgo = "naranja", "🟠", "Riesgo meteorológico moderado a alto"
                elif "extreme" in severidad:
                    nivel, emoji, riesgo = "rojo", "🔴", "Riesgo meteorológico extremo"
                    
                inicio_match = re.search(r'<effective>(.*?)</effective>', info, re.IGNORECASE | re.DOTALL) or re.search(r'<onset>(.*?)</onset>', info, re.IGNORECASE | re.DOTALL)
                fin_match = re.search(r'<expires>(.*?)</expires>', info, re.IGNORECASE | re.DOTALL)
                
                fecha_dia, hora_inicio = formatear_fecha_alerta(inicio_match.group(1).strip()) if inicio_match else ("N/A", "XX:XX")
                _, hora_fin = formatear_fecha_alerta(fin_match.group(1).strip()) if fin_match else ("N/A", "XX:XX")
                
                mensaje = (
                    f"⚠️ Nuevamente el SMN actualizó su sistema de alerta temprana a las {hora_emision} hs "
                    f"dejando bajo alerta meteorológica nivel {nivel} a {NOMBRE_LOCALIDAD.title()}, se copia la misma:\n\n"
                    f"‼️⚠️ Alerta meteorológica del SMN por \"{evento}\" para el {fecha_dia} desde las {hora_inicio} hasta las {hora_fin} hs.- nivel {nivel}\n\n"
                    f"{desc}\n\n"
                    f"{emoji} {riesgo}\n\n"
                    f"🔗 <i>ID Fuente: {xml_id_archivo}</i>"
                )
                enviar_telegram(mensaje)
            
    except Exception as e:
        print(f"Error procesando Alertas CAP: {e}")
