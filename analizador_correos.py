"""
SISTEMA DE ANÁLISIS INTELIGENTE DE CORREOS
Reduce costos en 99% usando filtrado en 3 capas
"""

import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pytz
import time

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
            'urgente': ['urgente', 'prioridad', 'inmediato', 'cuanto antes', 'hoy', 'deadline'],
            'laboral': ['entrevista', 'oferta', 'vacante', 'postulación', 'proceso de selección', 'segunda etapa'],
            'academico': ['tarea', 'examen', 'proyecto', 'entrega', 'plazo', 'calificación'],
            'legal': ['contrato', 'firma', 'documento', 'trámite', 'constancia', 'certificado'],
            'financiero': ['factura', 'pago', 'vencimiento', 'cobro', 'transferencia', 'deuda']
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
        
        # -20 si tiene "unsubscribe" (newsletters)
        if 'unsubscribe' in cuerpo or 'darse de baja' in cuerpo:
            score -= 20
        
        return max(0, min(100, score))
    
    # ================================================================
    # CAPA 2: CLASIFICACIÓN RÁPIDA (IA Lite)
    # ================================================================
    
    async def clasificar_con_ia_rapida(self, correo: Dict, gemini_client) -> Dict:
        """
        Usa gemini-2.5-flash (el más barato y rápido) solo para clasificar.
        NO genera respuestas todavía.
        
        Returns:
            {
                'requiere_accion': bool,
                'categoria': str,  # 'laboral', 'academico', 'financiero', 'personal'
                'urgencia': str,   # 'alta', 'media', 'baja'
                'resumen_corto': str  # 1 línea
            }
        """
        prompt = f"""
        Eres un asistente de clasificación de correos. Analiza RÁPIDAMENTE este correo y responde SOLO el JSON.
        
        REMITENTE: {correo['de']}
        ASUNTO: {correo['asunto']}
        CUERPO (primeros 800 caracteres): {correo['cuerpo'][:800]}
        
        Responde SOLO con este JSON:
        {{
            "requiere_accion": true/false,  // ¿El usuario debe hacer algo?
            "categoria": "laboral" | "academico" | "financiero" | "personal" | "spam",
            "urgencia": "alta" | "media" | "baja",
            "resumen_corto": "Una línea de máximo 60 caracteres"
        }}
        
        CRITERIOS:
        - requiere_accion = true solo si solicitan una respuesta, entrega, pago, o acción concreta.
        - urgencia = alta si mencionan plazos, fechas cercanas, o "urgente".
        - spam si es newsletter, marketing, o notificación automática.
        """
        
        try:
            from google.genai import types
            
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",  # El más barato
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1  # Más determinista = más barato
                )
            )
            
            import json
            return json.loads(response.text)
        
        except Exception as e:
            print(f"Error en clasificación rápida: {e}")
            return {
                'requiere_accion': False,
                'categoria': 'personal',
                'urgencia': 'baja',
                'resumen_corto': 'Error al clasificar'
            }
    
    # ================================================================
    # MODIFICAR LA FUNCIÓN `analizar_profundo` (REEMPLAZAR LA EXISTENTE)
    # ================================================================

    async def analizar_profundo(
        self, 
        correo: Dict, 
        historial_remitente: List[Dict],
        gemini_client,
        contexto_adicional: Dict = {}  # 🔥 NUEVO PARÁMETRO
    ) -> Dict:
        """
        Análisis completo con contexto histórico MEJORADO.
        
        Args:
            correo: El correo actual
            historial_remitente: Últimos correos con este remitente
            gemini_client: Cliente Gemini
            contexto_adicional: Contexto obtenido de obtener_contexto_remitente()
        """
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
                for i, resp in enumerate(respuestas_previas, 1):
                    contexto_hist += f"{i}. {resp[:200]}...\n"
            else:
                contexto_hist += "(No hay respuestas previas registradas)\n"
        
        # Agregar resumen de últimos correos
        if historial_remitente:
            contexto_hist += "\n📧 ÚLTIMOS CORREOS:\n"
            for h in historial_remitente[-3:]:
                contexto_hist += f"- [{h.get('fecha', 'N/A')}] {h.get('asunto', 'Sin asunto')}\n"
        
        # Prompt mejorado con contexto
        prompt = f"""
    Actúa como asistente personal experto analizando un correo CRÍTICO.

    {contexto_hist}

    CORREO ACTUAL:
    De: {correo['de']}
    Asunto: {correo['asunto']}
    Fecha: {correo.get('fecha', 'N/A')}
    Cuerpo:
    {correo['cuerpo']}

    ANÁLISIS REQUERIDO:

    1. RESPUESTA SUGERIDA: Redacta un borrador profesional considerando:
    - El tono usado en correos anteriores con este remitente
    - La urgencia y contexto actual
    - Mantener CONSISTENCIA con respuestas previas
    - Que sea conciso pero completo (máximo 200 palabras)

    2. ACCIONES PENDIENTES: Lista específica de lo que el usuario debe hacer.

    3. FECHA LÍMITE: Si hay deadline, extrae la fecha en formato ISO (YYYY-MM-DD).

    4. TONO DETECTADO: Formal, informal, urgente, amigable, etc.

    Responde en JSON:
    {{
        "respuesta_sugerida": "Estimado/a...",
        "tono_detectado": "formal" | "informal" | "urgente",
        "acciones_pendientes": ["Acción 1", "Acción 2"],
        "fecha_limite": "2026-01-20" | null,
        "prioridad_final": 80-100,
        "contexto_adicional": "Notas relevantes del historial",
        "cambio_tono": false  // true si el tono cambió respecto al habitual
    }}
    """
        
        try:
            from google.genai import types
            
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            
            import json
            return json.loads(response.text)
        
        except Exception as e:
            print(f"Error en análisis profundo: {e}")
            return {
                'respuesta_sugerida': 'Error generando respuesta',
                'tono_detectado': 'neutro',
                'acciones_pendientes': [],
                'fecha_limite': None,
                'prioridad_final': 50,
                'cambio_tono': False
            }
    
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
            
            return {
                'total_correos': total,
                'es_primer_contacto': False,
                'ultimo_contacto': ultimo,
                'tono_habitual': tono_mas_comun,
                'respuestas_anteriores': respuestas[-2:],  # Últimas 2
                'tema_principal': tema_principal,
                'historial_completo': historial.data  # Por si se necesita
            }
        
        except Exception as e:
            print(f"Error obteniendo contexto de remitente: {e}")
            return {
                'total_correos': 0,
                'es_primer_contacto': True,
                'tono_habitual': 'desconocido',
                'respuestas_anteriores': [],
                'temas_frecuentes': []
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
        
        for correo in correos:
            time.sleep(5)
            estadisticas['procesados'] += 1
            
            # ============================================
            # CAPA 1: Filtro rápido (sin IA)
            # ============================================
            if self.es_spam_obvio(correo):
                estadisticas['spam_descartado'] += 1
                continue
            
            score = self.calcular_score_inicial(correo, nombre_usuario)
            
            # Si el score es muy bajo, ni siquiera usamos IA
            if score < 30:
                estadisticas['spam_descartado'] += 1
                continue
            
            # ============================================
            # CAPA 2: Clasificación rápida (IA Lite)
            # ============================================
            clasificacion = await self.clasificar_con_ia_rapida(correo, gemini_client)
            
            if clasificacion['categoria'] == 'spam' or not clasificacion['requiere_accion']:
                estadisticas['accion_baja'] += 1
                continue
            
            # ============================================
            # CAPA 3: Análisis profundo (solo críticos)
            # ============================================
            
            # DENTRO DE procesar_lote_correos, REEMPLAZA ESTE BLOQUE:
            # (El que dice "# CAPA 3: Análisis profundo (solo críticos)")

            if clasificacion['urgencia'] == 'alta' or score > 70:
                # 🔥 OBTENER CONTEXTO ENRIQUECIDO
                contexto_remitente = await self.obtener_contexto_remitente(
                    correos=correos,              # 🔥 AGREGAR
                    usuario_id=usuario_id,
                    remitente=correo['de'],
                    gemini_client=gemini_client,  # 🔥 AGREGAR
                    supabase_client=supabase_client,
                    nombre_usuario=nombre_usuario,
                    cuenta_gmail_id=cuenta_gmail_id
                )
                
                # Análisis profundo con contexto
                analisis_completo = await self.analizar_profundo(
                    correo,
                    contexto_remitente.get('historial_completo', []),
                    gemini_client,
                    contexto_adicional=contexto_remitente  # 🔥 PASAR CONTEXTO
                )
                
                # Preparar fecha límite como TEXTO para evitar el error JSON
                f_limite = analisis_completo.get('fecha_limite')
                if hasattr(f_limite, 'isoformat'): # Si es objeto fecha, convertirlo
                    f_limite = f_limite.isoformat()
                elif f_limite is None:
                    f_limite = None
                else:
                    f_limite = str(f_limite) # Asegurar que sea string


                # Guardar en BD
                datos_bd = {
                    'usuario_id': usuario_id,
                    'cuenta_gmail_id': cuenta_gmail_id,  # 🔥 AGREGAR ESTA LÍNEA
                    'remitente': correo['de'],
                    'asunto': correo['asunto'],
                    'fecha': correo.get('fecha'),
                    'score_importancia': score,
                    'categoria': clasificacion['categoria'],
                    'urgencia': clasificacion['urgencia'],
                    'requiere_accion': True,
                    'respuesta_sugerida': analisis_completo['respuesta_sugerida'],
                    'tono_detectado': analisis_completo['tono_detectado'],
                    'acciones_pendientes': analisis_completo['acciones_pendientes'],
                    'fecha_limite': f_limite, # 👈 USAMOS LA VARIABLE YA CONVERTIDA
                    'metadata': {
                        'correo_id_gmail': correo.get('id'),
                        'contexto': analisis_completo.get('contexto_adicional'),
                        'historial_previo': contexto_remitente.get('total_correos', 0),
                        'cambio_tono': analisis_completo.get('cambio_tono', False)
                    }
                }
                
                supabase_client.table('correos_analizados').insert(datos_bd).execute()
                
                correos_criticos.append({
                    'correo': correo,
                    'analisis': analisis_completo,
                    'clasificacion': clasificacion
                })
                
                estadisticas['accion_alta'] += 1
            else:
                estadisticas['accion_media'] += 1
        
        return {
            **estadisticas,
            'correos_criticos': correos_criticos
        }

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

                # 👇 AGREGAR ESTA PAUSA AQUÍ
                print(f"⏳ Pausando 4 segundos para no saturar a la IA...")
                time.sleep(4) 
                # 👆 FIN DE LO AGREGADO
                
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
