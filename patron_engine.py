"""
patron_engine.py - VERSION FUSIONADA FINAL
Combina:
  - detectar_patrones_temporales()     → batch semanal con timestamps reales
  - procesar_patrones_incrementales()  → incremental diario sin IA, puramente Python
  - proponer_accion_predictiva()       → prediccion con ventana de hora entera
"""
import asyncio
import math
import difflib
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from zoneinfo import ZoneInfo

ZONA = ZoneInfo("America/Lima")

# ─────────────────────────────────────────────────────────────────────────────
# UTILIDADES DE TIEMPO (sin IA, puro Python)
# ─────────────────────────────────────────────────────────────────────────────

def diferencia_horas(hora1_str: str, hora2_str: str) -> float:
    """Diferencia mínima en horas entre dos tiempos considerando medianoche."""
    if not hora1_str or not hora2_str:
        return 999.0
    try:
        h1 = datetime.strptime(hora1_str[:5], "%H:%M").time()
        h2 = datetime.strptime(hora2_str[:5], "%H:%M").time()
        m1 = h1.hour * 60 + h1.minute
        m2 = h2.hour * 60 + h2.minute
        diff = abs(m1 - m2)
        if diff > 720:
            diff = 1440 - diff
        return diff / 60.0
    except Exception:
        return 999.0


def calcular_hora_tipica(historial_horas: list) -> str:
    """Media circular para promediar horas correctamente cruzando medianoche."""
    if not historial_horas:
        return "00:00"
    validas = []
    for h in historial_horas:
        try:
            validas.append(datetime.strptime(str(h)[:5], "%H:%M").time())
        except Exception:
            continue
    if not validas:
        return "00:00"
    sum_sin = sum(math.sin(math.radians(((t.hour * 60 + t.minute) / 1440.0) * 360)) for t in validas)
    sum_cos = sum(math.cos(math.radians(((t.hour * 60 + t.minute) / 1440.0) * 360)) for t in validas)
    ang = math.degrees(math.atan2(sum_sin / len(validas), sum_cos / len(validas)))
    if ang < 0:
        ang += 360
    mins = int((ang / 360.0) * 1440.0)
    return f"{mins // 60:02d}:{mins % 60:02d}"


def es_fin_de_mes(dia: int) -> bool:
    """Considera fin de mes contable: dias 26-31 y 1-4."""
    return dia >= 26 or dia <= 4


def confianza_por_ocurrencias(n: int) -> float:
    """Escala logarítmica robusta. Igual que la version batch."""
    if n >= 12: return 0.95
    if n >= 8:  return 0.85
    if n >= 5:  return 0.70
    if n >= 3:  return 0.50
    return 0.20


def similitud_texto(a: str, b: str) -> float:
    """Similitud de secuencia entre dos strings normalizados."""
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


# ─────────────────────────────────────────────────────────────────────────────
# COINCIDENCIA DINÁMICA (sin IA — puro Python)
# ─────────────────────────────────────────────────────────────────────────────

# Grupos semánticos conocidos: palabras que significan lo mismo
SINONIMOS = {
    "reunion_virtual": ["meet", "zoom", "teams", "reunion", "reu", "unete", "subir", "conectar",
                        "videollamada", "llamada virtual", "link"],
    "solicitud_documento": ["doc", "documento", "reporte", "archivo", "formato", "entregable",
                             "informe", "adjunto", "pásame", "pasame", "envía", "envia", "manda"],
    "pago": ["yape", "plin", "transferencia", "pagar", "deposita", "debe", "deuda"],
    "saludo_rutinario": ["hola", "buenas", "cómo estás", "como estas", "qué tal", "que tal"],
}

def normalizar_intencion(texto: str) -> str:
    """
    Colapsa variantes de texto a un grupo semantico canónico.
    Si no matchea ningún grupo, retorna el texto limpio.
    """
    t = texto.lower().strip()
    for grupo, palabras in SINONIMOS.items():
        if any(p in t for p in palabras):
            return grupo
    return t


def evaluar_coincidencia_dinamica(nueva_tarea: dict, patron: dict) -> dict:
    """
    Motor de coincidencia flexible. SIN llamadas a IA.
    Combina similitud textual + grupos semánticos + tolerancia temporal.
    
    nueva_tarea debe tener:
      - intencion_normalizada: str
      - hora_evento: str|None  (HH:MM)
      - dia_mes: int
      - dia_semana: int
    """
    intencion_nueva   = nueva_tarea.get("intencion_normalizada", "").lower()
    intencion_patron  = normalizar_intencion(patron.get("descripcion", ""))
    
    # ── FILTRO 1: similitud semántica (umbral 0.60 para texto libre) ──────────
    sim = similitud_texto(intencion_nueva, intencion_patron)
    
    # Boost si ambos colapsan al mismo grupo canónico
    if intencion_nueva == intencion_patron and intencion_nueva in SINONIMOS:
        sim = 1.0
    
    if sim < 0.90:
        return {"match": False, "razon": f"similitud={sim:.2f} < 0.60"}

    hora_nueva    = nueva_tarea.get("hora_evento")
    dia_mes_nuevo = nueva_tarea.get("dia_mes", 0)
    
    patron_hora    = patron.get("hora_tipica")
    patron_dias_mes = patron.get("dias_mes") or []

    # ── FILTRO 2A: evento con hora específica ─────────────────────────────────
    if hora_nueva and patron_hora:
        diff = diferencia_horas(hora_nueva, patron_hora)
        if diff <= 1.5:
            return {"match": True, "tipo": "hora_flexible",
                    "similitud": sim, "diff_horas": diff}
        else:
            return {"match": False,
                    "razon": f"misma intencion pero hora muy diferente ({diff:.1f}h)"}

    # ── FILTRO 2B: evento de fecha (fin de mes contable) ─────────────────────
    if not hora_nueva:
        if es_fin_de_mes(dia_mes_nuevo) and any(es_fin_de_mes(d) for d in patron_dias_mes):
            return {"match": True, "tipo": "fin_de_mes", "similitud": sim}
        # Sin hora y sin patron_dias_mes: match por intención pura (baja confianza)
        if not patron_dias_mes:
            return {"match": True, "tipo": "intencion_pura", "similitud": sim}

    return {"match": False, "razon": "sin contexto temporal coincidente"}


# ─────────────────────────────────────────────────────────────────────────────
# 1. DETECCIÓN BATCH SEMANAL (timestamps reales de mensajes_whatsapp)
# ─────────────────────────────────────────────────────────────────────────────

async def detectar_patrones_temporales(supabase, usuario_id: str):
    """
    Corre 1 vez/semana. Lee timestamps reales de mensajes_whatsapp.
    Detecta hora_tipica y dias_semana con precision de timestamp.
    """
    print(f"[patron_engine] Detectando patrones batch para {usuario_id}...")
    try:
        hace_90 = (datetime.now(ZONA) - timedelta(days=90)).timestamp() * 1000
        res_wa = supabase.table('mensajes_whatsapp')\
            .select('chat_id, chat_nombre, contenido, timestamp, es_mio')\
            .eq('usuario_id', usuario_id)\
            .gte('timestamp', int(hace_90))\
            .execute()

        hace_90_iso = (datetime.now(ZONA) - timedelta(days=90)).isoformat()
        res_email = supabase.table('correos_analizados')\
            .select('remitente, asunto, fecha, categoria')\
            .eq('usuario_id', usuario_id)\
            .gte('fecha', hace_90_iso)\
            .execute()

        # Resolver ancla telefónica
        chat_ids = list(set(m['chat_id'] for m in (res_wa.data or [])))
        directorio = {}
        if chat_ids:
            dir_res = supabase.table('contactos_directorio')\
                .select('chat_id, numero_telefonico')\
                .eq('usuario_id', usuario_id)\
                .execute()
            directorio = {d['chat_id']: d['numero_telefonico'] for d in (dir_res.data or [])}

        grupos_wa = defaultdict(list)
        for msg in (res_wa.data or []):
            ts = msg['timestamp'] / 1000 if msg['timestamp'] > 1e10 else msg['timestamp']
            dt = datetime.fromtimestamp(ts, tz=ZONA)
            ancla = directorio.get(msg['chat_id'], msg['chat_nombre'])
            grupos_wa[(ancla, dt.hour, dt.weekday())].append({'fecha': dt, 'contenido': msg['contenido'][:80]})

        grupos_email = defaultdict(list)
        for mail in (res_email.data or []):
            if not mail.get('fecha'):
                continue
            try:
                dt = datetime.fromisoformat(mail['fecha'].replace('Z', '+00:00')).astimezone(ZONA)
            except Exception:
                continue
            grupos_email[(mail['remitente'], dt.hour, dt.weekday())].append({'fecha': dt})

        DIA = ['lunes','martes','miercoles','jueves','viernes','sabado','domingo']

        guardados = []
        for (ancla, hora, dia), eventos in {**grupos_wa, **grupos_email}.items():
            canal = 'whatsapp' if (ancla, hora, dia) in grupos_wa else 'email'
            n = len(eventos)
            if n < 7:
                continue
            descripcion = f"Conversacion frecuente con {ancla} los {DIA[dia]} a las {hora:02d}:xx hrs"
            ultima = max(e['fecha'] for e in eventos)
            supabase.table('patron_temporal').upsert({
                'usuario_id':           usuario_id,
                'numero_telefonico':    ancla,
                'canal':                canal,
                'descripcion':          descripcion,
                'hora_tipica':          f'{hora:02d}:00:00',
                'historial_horas':      [f'{hora:02d}:00'] * min(n, 10),
                'dias_semana':          [dia],
                'dias_mes':             [],
                'tipo_accion_sugerida': 'recordatorio_chat',
                'ocurrencias':          n,
                'confianza':            confianza_por_ocurrencias(n),
                'ultima_ocurrencia':    ultima.isoformat(),
                'updated_at':           datetime.now(ZONA).isoformat(),
            }, on_conflict='usuario_id, numero_telefonico, canal, descripcion').execute()
            guardados.append({'ancla': ancla, 'hora': hora, 'dia': DIA[dia], 'n': n})

        print(f"[patron_engine] Batch: {len(guardados)} patrones guardados")
        return guardados

    except Exception as e:
        print(f"[patron_engine] Error batch: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 2. DETECCIÓN INCREMENTAL DIARIA (sin IA, desde alertas ya extraídas)
# ─────────────────────────────────────────────────────────────────────────────


async def procesar_patrones_incrementales(supabase, usuario_id: str):
    """
    Corre 1 vez/día. Lee alertas tipo tarea_ia de las últimas 24h.
    El cerebro ya extrajo las intenciones — no se llama a Gemini aquí.
    Actualiza patron_temporal con lógica Python pura y previene errores de duplicados.
    """
    print(f"[patron_engine] Incremental para {usuario_id}...")
    try:
        # Ajusta ZONA a la variable que usas en tu archivo, o usa una zona por defecto si no está global
        hace_24h = (datetime.now(ZONA) - timedelta(hours=24)).isoformat()
        res = supabase.table('alertas')\
            .select('titulo, descripcion, metadata, created_at')\
            .eq('usuario_id', usuario_id)\
            .eq('tipo', 'tarea_ia')\
            .gte('created_at', hace_24h)\
            .execute()

        nuevas_tareas = res.data or []
        if not nuevas_tareas:
            return {"status": "sleep", "procesadas": 0}

        log = []

        # Agrupar por número telefónico (desde metadata.chat o metadata.numero_telefonico)
        por_contacto = defaultdict(list)
        for t in nuevas_tareas:
            meta = t.get('metadata') or {}
            tel = meta.get('numero_telefonico') or meta.get('chat', 'desconocido')
            
            # Construir objeto normalizado
            hora_raw = meta.get('hora_del_evento') or meta.get('fecha_hora_especifica', '')
            hora_evento = None
            if hora_raw:
                try:
                    # Acepta HH:MM o ISO completo
                    hora_evento = datetime.fromisoformat(hora_raw).strftime('%H:%M') \
                        if 'T' in str(hora_raw) else str(hora_raw)[:5]
                except Exception:
                    hora_evento = str(hora_raw)[:5] if hora_raw else None

            try:
                dt_tarea = datetime.fromisoformat(t['created_at'].replace('Z', '+00:00'))
            except Exception:
                dt_tarea = datetime.now(ZONA)

            # Usar fecha_limite si existe: es la fecha REAL del evento (ej. fin de mes)
            dt_evento = dt_tarea
            fecha_limite_raw = t.get('fecha_limite') or meta.get('fecha_limite')
            if fecha_limite_raw:
                try:
                    dt_evento = datetime.fromisoformat(
                        str(fecha_limite_raw).replace('Z', '+00:00')
                    ).astimezone(ZONA)
                except Exception:
                    pass

            intencion_raw = meta.get('intencion_nativa') or t.get('titulo', '')
            por_contacto[tel].append({
                'intencion_normalizada': normalizar_intencion(intencion_raw),
                'intencion_raw':         intencion_raw,
                'hora_evento':           hora_evento,
                'dia_mes':               dt_evento.day,   # día real del evento
                'dia_semana':            dt_evento.weekday(),
                'dt':                    dt_tarea,
            })

        for numero_tel, tareas in por_contacto.items():
            # Leer patrones existentes para este contacto
            res_pt = supabase.table('patron_temporal')\
                .select('*')\
                .eq('usuario_id', usuario_id)\
                .eq('numero_telefonico', numero_tel)\
                .execute()
            patrones = res_pt.data or []

            for tarea in tareas:
                hubo_match = False
                
                # Capa 1: Limpieza básica para evitar falsos negativos en tu función
                desc_tarea_limpia = tarea['intencion_normalizada'].strip().lower()

                for patron in patrones:
                    desc_patron_limpia = patron['descripcion'].strip().lower()
                    
                    # Verificación directa primero
                    if desc_tarea_limpia == desc_patron_limpia:
                        ev = {'match': True, 'tipo': 'exacto'}
                    else:
                        # Si no es exacto, llamamos a tu función de coincidencia dinámica
                        ev = evaluar_coincidencia_dinamica(tarea, patron)
                        
                    if not ev['match']:
                        continue

                    hubo_match = True
                    nuevas_ocurrencias = patron['ocurrencias'] + 1
                    nueva_confianza    = confianza_por_ocurrencias(nuevas_ocurrencias)

                    # Actualizar historial de horas (ventana deslizante de 10)
                    hist = list(patron.get('historial_horas') or [])
                    if tarea['hora_evento']:
                        hist.append(tarea['hora_evento'])
                        hist = hist[-10:]

                    nueva_hora_tipica = calcular_hora_tipica(hist) if hist \
                        else patron.get('hora_tipica')

                    # Acumular dias_semana y dias_mes (sin duplicados)
                    dias_semana = list(set((patron.get('dias_semana') or []) + [tarea['dia_semana']]))
                    dias_mes    = list(set((patron.get('dias_mes') or [])    + [tarea['dia_mes']]))

                    supabase.table('patron_temporal').update({
                        'ocurrencias':      nuevas_ocurrencias,
                        'confianza':        nueva_confianza,
                        'hora_tipica':      nueva_hora_tipica,
                        'historial_horas':  hist,
                        'dias_semana':      dias_semana,
                        'dias_mes':         dias_mes,
                        'ultima_ocurrencia': tarea['dt'].isoformat(),
                        'updated_at':        datetime.now(ZONA).isoformat(),
                    }).eq('id', patron['id']).execute()

                    log.append({
                        'accion':     'match',
                        'patron':     patron['descripcion'][:50],
                        'tipo':       ev['tipo'],
                        'ocurrencias': nuevas_ocurrencias,
                        'nueva_hora': nueva_hora_tipica,
                    })
                    
                    # Actualizar objeto local para siguiente iteración
                    patron['ocurrencias']   = nuevas_ocurrencias
                    patron['hora_tipica']   = nueva_hora_tipica
                    patron['historial_horas'] = hist
                    break

                if not hubo_match:
                    # Crear nuevo patrón candidato (confianza inicial 0.20)
                    nuevo = {
                        'usuario_id':           usuario_id,
                        'numero_telefonico':    numero_tel,
                        'canal':                'whatsapp',
                        'descripcion':          tarea['intencion_normalizada'],
                        'hora_tipica':          tarea['hora_evento'] or tarea['dt'].strftime('%H:%M'),
                        'historial_horas':      [tarea['hora_evento']] if tarea['hora_evento'] else [],
                        'dias_semana':          [tarea['dia_semana']],
                        'dias_mes':             [tarea['dia_mes']],
                        'tipo_accion_sugerida': 'recordatorio_inferido',
                        'ocurrencias':          1,
                        'confianza':            0.20,
                        'ultima_ocurrencia':    tarea['dt'].isoformat(),
                        'updated_at':           datetime.now(ZONA).isoformat(),
                    }
                    
                    # Capa 2: Manejo de Upsert / Colisiones usando el nombre exacto de la restricción única
                    try:
                        supabase.table('patron_temporal').upsert(
                            nuevo, 
                            # Asegúrate de que estas sean exactamente las columnas de tu Unique Constraint en BD
                            on_conflict='usuario_id, numero_telefonico, canal, md5(descripcion)' 
                        ).execute()
                        
                        patrones.append(nuevo)
                        log.append({'accion': 'nuevo', 'descripcion': tarea['intencion_normalizada']})
                        
                    except Exception as e:
                        if "23505" in str(e):
                            print(f"⚠️ Colisión evitada (23505). El patrón '{tarea['intencion_normalizada']}' ya existe en BD pero no fue cargado/matcheado correctamente. Se ignora la inserción.")
                        else:
                            raise e # Re-lanzar si es otro tipo de error

        print(f"[patron_engine] Incremental: {len(log)} eventos procesados")
        return {"status": "success", "procesadas": len(log), "detalle": log}

    except Exception as e:
        print(f"[patron_engine] Error incremental: {e}")
        return {"status": "error", "detalle": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# 3. PROPONER ACCIÓN PREDICTIVA (ventana por hora entera)
# ─────────────────────────────────────────────────────────────────────────────

async def guardar_observacion(supabase, usuario_id, numero_telefonico, canal,
                               sujeto, dimension, observacion, evidencia, peso=0.5):
    """
    Registra o actualiza una observación de comportamiento.
    Si ya existe una observación idéntica (misma dimensión, sujeto y texto), 
    incrementa las confirmaciones y promedia el peso. Si no, crea un registro nuevo.
    """
    # 1. Validación de salida temprana (Early Return)
    if not observacion or not observacion.strip():
        return

    # 2. Limpieza y preparación de datos
    obs_limpia = observacion.strip()
    evidencia_limpia = [str(e)[:200] for e in (evidencia or [])[:5]]
    
    try:
        # 3. CHECK (Consultar): Buscamos si la observación exacta ya existe en la BD
        ex = supabase.table('patron_observaciones')\
            .select('id, confirmaciones, peso')\
            .eq('usuario_id', usuario_id)\
            .eq('numero_telefonico', numero_telefonico)\
            .eq('canal', canal)\
            .eq('sujeto', sujeto)\
            .eq('dimension', dimension)\
            .eq('observacion', obs_limpia)\
            .limit(1).execute()

        # 4. ACT (Actuar): Lógica de bifurcación limpia
        if ex.data:
            # CASO A: La observación ya existe -> Actualizamos
            row = ex.data[0]
            nuevo_peso = round((row['peso'] + peso) / 2.0, 3)
            nuevas_confirmaciones = row['confirmaciones'] + 1
            
            supabase.table('patron_observaciones').update({
                'confirmaciones': nuevas_confirmaciones,
                'peso': nuevo_peso,
                'ultima_vez': datetime.now(ZONA).isoformat(),
            }).eq('id', row['id']).execute()
            
        else:
            # CASO B: La observación es nueva -> Insertamos
            supabase.table('patron_observaciones').insert({
                'usuario_id':        usuario_id,
                'numero_telefonico': numero_telefonico,
                'canal':             canal,
                'sujeto':            sujeto,
                'dimension':         dimension,
                'observacion':       obs_limpia,
                'evidencia':         evidencia_limpia,
                'peso':              round(peso, 3),
                'confirmaciones':    1,
                'ultima_vez':        datetime.now(ZONA).isoformat(),
            }).execute()

    except Exception as e:
        # 5. Manejo de Errores Críticos (ahora si atrapa un error, es un problema real de DB)
        print(f"[patron_engine] ❌ Error crítico al guardar_observacion: {e}")

        

async def proponer_accion_predictiva(supabase, usuario_id: str, enviar_push_fn):
    ahora = datetime.now(ZONA)
    hora_actual   = ahora.hour
    minuto_actual = ahora.minute
    horas_ventana = {hora_actual}
    if minuto_actual >= 30:
        horas_ventana.add((hora_actual + 1) % 24)

    try:
        patrones = supabase.table('patron_temporal')\
            .select('*').eq('usuario_id', usuario_id).gte('confianza', 0.70).execute()
        if not patrones.data:
            return {"propuestas": 0}

        user_data = supabase.table('usuarios').select('fcm_token, nombre')\
            .eq('id', usuario_id).execute()
        token_fcm = user_data.data[0].get('fcm_token') if user_data.data else None
        propuestas = 0

        for patron in patrones.data:
            dias = patron.get('dias_semana') or []
            if dias and ahora.weekday() not in dias:
                continue

            hora_str = patron.get('hora_tipica')
            if not hora_str:
                continue
            try:
                h = int(hora_str[:2])
            except Exception:
                continue

            if h not in horas_ventana:
                if minuto_actual < 30 and h == (hora_actual - 1) % 24:
                    pass
                else:
                    continue

            hora_patron = ahora.replace(hour=h, minute=0, second=0, microsecond=0)

            # No duplicar si ya se proceso hoy
            ultima = patron.get('ultima_ocurrencia')
            if ultima:
                try:
                    dt_u = datetime.fromisoformat(ultima.replace('Z', '+00:00')).astimezone(ZONA)
                    if dt_u.date() == ahora.date():
                        continue
                except Exception:
                    pass

            supabase.table('alertas').insert({
                'usuario_id':  usuario_id,
                'titulo':      f"Recordatorio: {patron['descripcion'][:60]}",
                'descripcion': (f"Sueles hacer esto a las {hora_str[:5]}. "
                                f"Confianza: {int(patron['confianza']*100)}%"),
                'prioridad':   'MEDIA',
                'tipo':        'prediccion_ia',
                'estado':      'pendiente',
                'etiqueta':    'PERSONAL',
                'fecha_limite': hora_patron.isoformat(),
                'metadata': {
                    'origen': 'patron_engine', 'patron_id': patron['id'],
                    'canal': patron['canal'], 'numero_telefonico': patron['numero_telefonico'],
                    'confianza': patron['confianza'],
                }
            }).execute()
            propuestas += 1

            if token_fcm and enviar_push_fn:
                try:
                    enviar_push_fn(token=token_fcm, titulo="Recordatorio anticipado",
                                   cuerpo=f"Pronto: {patron['descripcion'][:80]}",
                                   data_extra={'tipo': 'PREDICCION', 'patron_id': patron['id'],
                                               'hora_sugerida': hora_str[:5]})
                except Exception:
                    pass

            supabase.table('patron_temporal').update({
                'accion_creada': True,
                'ultima_ocurrencia': ahora.isoformat(),
                'updated_at': ahora.isoformat()
            }).eq('id', patron['id']).execute()

        return {"propuestas": propuestas}

    except Exception as e:
        print(f"[patron_engine] Error predictivo: {e}")
        return {"propuestas": 0, "error": str(e)}
