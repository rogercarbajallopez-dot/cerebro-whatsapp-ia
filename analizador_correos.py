"""
SISTEMA DE ANÁLISIS INTELIGENTE DE CORREOS
Reduce costos en 99% usando filtrado en 3 capas
"""
import asyncio
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pytz
import time
from google.genai import types
import json
import gc  # 🧹 IMPORTACIÓN DEL RECOLECTOR DE BASURA
from patron_engine import guardar_observacion

class AnalizadorCorreos:
    """
    Motor de análisis de correos con optimización de costos.
    """
    
    def __init__(self):
        # CAPA 1: Patrones de spam/basura (sin IA)
        self.dominios_spam = [
            'noreply', 'no-reply', 'newsletter', 'marketing',
            'notifications', 'promo', 'deals', 'offers'
        ]
        
        self.palabras_spam = [
            'unsubscribe', 'suscripción', 'descuento', 'oferta',
            '% off', 'compra ahora', 'click here', 'gratis',
            'winner', 'ganador', 'premio', 'sorteo'
        ]
        
        # Palabras clave de ACCIÓN (requieren análisis profundo)
        self.triggers_accion = {
            'urgente': ['urgente', 'prioridad', 'inmediato', 'cuanto antes', 'hoy', 'deadline', 'asap', 'pendiente'],
            'laboral': ['entrevista', 'oferta', 'vacante', 'postulación', 'proceso de selección', 'segunda etapa', 'selección', 'cierre', 'contabilidad', 'formato', 'adjunto', 'adjuntar', 'enviar', 'reporte'],
            'academico': ['tarea', 'examen', 'proyecto', 'entrega', 'plazo', 'calificación', 'calificación', 'trabajo'],
            'legal': ['contrato', 'firma', 'documento', 'trámite', 'constancia', 'certificado', 'anexos', 'legal'],
            'financiero': ['factura', 'pago', 'vencimiento', 'cobro', 'transferencia', 'deuda', 'depósito', 'cotización']
        }
        
    # ================================================================
    # CAPA 1: FILTRO RÁPIDO (Sin IA)
    # ================================================================
    
    def es_spam_obvio(self, correo: Dict) -> bool:
        """
        Detecta spam sin usar IA (basado en patrones).
        
        Args:
            correo: {'de': str, 'asunto': str, 'cuerpo': str}
        
        Returns:
            True si es spam/basura (descartable)
        """
        remitente = correo.get('de', '').lower()
        asunto = correo.get('asunto', '').lower()
        cuerpo = correo.get('cuerpo', '')[:500].lower()  # Solo primeros 500 chars
        
        # 1. Remitente sospechoso
        if any(palabra in remitente for palabra in self.dominios_spam):
            return True
        
        # 2. Asunto típico de spam
        if any(palabra in asunto for palabra in self.palabras_spam):
            return True
        
        # 3. Correos muy cortos (probablemente notificaciones automáticas)
        if len(cuerpo) < 50:
            return True
        
        # 4. Exceso de enlaces (>5 links = probable marketing)
        if cuerpo.count('http') > 5:
            return True
        
        return False
    
    def detectar_mencion_directa(self, correo: Dict, nombre_usuario: str = "") -> bool:
        """
        Detecta si el usuario es mencionado directamente.
        Esto aumenta la prioridad del correo.
        """
        cuerpo = correo.get('cuerpo', '').lower()
        
        # Buscar menciones directas
        if nombre_usuario and nombre_usuario.lower() in cuerpo:
            return True
        
        # Patrones de mención
        patrones_mencion = [
            r'@\w+',  # @usuario
            r'\btu\b.*\b(debes|necesitas|solicito|requiero)',  # "Tu debes..."
            r'favor.*responder',
            r'necesito.*que'
        ]
        
        for patron in patrones_mencion:
            if re.search(patron, cuerpo):
                return True
        
        return False
    
    def calcular_score_inicial(self, correo: Dict, nombre_usuario: str = "") -> int:
        """
        Calcula un puntaje de importancia (0-100) usando reglas simples.
        Solo los correos con score > 40 pasan a la siguiente capa.
        """
        score = 0
        asunto = correo.get('asunto', '').lower()
        cuerpo = correo.get('cuerpo', '').lower()
        
        # +30 si tiene palabras de acción
        for categoria, palabras in self.triggers_accion.items():
            if any(palabra in asunto or palabra in cuerpo for palabra in palabras):
                score += 30
                break
        
        # +20 si está en copia pero mencionado
        if self.detectar_mencion_directa(correo, nombre_usuario):
            score += 20
        
        # +15 si es un remitente conocido (dominio corporativo)
        remitente = correo.get('de', '')
        if any(ext in remitente for ext in ['.edu', '.gob', '.com.pe', 'company.com']):
            score += 15
        
        # +10 si el asunto es corto y directo (probablemente importante)
        if 5 < len(asunto.split()) < 10:
            score += 10
        
        # +10 si NO tiene imágenes ni HTML pesado (correos personales vs marketing)
        if '<img' not in cuerpo and len(cuerpo) < 2000:
            score += 10
        
        # +15 Salvavidas: Si es corto, tiene archivos (menciona adjunto/formato/anexo) y pide algo.
        if any(w in cuerpo for w in ['adjunt', 'formato', 'anexo', 'documento', 'enviar']):
            score += 15

        # -20 si tiene "unsubscribe" (newsletters)
        if 'unsubscribe' in cuerpo or 'darse de baja' in cuerpo:
            score -= 20
        
        return max(0, min(100, score))
    
    # ================================================================
    # CAPA 2: CLASIFICACIÓN RÁPIDA (IA Lite)
    # ================================================================
    
    
    async def clasificar_con_ia_rapida(self, lote_correos: List[Dict], gemini_client) -> List[Dict]:
        """
        Clasifica hasta 10 correos en 1 sola llamada a Gemini (Batch).
        NO genera respuestas todavía.
        
        Returns:
            {
                'requiere_accion': bool,
                'categoria': str,  # 'laboral', 'academico', 'financiero', 'personal'
                'urgencia': str,   # 'alta', 'media', 'baja'
                'resumen_corto': str  # 1 línea
            }
        """
        if not lote_correos: return []

        textos_correos = ""
        for i, correo in enumerate(lote_correos):
            # Uso de .get() blindado contra KeyErrors
            textos_correos += f"""
                --- CORREO ID: {i} ---
                REMITENTE: {correo.get('de', 'Desconocido')}
                ASUNTO: {correo.get('asunto', 'Sin Asunto')}
                CUERPO: {correo.get('cuerpo', '')[:600]}
                """
        prompt = f"""
                    Eres un asistente de clasificación. Analiza estos {len(lote_correos)} correos.
                    Devuelve ÚNICAMENTE un ARRAY JSON (lista) con exactamente {len(lote_correos)} objetos.

                    {textos_correos}

                    CRITERIOS:
                    - requiere_accion = true solo si solicitan una respuesta, entrega, pago, o acción concreta.
                    - urgencia = alta si mencionan plazos, fechas cercanas, o "urgente".
                    - spam si es newsletter, marketing, o notificación automática.

                    Formato estricto:
                    [
                        {{
                            "requiere_accion": true/false,  // ¿El usuario debe hacer algo?
                            "categoria": "laboral" | "academico" | "salud" |"financiero" | "personal" | "spam",
                            "urgencia": "alta" | "media" | "baja",
                            "resumen_corto": "Una línea de máximo 60 caracteres"
                        }}
                    ]
                """
       
        # INTENTAR HASTA 3 VECES SI GOOGLE NOS BLOQUEA
        max_retries = 3
        for intento in range(max_retries):
            try:
                from google.genai import types
                
                response = gemini_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1
                    )
                )
                resultados = json.loads(response.text)
                if len(resultados) == len(lote_correos):
                    return sorted(resultados, key=lambda x: x.get('id_correo', 0))
                else:
                    raise ValueError("La IA omitió correos en su respuesta.")
                
            
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    print(f"⚠️ Cuota IA Rápida excedida. Pausando 35s ({intento+1}/{max_retries})...")
                    import asyncio
                    await asyncio.sleep(35)
                else:
                    print(f"⚠️ Error formato IA. Reintentando en 5s...")
                    import asyncio
                    await asyncio.sleep(5)

        # Fallback Obligatorio en Lista (Protege el sistema de colapsos)
        return [{
            'id_correo': i, 'requiere_accion': False, 'categoria': 'personal', 
            'urgencia': 'baja', 'resumen_corto': 'Error al clasificar (Cuota/API)'
        } for i in range(len(lote_correos))]

    
    # ================================================================
    # MODIFICAR LA FUNCIÓN `analizar_profundo` (REEMPLAZAR LA EXISTENTE)
    # ================================================================
    
    async def analizar_profundo(self, lote_datos: List[Dict], gemini_client) -> List[Dict]:
    
        """
        Análisis completo con contexto histórico MEJORADO en lotes pequeños.
        
        Args:
            lote_datos: Lista con formato [{'id_correo': i, 'correo': {...}, 'historial_remitente': [...], 'contexto_adicional': {...}}]
        """

        if not lote_datos: return []

        textos_prompt = ""
        # 🔥 TU LÓGICA ORIGINAL EXACTA SE EJECUTA PARA CADA CORREO DEL LOTE
        for item in lote_datos:
            correo = item['correo']
            historial_remitente = item['historial_remitente']
            contexto_adicional = item['contexto_adicional']
        
            # Construir contexto histórico enriquecido
            contexto_hist = ""
            
            if contexto_adicional.get('es_primer_contacto'):
                contexto_hist = "⚠️ PRIMER CORREO de este remitente. Usar tono neutro-profesional.\n\n"
            else:
                contexto_hist = f"""
    📊 HISTORIAL CON ESTE REMITENTE:
    - Total de correos previos: {contexto_adicional.get('total_correos', 0)}
    - Último contacto: {contexto_adicional.get('ultimo_contacto', 'N/A')}
    - Tono habitual: {contexto_adicional.get('tono_habitual', 'desconocido')}
    - Tema principal: {contexto_adicional.get('tema_principal', 'general')}

    RESPUESTAS ANTERIORES (para mantener consistencia):
    """
                # Agregar ejemplos de respuestas previas
                respuestas_previas = contexto_adicional.get('respuestas_anteriores', [])
                if respuestas_previas:
                    for i, resp in enumerate(respuestas_previas[:2], 1):
                        contexto_hist += f"{i}. {resp[:200]}...\n"
                else:
                    contexto_hist += "(No hay respuestas previas registradas)\n"
            
            # Agregar resumen de últimos correos
            if historial_remitente:
                contexto_hist += "\n📧 ÚLTIMOS CORREOS:\n"
                for h in historial_remitente[-3:]:
                    contexto_hist += f"- [{h.get('fecha', 'N/A')}] {h.get('asunto', 'Sin asunto')}\n"

            # --- [INSERCIÓN QUIRÚRGICA: PASO 2 (Inyectar al Prompt)] ---
            obs_c = contexto_adicional.get('obs_contacto_txt', '')
            obs_u = contexto_adicional.get('obs_usuario_txt', '')
            if obs_c or obs_u:
                contexto_hist += f"\n{obs_c}\n{obs_u}\n"
            # -----------------------------------------------------------

        # Acumular el texto para este correo en el súper-prompt
            textos_prompt += f"""
                --- ID_CORREO: {item['id_correo']} ---
                {contexto_hist}

                CORREO ACTUAL:
                De: {correo.get('de', 'Desconocido')}
                Asunto: {correo.get('asunto', 'Sin asunto')}
                Fecha: {correo.get('fecha', 'N/A')}
                Cuerpo:
                {correo.get('cuerpo', '')[:800]}
                """
        # Prompt mejorado con contexto
        prompt = f"""
        Actúa como asistente personal experto analizando un correo CRÍTICO.

        {contexto_hist}

        {textos_prompt}

        ANÁLISIS REQUERIDO PARA CADA CORREO:

            1. RESPUESTA SUGERIDA: Redacta un borrador profesional considerando:
            - El tono usado en correos anteriores con este remitente
            - La urgencia y contexto actual
            - Mantener CONSISTENCIA con respuestas previas
            - Que sea conciso pero completo (máximo 200 palabras)

            2. ACCIONES PENDIENTES: Lista específica de lo que el usuario debe hacer.

            3. FECHA LÍMITE: Si hay deadline, extrae la fecha en formato ISO (YYYY-MM-DD).

            4. TONO DETECTADO: Formal, informal, urgente, amigable, etc.

        Responde SOLO con este ARRAY JSON de {len(lote_datos)} objetos:
        [
            {{
                "id_correo": 0,
                "respuesta_sugerida": "Estimado/a...",
                "tono_detectado": "formal" | "informal" | "urgente",
                "acciones_pendientes": ["Acción 1", "Acción 2"],
                "fecha_limite": "2026-01-20" | null,
                "prioridad_final": 80-100,
                "contexto_adicional": "Notas relevantes del historial",
                "cambio_tono": false  // true si el tono cambió respecto al habitual
            }}
        ]
        """
        
        try:
            from google.genai import types
            
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2)
                       
            )
            resultados = json.loads(response.text)
            return sorted(resultados, key=lambda x: x.get('id_correo', 0))
        
        except Exception as e:
            print(f"Error en análisis profundo: {e}")
            return [{
                'id_correo': item['id_correo'], 'respuesta_sugerida': 'Error generando respuesta', 
                'tono_detectado': 'neutro', 'acciones_pendientes': [], 'fecha_limite': None, 
                'prioridad_final': 50, 'contexto_adicional': 'Error', 'cambio_tono': False
            } for item in lote_datos]
    
    # ================================================================
    # AGREGAR ESTA FUNCIÓN A TU analizador_correos.py (Doc 22)
    # Insértala ANTES de la función `procesar_lote_correos`
    # ================================================================

    async def obtener_contexto_remitente(
        self,
        correos: List[Dict],
        usuario_id: str,
        remitente: str,
        gemini_client,
        supabase_client,
        nombre_usuario: str = "",
        cuenta_gmail_id: str = None  # 🔥 AGREGAR ESTA LÍNEA
    ) -> Dict:
        """
        Obtiene el historial de interacción con un remitente específico.
        Esto permite aprender el tono y estilo de comunicación.
        
        Args:
            usuario_id: ID del usuario en Supabase
            remitente: Email del remitente
            supabase_client: Cliente de Supabase
        
        Returns:
            {
                'total_correos': int,
                'ultimo_contacto': str (fecha),
                'tono_habitual': str,
                'respuestas_anteriores': [str],
                'temas_frecuentes': [str]
            }
        """
        try:
            # 1. Obtener últimos 5 correos con este remitente
            historial = supabase_client.table('correos_analizados')\
                .select('*')\
                .eq('usuario_id', usuario_id)\
                .eq('remitente', remitente)\
                .order('fecha', desc=True)\
                .limit(5)\
                .execute()
            
            if not historial.data:
                return {
                    'total_correos': 0,
                    'es_primer_contacto': True,
                    'tono_habitual': 'desconocido',
                    'respuestas_anteriores': [],
                    'temas_frecuentes': []
                }
            
            # 2. Analizar el historial
            total = len(historial.data)
            
            # Extraer tonos detectados previamente
            tonos = [h.get('tono_detectado', 'neutro') for h in historial.data]
            tono_mas_comun = max(set(tonos), key=tonos.count)
            
            # Extraer respuestas enviadas (si las hay)
            respuestas = []
            for h in historial.data:
                if h.get('respondido'):
                    # Aquí asumimos que guardas la respuesta enviada en metadata
                    resp = h.get('metadata', {}).get('respuesta_enviada')
                    if resp:
                        respuestas.append(resp)
            
            # Extraer temas (de las categorías)
            categorias = [h.get('categoria', 'personal') for h in historial.data]
            tema_principal = max(set(categorias), key=categorias.count)
            
            # Último contacto
            ultimo = historial.data[0].get('fecha', 'Desconocido')
            
            # --- [INSERCIÓN QUIRÚRGICA: PASO 1 (Cargar Observaciones)] ---
            obs_contacto_txt = ""
            obs_usuario_txt  = ""
            try:
                # Nota: usamos 'numero_telefonico' como identificador del remitente según tu esquema actual
                obs_res = supabase_client.table('patron_observaciones')\
                    .select('dimension, observacion, sujeto, peso')\
                    .eq('usuario_id', usuario_id)\
                    .eq('numero_telefonico', remitente)\
                    .gte('peso', 0.55)\
                    .order('peso', desc=True)\
                    .limit(10)\
                    .execute()

                if obs_res.data:
                    obs_contacto = [o for o in obs_res.data if o['sujeto'] == 'contacto']
                    obs_usuario  = [o for o in obs_res.data if o['sujeto'] == 'usuario']

                    if obs_contacto:
                        obs_contacto_txt = "PERFIL DEL CONTACTO (aprendido):\n" + "\n".join(
                            f"- [{o['dimension']}] {o['observacion']}"
                            for o in obs_contacto[:5]
                        )
                    if obs_usuario:
                        obs_usuario_txt = "CÓMO EL USUARIO RESPONDE A ESTE CONTACTO:\n" + "\n".join(
                            f"- [{o['dimension']}] {o['observacion']}"
                            for o in obs_usuario[:5]
                        )
            except Exception as e_obs:
                print(f"⚠️ No se cargaron observaciones para {remitente}: {e_obs}")

            return {
                'total_correos': total,
                'es_primer_contacto': False,
                'ultimo_contacto': ultimo,
                'tono_habitual': tono_mas_comun,
                'respuestas_anteriores': respuestas[-2:],  # Últimas 2
                'tema_principal': tema_principal,
                'historial_completo': historial.data,  # Por si se necesita
                'obs_contacto_txt':     obs_contacto_txt,   # NUEVO
                'obs_usuario_txt':      obs_usuario_txt,    # NUEVO
            }
        
        except Exception as e:
            print(f"Error obteniendo contexto de remitente: {e}")
            return {
                'total_correos': 0,
                'es_primer_contacto': True,
                'tono_habitual': 'desconocido',
                'respuestas_anteriores': [],
                'temas_frecuentes': [],
                'obs_contacto_txt': "",
                'obs_usuario_txt': "",
            }

    # ================================================================
    # ORQUESTADOR PRINCIPAL
    # ================================================================
    
    async def procesar_lote_correos(
        self,
        correos: List[Dict],
        usuario_id: str,
        gemini_client,
        supabase_client,
        nombre_usuario: str = "",
        cuenta_gmail_id: str = None  # 👈 ¡ESTA ES LA LÍNEA NUEVA QUE FALTABA!
    ) -> Dict:
        
        """
        Procesa un lote de correos de forma eficiente.
        
        Returns:
            {
                'procesados': int,
                'spam_descartado': int,
                'requieren_accion': int,
                'correos_criticos': [...]
            }
        """
        estadisticas = {
            'procesados': 0,
            'spam_descartado': 0,
            'accion_baja': 0,
            'accion_media': 0,
            'accion_alta': 0
        }
        
        correos_criticos = []

        # --- PASO 0: FILTRAR DUPLICADOS (AHORRO DE CUOTA) ---
        # Sacamos los IDs de Gmail de la lista que acabamos de bajar
        ids_gmail_entrantes = [c['id'] for c in correos]
        
        # Preguntamos a Supabase: "¿Cuáles de estos IDs ya tienes?"
        # Nota: Asumimos que guardaste el ID de gmail en metadata->>correo_id_gmail
        if ids_gmail_entrantes:
            ya_existen = supabase_client.table('correos_analizados')\
                .select('metadata')\
                .eq('usuario_id', usuario_id)\
                .filter('metadata->>correo_id_gmail', 'in', f'({",".join(ids_gmail_entrantes)})')\
                .execute()
            
            # Crear set de IDs existentes para búsqueda rápida
            ids_existentes = {fila['metadata']['correo_id_gmail'] for fila in ya_existen.data if fila.get('metadata')}
            
            # Filtrar: Nos quedamos SOLO con los que NO existen
            correos_nuevos = [c for c in correos if c['id'] not in ids_existentes]
            
            print(f"📉 Filtro de duplicados: {len(correos)} entrantes -> {len(correos_nuevos)} nuevos reales.")
            correos = correos_nuevos # Reemplazamos la lista
            
            if not correos:
                return {'procesados': 0, 'mensaje': 'No hay correos nuevos'}

        # --- 2. FILTRO LOCAL (Sin cambios) ---
        correos_para_ia = []
        for correo in correos:
            estadisticas['procesados'] += 1

            # ✂️ OPTIMIZACIÓN VITAL DE RAM: Recortar antes de guardar en memoria
            cuerpo_correo = correo.get('cuerpo', '')
            correo['cuerpo'] = cuerpo_correo[:3000]
            
            # Si tienes cuerpo_html, recórtalo drásticamente (el HTML pesa muchísimo)
            if 'cuerpo_html' in correo and correo['cuerpo_html']:
                correo['cuerpo_html'] = correo['cuerpo_html'][:3000]

            if self.es_spam_obvio(correo) or self.calcular_score_inicial(correo, nombre_usuario) < 30:
                estadisticas['spam_descartado'] += 1
            else:
                correos_para_ia.append(correo)

        if not correos_para_ia:
            return {**estadisticas, 'correos_criticos': []}

        # --- 3. BATCH IA RÁPIDA (De 10 en 10) ---
        correos_urgentes_pendientes = []
        # 👉 AQUÍ DICE QUE CORTE DE 10 EN 10:
        for i in range(0, len(correos_para_ia), 10):
            lote_actual = correos_para_ia[i : i + 10]
            
            resultados_rapidos = await self.clasificar_con_ia_rapida(lote_actual, gemini_client)
            
            for idx, res_ia in enumerate(resultados_rapidos):
                correo_orig = lote_actual[idx]
                correo_orig.update(res_ia)
                
                if res_ia.get('categoria') == 'spam' or not res_ia.get('requiere_accion'):
                    estadisticas['accion_baja'] += 1
                elif res_ia.get('urgencia') == 'alta' or self.calcular_score_inicial(correo_orig, nombre_usuario) > 70:
                    correos_urgentes_pendientes.append(correo_orig)
                else:
                    estadisticas['accion_media'] += 1
            
            import asyncio
            await asyncio.sleep(4) # Válvula de seguridad

        # --- 4. BATCH IA PROFUNDA (De 3 en 3) ---
        # 👉 AQUÍ DICE QUE CORTE DE 3 EN 3:
        for i in range(0, len(correos_urgentes_pendientes), 3):
            lote_prof_actual = correos_urgentes_pendientes[i : i + 3]
            
            datos_para_profundo = []
            for idx_prof, correo_urg in enumerate(lote_prof_actual):
                ctx = await self.obtener_contexto_remitente(
                    correos=correos, usuario_id=usuario_id, remitente=correo_urg['de'], 
                    gemini_client=gemini_client, supabase_client=supabase_client, 
                    nombre_usuario=nombre_usuario, cuenta_gmail_id=cuenta_gmail_id
                )
                datos_para_profundo.append({
                    'id_correo': idx_prof,
                    'correo': correo_urg,
                    'historial_remitente': ctx.get('historial_completo', []),
                    'contexto_adicional': ctx
                })
            
            # Pasamos la lista estructurada a TU función
            resultados_profundos = await self.analizar_profundo(datos_para_profundo, gemini_client)
            
            # Guardado en BD (Tu lógica original intacta)
            for item in datos_para_profundo:
                idx = item['id_correo']
                c_final = item['correo']
                ctx_bd = item['contexto_adicional']
                res_prof = next((x for x in resultados_profundos if x.get('id_correo') == idx), {})
                
                f_limite = res_prof.get('fecha_limite')
                f_limite = str(f_limite) if hasattr(f_limite, 'isoformat') or f_limite else None

                 # Guardar en BD
                datos_bd = {
                    'usuario_id': usuario_id, 'cuenta_gmail_id': cuenta_gmail_id,
                    'remitente': c_final.get('de'), 'asunto': c_final.get('asunto'),
                    'fecha': c_final.get('fecha'), 'score_importancia': self.calcular_score_inicial(c_final, nombre_usuario),
                    'cuerpo_html': c_final.get('cuerpo_html', ''), 'cuerpo_texto': c_final.get('cuerpo', ''),
                    'categoria': c_final.get('categoria', 'personal'), 'urgencia': c_final.get('urgencia', 'alta'),
                    'requiere_accion': True, 'respuesta_sugerida': res_prof.get('respuesta_sugerida', ''),
                    'tono_detectado': res_prof.get('tono_detectado', 'Neutro'),
                    'acciones_pendientes': res_prof.get('acciones_pendientes', []),
                    'fecha_limite': f_limite,
                    'metadata': {
                        'correo_id_gmail': c_final.get('id'), 'thread_id': c_final.get('thread_id'),
                        'contexto': res_prof.get('contexto_adicional'), 'historial_previo': ctx_bd.get('total_correos', 0),
                        'cambio_tono': res_prof.get('cambio_tono', False)
                    }
                }
                supabase_client.table('correos_analizados').insert(datos_bd).execute()
                
                # --- [INSERCIÓN QUIRÚRGICA: Cambio 2 (Observaciones)] ---
                try:
                    
                    remitente_email = c_final.get('de', '')
                    tono            = res_prof.get('tono_detectado', '')
                    urgencia        = c_final.get('urgencia', '')
                    categoria       = c_final.get('categoria', '')

                    tareas_obs = []
                    if tono:
                        tareas_obs.append(('usuario', 'tono_respuesta',
                            f"Roger responde con tono {tono} a {remitente_email}",
                            [c_final.get('asunto','')[:80]], 0.70))
                    if urgencia == 'alta':
                        tareas_obs.append(('contacto', 'nivel_urgencia',
                            f"{remitente_email} envía correos de alta urgencia frecuentemente",
                            [c_final.get('asunto','')[:80]], 0.75))
                    if categoria:
                        tareas_obs.append(('contacto', 'tema_dominante',
                            f"Correos de {remitente_email} son principalmente de categoría {categoria}",
                            [c_final.get('asunto','')[:80]], 0.65))
                    if res_prof.get('cambio_tono'):
                        tareas_obs.append(('contacto', 'variacion_tono',
                            f"{remitente_email} cambió su tono habitual",
                            [c_final.get('asunto','')[:80]], 0.60))

                    for sujeto, dim, obs, ev, peso in tareas_obs:
                        await guardar_observacion(
                            supabase_client, usuario_id, remitente_email,
                            'email', sujeto, dim, obs, ev, peso
                        )
                except Exception as e_obs:
                    print(f"⚠️ Error guardando observaciones correo: {e_obs}")
                # --------------------------------------------------------

                # --- [INSERCIÓN QUIRÚRGICA: Paso 3 ampliado (Alertas)] ---
                if res_prof.get('acciones_pendientes'):
                    try:
                        supabase_client.table('alertas').insert({
                            'usuario_id':  usuario_id,
                            'titulo':      f"📧 {c_final.get('asunto', 'Correo sin asunto')[:60]}",
                            'descripcion': f"De: {c_final.get('de', '')}",
                            'tipo':        'tarea_ia',
                            'prioridad':   'ALTA' if c_final.get('urgencia') == 'alta' else 'MEDIA',
                            'estado':      'pendiente',
                            'etiqueta':    'NEGOCIO',
                            'fecha_limite': f_limite,
                            'metadata': {
                                'origen':            'email_cerebro',
                                'numero_telefonico': c_final.get('de', ''),   # email como ancla
                                'intencion_nativa':  c_final.get('categoria', 'solicitud_documento'),
                                'hora_del_evento':   None,
                                'correo_id':         c_final.get('id'),
                            }
                        }).execute()
                    except Exception as e_alerta:
                        print(f"⚠️ Error creando alerta desde correo: {e_alerta}")
                # ---------------------------------------------------------

                estadisticas['accion_alta'] += 1
                correos_criticos.append({'correo': c_final, 'analisis': res_prof, 'clasificacion': c_final})
                
            import asyncio
            await asyncio.sleep(5)
            # 🧹 LIBERAR RAM DESPUÉS DE CADA LOTE PROFUNDO
            del lote_prof_actual
            del datos_para_profundo
            del resultados_profundos
            gc.collect()

        # 👉 TUS PRINTS ORIGINALES RESTAURADOS AQUÍ:
        print(f"✅ Lote completado. Procesados: {estadisticas['procesados']} | Alta prioridad: {estadisticas['accion_alta']}")

        return {**estadisticas, 'correos_criticos': correos_criticos}
                
                

"""
ANÁLISIS HISTÓRICO DE CORREOS - UNA SOLA VEZ POR CUENTA
"""

async def analizar_historial_gmail_optimizado(
    usuario_id: str,
    email_gmail: str,
    gmail_service,
    gemini_client,
    supabase_client
):
    """
    Analiza el historial completo de Gmail de forma ULTRA OPTIMIZADA.
    - Filtra spam SIN usar IA
    - Agrupa por remitente
    - Analiza patrones estadísticamente
    - Usa IA solo para lo crítico
    """
    print(f"🔍 Iniciando análisis histórico optimizado para {email_gmail}")
    
    try:
        # 1. Verificar si ya se analizó
        check = supabase_client.table('gmail_analisis_historico')\
            .select('completado')\
            .eq('usuario_id', usuario_id)\
            .eq('email_gmail', email_gmail)\
            .execute()
        
        if check.data and check.data[0].get('completado'):
            return {"status": "ya_analizado", "mensaje": "Cuenta previamente analizada"}
        
        # 2. Obtener TODOS los correos
        correos_gmail = gmail_service.obtener_correos_todos(cantidad=500)
        
        if not correos_gmail:
            return {"status": "error", "mensaje": "No se encontraron correos"}
        
        print(f"📬 {len(correos_gmail)} correos descargados")
        
        # 3. FILTRADO PRE-IA (Capa 1)
        analizador = AnalizadorCorreos()
        correos_valor = []
        spam_count = 0
        
        for correo in correos_gmail:
            if analizador.es_spam_obvio(correo):
                spam_count += 1
                continue
            
            score = analizador.calcular_score_inicial(correo)
            if score < 30:
                spam_count += 1
                continue
            
            correos_valor.append(correo)
        
        print(f"🗑️ Descartados {spam_count} correos sin valor")
        print(f"💎 {len(correos_valor)} correos de valor identificados")
        
        # 4. AGRUPACIÓN POR REMITENTE
        correos_por_remitente = {}
        for correo in correos_valor:
            remitente = correo['de']
            if remitente not in correos_por_remitente:
                correos_por_remitente[remitente] = []
            correos_por_remitente[remitente].append(correo)
        
        # 5. ANÁLISIS ESTADÍSTICO (sin IA)
        perfiles_creados = 0
        llamadas_ia = 0
        
        # Solo los 30 remitentes más frecuentes
        remitentes_top = sorted(
            correos_por_remitente.items(),
            key=lambda x: len(x[1]),
            reverse=True
        )[:30]
        
        for remitente, lista_correos in remitentes_top:
            try:
                # Estadísticas automáticas (sin IA)
                total = len(lista_correos)
                
                # Calcular frecuencia
                fechas = [c.get('fecha') for c in lista_correos if c.get('fecha')]
                if len(fechas) > 1:
                    from datetime import datetime
                    try:
                        primera = datetime.fromisoformat(fechas[-1].replace('Z', '+00:00'))
                        ultima = datetime.fromisoformat(fechas[0].replace('Z', '+00:00'))
                        dias_diff = (ultima - primera).days
                        frecuencia = dias_diff / total if total > 1 else 0
                    except:
                        frecuencia = 0
                else:
                    frecuencia = 0
                
                # Hora más común
                horas = []
                for c in lista_correos:
                    try:
                        if c.get('fecha'):
                            dt = datetime.fromisoformat(c['fecha'].replace('Z', '+00:00'))
                            horas.append(dt.hour)
                    except:
                        continue
                
                hora_comun = max(set(horas), key=horas.count) if horas else 12
                
                # Longitud promedio
                longitudes = [len(c.get('cuerpo', '')) for c in lista_correos]
                longitud_prom = sum(longitudes) // len(longitudes) if longitudes else 0
                
                # Palabras clave (las 5 más comunes)
                import re
                from collections import Counter
                
                todas_palabras = []
                for c in lista_correos:
                    texto = (c.get('asunto', '') + ' ' + c.get('cuerpo', '')).lower()
                    palabras = re.findall(r'\b\w{4,}\b', texto)  # Palabras de 4+ letras
                    todas_palabras.extend(palabras)
                
                palabras_comunes = [p for p, _ in Counter(todas_palabras).most_common(5)]
                
                # 🔥 AHORA SÍ USAR IA (pero solo para entender la relación)
                muestra = lista_correos[-3:]  # Últimos 3 correos
                textos_muestra = [
                    f"Asunto: {c['asunto']}\nExtracto: {c['cuerpo'][:200]}"
                    for c in muestra
                ]
                
                prompt = f"""
Analiza estos {len(muestra)} correos del remitente: {remitente}

{chr(10).join(textos_muestra)}

Extrae SOLO:
1. Tono: formal | informal | urgente | amigable
2. Tema: laboral | academico | personal | comercial
3. Importancia (1-10): ¿Qué tan crítico es este contacto?

Responde JSON:
{{
    "tono_habitual": "...",
    "tema_principal": "...",
    "nivel_importancia": 1-10,
    "patron_comunicacion": "Breve descripción (1 línea)"
}}
"""
                
                from google.genai import types
                response = gemini_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.3
                    )
                )
                
                import json
                perfil_ia = json.loads(response.text)
                llamadas_ia += 1

                 # 🔥 PAUSA INTELIGENTE: Más tiempo para evitar límites
                pausa_base = 10  # Segundos base
                pausa_extra = 5 if llamadas_ia % 10 == 0 else 0  # +5s cada 10 llamadas
                time.sleep(pausa_base + pausa_extra)
                print(f"⏳ Pausando {pausa_base + pausa_extra}s (Llamadas IA: {llamadas_ia})")

                # Guardar perfil completo
                supabase_client.table('perfiles_contactos_gmail').insert({
                    'usuario_id': usuario_id,
                    'email_gmail': email_gmail,
                    'remitente': remitente,
                    'nombre_contacto': remitente.split('@')[0],
                    'tipo_relacion': perfil_ia.get('tema_principal', 'personal'),
                    'nivel_importancia': perfil_ia.get('nivel_importancia', 5),
                    'tono_habitual': perfil_ia.get('tono_habitual', 'neutro'),
                    'temas_principales': palabras_comunes,
                    'patron_comunicacion': perfil_ia.get('patron_comunicacion', ''),
                    'total_correos': total,
                    'frecuencia_dias': frecuencia,
                    'hora_comun': hora_comun,
                    'longitud_promedio': longitud_prom,
                    'palabras_clave': palabras_comunes,
                    'primer_contacto': fechas[-1] if fechas else None,
                    'ultimo_contacto': fechas[0] if fechas else None,
                }).execute()
                
                perfiles_creados += 1
                
            except Exception as e:
                print(f"⚠️ Error analizando {remitente}: {e}")
                continue
        
        # 6. Calcular ahorro
        ahorro = ((len(correos_gmail) - llamadas_ia) / len(correos_gmail)) * 100 if correos_gmail else 0
        
        # 7. Marcar como completado
        supabase_client.table('gmail_analisis_historico').upsert({
            'usuario_id': usuario_id,
            'email_gmail': email_gmail,
            'total_correos_analizados': len(correos_gmail),
            'correos_descartados': spam_count,
            'correos_valor': len(correos_valor),
            'remitentes_aprendidos': perfiles_creados,
            'llamadas_ia_usadas': llamadas_ia,
            'ahorro_tokens_porcentaje': round(ahorro, 2),
            'completado': True
        }).execute()
        
        print(f"✅ Análisis completado:")
        print(f"   📊 {perfiles_creados} perfiles creados")
        print(f"   🤖 {llamadas_ia} llamadas IA (ahorro {ahorro:.1f}%)")
        
        return {
            "status": "success",
            "total_correos": len(correos_gmail),
            "spam_descartado": spam_count,
            "correos_valor": len(correos_valor),
            "remitentes_aprendidos": perfiles_creados,
            "llamadas_ia": llamadas_ia,
            "ahorro_porcentaje": round(ahorro, 2),
            "mensaje": f"Análisis completado. {perfiles_creados} contactos aprendidos con {ahorro:.0f}% de ahorro."
        }
    
    except Exception as e:
        print(f"❌ Error en análisis histórico: {e}")
        return {"status": "error", "mensaje": str(e)}
