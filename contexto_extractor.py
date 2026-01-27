"""
EXTRACTOR DE CONTEXTO PARA ACCIONES INTELIGENTES
Analiza texto y extrae: fechas, lugares, personas, números, emails
"""

import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pytz
import phonenumbers
from dateutil import parser as date_parser

# Zona horaria por defecto (Perú)
TIMEZONE = pytz.timezone('America/Lima')

class ExtractorContexto:
    """
    Extrae información estructurada de texto natural para crear acciones contextuales.
    """
    
    def __init__(self):
        # Patrones de extracción
        self.patrones_telefono = [
            r'\+?51\s?9\d{8}',  # Perú: +51 987654321
            r'9\d{8}',          # Perú corto: 987654321
            r'\d{3}[-.\s]?\d{3}[-.\s]?\d{3}',  # General
        ]
        
        self.patrones_email = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        
        # Palabras clave para tipo de acción
        self.keywords_accion = {
            # 🔥 NUEVO: Prioridad a Alarmas
            'alarma': ['despiértame', 'alarma', 'despertador', 'despertar', 'avísame a las', 'pon una alarma'],
            'reunion_presencial': ['reunión', 'cita', 'junta', 'encuentro', 'visita', 'ir a'],
            'videollamada': ['zoom', 'meet', 'teams', 'videollamada', 'video llamada', 'google meet', 'reunión virtual', 'entrevista virtual'],
            'llamada': ['llamar', 'telefonear', 'contactar por teléfono'],
            'whatsapp': ['whatsapp', 'escribir por wsp', 'mensaje wsp', 'mandar wsp'],
            'email': ['email', 'correo', 'enviar mail', 'mandar correo'],
            'pago': ['pagar', 'yapear', 'transferir', 'plin', 'depositar']
        }

    def extraer_todo(self, texto: str, fecha_referencia: datetime = None) -> Dict:
        """
        Función principal que extrae TODOS los datos contextuales.
        
        Args:
            texto: Texto de la conversación o tarea
            fecha_referencia: Fecha actual (default: ahora en Lima)
        
        Returns:
            Dict con toda la información estructurada
        """
        if not fecha_referencia:
            fecha_referencia = datetime.now(TIMEZONE)
        
        return {
            'fecha_hora': self.extraer_fecha_hora(texto, fecha_referencia),
            'ubicacion': self.extraer_ubicacion(texto),
            'personas': self.extraer_personas(texto),
            'tipo_accion': self.detectar_tipo_accion(texto),
            'detalles': self.extraer_detalles(texto),
            'acciones_sugeridas': [],  # Se calcula después
            'mensaje_sugerido': None,   # Se genera con IA después
            'completitud': self.calcular_completitud(texto)
        }

    def extraer_fecha_hora(self, texto: str, ref: datetime) -> Optional[Dict]:
        """
        Extrae fechas y horas del texto usando lógica inteligente.
        
        Ejemplos:
        - "mañana a las 3pm" → 2026-01-15 15:00:00
        - "el viernes 10am" → próximo viernes a las 10:00
        - "15 de enero" → 2026-01-15 (sin hora)
        """
        usar_dateutil = len(texto) < 100  # Aumentado de 50 a 150
        texto_lower = texto.lower()
        resultado = {'fecha': None, 'hora': None, 'timestamp': None}
        
        # 1. DETECTAR REFERENCIAS RELATIVAS
        if 'hoy' in texto_lower:
            resultado['fecha'] = ref.date()
        elif 'mañana' in texto_lower or 'mañana' in texto_lower:
            resultado['fecha'] = (ref + timedelta(days=1)).date()
        elif 'pasado mañana' in texto_lower:
            resultado['fecha'] = (ref + timedelta(days=2)).date()
        
        # 2. DETECTAR DÍAS DE LA SEMANA
        dias_semana = {
            'lunes': 0, 'martes': 1, 'miércoles': 2, 'miercoles': 2,
            'jueves': 3, 'viernes': 4, 'sábado': 5, 'sabado': 5, 'domingo': 6
        }
        
        for dia_nombre, dia_num in dias_semana.items():
            if dia_nombre in texto_lower:
                dias_adelante = (dia_num - ref.weekday()) % 7
                if dias_adelante == 0:
                    dias_adelante = 7  # Si es el mismo día, asumimos la próxima semana
                resultado['fecha'] = (ref + timedelta(days=dias_adelante)).date()
                break
        
        # 3. DETECTAR FECHAS EXACTAS (15/01, 15 de enero, etc.)
        # 3. DETECTAR FECHAS EXACTAS (MEJORADO)
        # Patrones específicos para fechas completas
        patrones_fecha_completa = [
            r'(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s+del?\s+(\d{4})',  # 31 de enero del 2026
            r'(\d{1,2})/(\d{1,2})/(\d{4})',  # 31/01/2026
            r'(\d{4})-(\d{1,2})-(\d{1,2})',  # 2026-01-31
        ]

        meses_es = {
            'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
            'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
            'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12
        }

        # Intentar con regex primero
        for patron in patrones_fecha_completa:
            match = re.search(patron, texto_lower)
            if match:
                try:
                    if 'de' in patron:  # Formato: "31 de enero del 2026"
                        dia = int(match.group(1))
                        mes_nombre = match.group(2)
                        año = int(match.group(3))
                        mes = meses_es[mes_nombre]
                        resultado['fecha'] = datetime(año, mes, dia).date()
                        print(f"✅ Fecha detectada (formato texto): {resultado['fecha']}")
                        break
                    elif '/' in patron:  # Formato: "31/01/2026"
                        dia = int(match.group(1))
                        mes = int(match.group(2))
                        año = int(match.group(3))
                        resultado['fecha'] = datetime(año, mes, dia).date()
                        print(f"✅ Fecha detectada (formato barra): {resultado['fecha']}")
                        break
                except Exception as e:
                    print(f"⚠️ Error parseando fecha con regex: {e}")
                    continue

        # Si no se encontró con regex, intentar con dateutil
        # Si no se encontró con regex, intentar con dateutil SOLO si el texto es corto
        if not resultado['fecha']:
            try:
                # 🔥 CORRECCIÓN: Solo usar dateutil si el texto es menor a 50 caracteres
                if len(texto) < 50:
                    fecha_parseada = date_parser.parse(texto, fuzzy=True, default=ref)
                    if fecha_parseada.date() != ref.date():
                        resultado['fecha'] = fecha_parseada.date()
                        print(f"✅ Fecha detectada (dateutil): {resultado['fecha']}")
                else:
                    print(f"⚠️ Texto muy largo para dateutil ({len(texto)} chars), usando solo regex")
            except Exception as e:
                print(f"⚠️ Error con dateutil: {e}")
        
        # 4. DETECTAR HORAS
        # 4. DETECTAR HORAS (CORREGIDO - Prioridad a contexto)
        patrones_hora = [
            # 🔥 PRIORIDAD 1: "X de la tarde/mañana/noche" (MÁS ESPECÍFICO)
            (r'(\d{1,2})\s+de\s+la\s+(mañana|tarde|noche)', 'contextual'),
            (r'a\s+las?\s+(\d{1,2})\s+de\s+la\s+(mañana|tarde|noche)', 'contextual'),
            
            # PRIORIDAD 2: Formato 24h (ej: "17:00")
            (r'(\d{1,2}):(\d{2})', '24h'),
            
            # PRIORIDAD 3: AM/PM
            (r'(\d{1,2})\s*(am|pm)', 'ampm'),
            
            # PRIORIDAD 4: "a las X" sin contexto
            (r'a\s+las?\s+(\d{1,2})', 'simple'),
        ]

        hora_detectada = None
        modificador = None

        for patron, tipo in patrones_hora:
            match = re.search(patron, texto_lower)
            if match:
                try:
                    grupos = match.groups()
                    
                    # CASO 1: "5 de la tarde" (CONTEXTO: mañana/tarde/noche)
                    if tipo == 'contextual' and len(grupos) >= 2:
                        hora_num = int(grupos[0])
                        modificador = grupos[1]
                        
                        if modificador == 'tarde' and hora_num < 12:
                            hora_num += 12  # "5 de la tarde" = 17:00
                        elif modificador == 'noche' and hora_num < 12:
                            hora_num += 12  # "8 de la noche" = 20:00
                        # "mañana" no se modifica: "6 de la mañana" = 06:00
                        
                        hora_detectada = datetime.strptime(f"{hora_num}:00", "%H:%M").time()
                        print(f"✅ Hora detectada: {hora_detectada} (de la {modificador})")
                        break
                    
                    # CASO 2: Formato "17:00" (solo si NO se detectó contexto antes)
                    elif tipo == '24h' and len(grupos) >= 2:
                        hora_num = int(grupos[0])
                        minutos = int(grupos[1])
                        hora_detectada = datetime.strptime(f"{hora_num}:{minutos:02d}", "%H:%M").time()
                        print(f"✅ Hora detectada: {hora_detectada} (formato 24h)")
                        break
                    
                    # CASO 3: AM/PM
                    elif tipo == 'ampm' and len(grupos) >= 2:
                        hora_num = int(grupos[0])
                        periodo = grupos[1]
                        if periodo == 'pm' and hora_num < 12:
                            hora_num += 12
                        hora_detectada = datetime.strptime(f"{hora_num}:00", "%H:%M").time()
                        print(f"✅ Hora detectada: {hora_detectada} ({periodo})")
                        break
                    
                    # CASO 4: "a las 5" (inferir contexto)
                    elif tipo == 'simple' and len(grupos) == 1:
                        hora_num = int(grupos[0])
                        # Heurística: 1-6 = tarde, 7-12 = mañana
                        if 1 <= hora_num <= 6:
                            hora_num += 12
                        hora_detectada = datetime.strptime(f"{hora_num}:00", "%H:%M").time()
                        print(f"✅ Hora detectada: {hora_detectada} (inferida)")
                        break
                        
                except Exception as e:
                    print(f"⚠️ Error parseando hora: {e}")
                    continue

        if hora_detectada:
            resultado['hora'] = hora_detectada
        
        # 5. COMBINAR FECHA Y HORA EN TIMESTAMP
        if resultado['fecha']:
            if resultado['hora']:
                resultado['timestamp'] = datetime.combine(
                    resultado['fecha'], 
                    resultado['hora']
                ).replace(tzinfo=TIMEZONE).isoformat()
            else:
                # Si solo hay fecha, asignar 9am por defecto
                resultado['timestamp'] = datetime.combine(
                    resultado['fecha'],
                    datetime.strptime("09:00", "%H:%M").time()
                ).replace(tzinfo=TIMEZONE).isoformat()
        
        return resultado if resultado['fecha'] else None

    def extraer_ubicacion(self, texto: str) -> Optional[Dict]:
        """
        Extrae direcciones, lugares y nombres de establecimientos.
        MEJORADO: Captura direcciones completas incluyendo distrito.
        """
        ubicacion = {'direccion': None, 'lugar_nombre': None}
        
        # 🔥 NUEVO: Patrones mejorados para Perú
        patrones_direccion = [
            # Patrón completo: Distrito + Número + Calle
            r'(en\s+)?([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+de\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)\s+(\d{1,5})\s+(Av\.|Avenida|Jr\.|Jirón|Calle|Ca\.|Psje\.|Pasaje)\s+([\w\s]+)',
            # Patrón simple: Av/Jr/Calle + Nombre + Número
            r'(Av\.|Avenida|Jr\.|Jirón|Calle|Ca\.|Psje\.|Pasaje)\s+([\w\s]+?)\s+(\d{1,5})',
            # Patrón con distrito al final
            r'(Av\.|Avenida|Jr\.|Jirón|Calle)\s+([\w\s]+)\d+[,\s]+(Miraflores|San Isidro|Surco|Santiago de Surco|La Molina|Barranco|Lima|Jesús María|Lince|San Miguel|Pueblo Libre|Magdalena|San Borja)',
        ]
        
        for patron in patrones_direccion:
            match = re.search(patron, texto, re.IGNORECASE)
            if match:
                # Reconstruir dirección completa
                grupos = [g for g in match.groups() if g and g.lower() not in ['en', 'a']]
                ubicacion['direccion'] = ' '.join(grupos).strip()
                break
        
        # Si no encontró nada con patrones, buscar menciones de distritos
        if not ubicacion['direccion']:
            distritos_peru = ['Miraflores', 'San Isidro', 'Surco', 'Santiago de Surco', 
                            'La Molina', 'Barranco', 'Jesús María', 'San Miguel',
                            'Pueblo Libre', 'Magdalena', 'San Borja', 'Lince']
            
            for distrito in distritos_peru:
                if distrito.lower() in texto.lower():
                    # Buscar contexto alrededor del distrito
                    patron_contexto = rf'([^.!?]*{distrito}[^.!?]*)'
                    match_ctx = re.search(patron_contexto, texto, re.IGNORECASE)
                    if match_ctx:
                        ubicacion['direccion'] = match_ctx.group(1).strip()
                        break
        
        # Detectar nombres de lugares conocidos
        lugares_conocidos = ['Larcomar', 'Jockey Plaza', 'Real Plaza', 'Open Plaza',
                            'Clínica', 'Hospital', 'Universidad', 'Municipalidad', 
                            'Parque Kennedy', 'Ovalo Gutierrez', 'Estadio Nacional',
                            'Clínica Ricardo Palma', 'Hospital Loayza', 'Hospital Rebagliati']
        lugar_detectado = None
        for lugar in lugares_conocidos:
            if lugar.lower() in texto.lower():
                ubicacion['lugar_nombre'] = lugar
                # Si no hay dirección, usar el nombre del lugar
                if not ubicacion['direccion']:
                    ubicacion['direccion'] = lugar
                break
        # Si NO hay lugar específico, verificar si hay dirección
        if not ubicacion['direccion'] and not lugar_detectado:
            # NO devolver ubicación si solo menciona "hospital" genérico
            return None

        if lugar_detectado:
            ubicacion['lugar_nombre'] = lugar_detectado
            if not ubicacion['direccion']:
                ubicacion['direccion'] = lugar_detectado

        return ubicacion if (ubicacion['direccion'] or ubicacion['lugar_nombre']) else None

    def extraer_personas(self, texto: str) -> List[Dict]:
        """
        Extrae nombres de personas, teléfonos y emails.
        
        Returns:
            [{'nombre': str, 'telefono': str, 'email': str}]
        """
        personas = []
        
        # 1. Extraer nombres (detectar mayúsculas consecutivas)
        nombres_detectados = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b', texto)
        
        # 2. Extraer teléfonos
        telefonos = []
        for patron in self.patrones_telefono:
            matches = re.findall(patron, texto)
            telefonos.extend(matches)
        
        # Validar y normalizar teléfonos
        telefonos_validos = []
        for tel in telefonos:
            try:
                num_parseado = phonenumbers.parse(tel, "PE")
                if phonenumbers.is_valid_number(num_parseado):
                    telefonos_validos.append(phonenumbers.format_number(
                        num_parseado, 
                        phonenumbers.PhoneNumberFormat.E164
                    ))
            except:
                # Si falla el parsing, intentar formato simple
                tel_limpio = re.sub(r'\D', '', tel)
                if len(tel_limpio) >= 9:
                    telefonos_validos.append(f"+51{tel_limpio[-9:]}")
        
        # 3. Extraer emails
        emails = re.findall(self.patrones_email, texto)
        
        # 4. Combinar información
        for i, nombre in enumerate(nombres_detectados):
            personas.append({
                'nombre': nombre,
                'telefono': telefonos_validos[i] if i < len(telefonos_validos) else None,
                'email': emails[i] if i < len(emails) else None
            })
        
        # Si no hay nombres pero sí hay contactos, crear entrada genérica
        if not personas and (telefonos_validos or emails):
            personas.append({
                'nombre': 'Contacto',
                'telefono': telefonos_validos[0] if telefonos_validos else None,
                'email': emails[0] if emails else None
            })
        
        return personas

    def detectar_tipo_accion(self, texto: str) -> str:
        """
        Detecta el tipo de acción basándose en palabras clave.
        
        Returns:
           'alarma' | 'reunion_presencial' | 'videollamada' | 'llamada' | 'whatsapp' | 'email' | 'pago' | 'tarea_general'
        """
        texto_lower = texto.lower()
        
        # Chequear cada tipo en orden de especificidad
        for tipo, keywords in self.keywords_accion.items():
            if any(kw in texto_lower for kw in keywords):
                return tipo
        
        return 'tarea_general'

    def extraer_detalles(self, texto: str) -> Dict:
        """
        Extrae detalles adicionales: tema, duración, recordatorios, etc.
        """
        detalles = {
            'tema': None,
            'duracion_minutos': None,
            'notas': texto[:200]  # Primeros 200 chars como nota
        }
        
        # Detectar duración
        patron_duracion = r'(\d+)\s*(hora|horas|minuto|minutos|hr|hrs|min)'
        match = re.search(patron_duracion, texto.lower())
        if match:
            cantidad = int(match.group(1))
            unidad = match.group(2)
            
            if 'hora' in unidad or 'hr' in unidad:
                detalles['duracion_minutos'] = cantidad * 60
            else:
                detalles['duracion_minutos'] = cantidad
        
        return detalles

    def calcular_completitud(self, texto: str) -> int:
        """
        Calcula qué tan completa está la información (0-10).
        Usado para el score de urgencia.
        """
        puntos = 0
        
        # +3 si tiene fecha
        if self.extraer_fecha_hora(texto, datetime.now(TIMEZONE)):
            puntos += 3
        
        # +3 si tiene ubicación O persona
        if self.extraer_ubicacion(texto):
            puntos += 2
        if self.extraer_personas(texto):
            puntos += 2
        
        # +2 si tiene tipo de acción clara
        if self.detectar_tipo_accion(texto) != 'tarea_general':
            puntos += 2
        
        # +1 si tiene duración
        if 'hora' in texto.lower() or 'minuto' in texto.lower():
            puntos += 1
        
        return min(puntos, 10)

    def determinar_acciones_sugeridas(self, contexto: Dict) -> List[str]:
        """
        Basándose en el contexto extraído, sugiere qué botones mostrar.
        """
        acciones = []
        tipo = contexto.get('tipo_accion', '')
        # 1. 🔥 LÓGICA DE ALARMA VS CALENDARIO
        if tipo == 'alarma' and contexto.get('fecha_hora'):
            # Si es explícitamente una alarma, sugerimos poner alarma
            acciones.append('poner_alarma')
        elif tipo == 'videollamada' and contexto.get('fecha_hora'):
            acciones.append('crear_meet')
            acciones.append('agendar_calendario')
        elif contexto.get('fecha_hora'):
            # Si tiene fecha pero NO es alarma (es reunión, cita, etc), sugerimos calendario
            acciones.append('agendar_calendario')
        
        # Si tiene ubicación → Ver en mapa
        if contexto.get('ubicacion'):
            acciones.append('ver_ubicacion')
        
        # Si tiene personas con teléfono → Llamar / WhatsApp
        if contexto.get('personas'):
            for persona in contexto['personas']:
                if persona.get('telefono'):
                    acciones.append('llamar')
                    if contexto['tipo_accion'] == 'whatsapp':
                        acciones.append('whatsapp')
                    break
                if persona.get('email'):
                    acciones.append('email')
                    break
        
                
        # Si es pago → Abrir Yape
        if contexto['tipo_accion'] == 'pago':
            acciones.append('abrir_yape')
        
        # Eliminar duplicados y limitar a 4 acciones
        return list(dict.fromkeys(acciones))[:4]

def fragmentar_texto_inteligente(texto: str) -> List[Dict]:
    """
    Divide texto largo en fragmentos semánticos (por acciones, no por longitud arbitraria).
    Detecta patrones coloquiales de CUALQUIER usuario.
    
    VERSIÓN CORREGIDA: Fragmentos más pequeños y sin contexto base redundante.
    """
    
    # Patrones de numeración
    patrones_numeracion = [
        r'(?:^|\s)(\d+)[.)\-:]\s*',
        r'(?:primero|segundo|tercero|cuarto|quinto|sexto)[,\s]',
        r'(?:primera|segunda|tercera|cuarta|quinta)[,\s]',
        r'(?:1ro|2do|3ro|4to|5to)[,\s]',
    ]
    
    # Patrones de secuencia
    patrones_secuencia = [
        r'(?:luego|después|entonces|posteriormente)[,\s]',
        r'(?:también|además|aparte)[,\s]',
        r'(?:por último|finalmente|para terminar)[,\s]',
        r'(?:y\s+(?:también|además|luego|después))[,\s]',
    ]
    
    # Patrones de acción
    patrones_accion = [
        r'(?:recuérda|avísa|agend|program|cre|pon)[a-z]*me\s',
        r'(?:quiero|necesito|tengo que)\s',
        r'(?:dame|dime|muestra|busca|abre)\s',
    ]
    
    texto_lower = texto.lower()
    
    # Contar indicadores
    cant_numeracion = sum(1 for p in patrones_numeracion if re.search(p, texto_lower, re.IGNORECASE))
    cant_secuencia = sum(1 for p in patrones_secuencia if re.search(p, texto_lower, re.IGNORECASE))
    cant_acciones = len(re.findall('|'.join(patrones_accion), texto_lower, re.IGNORECASE))
    
    es_multiple = cant_numeracion >= 2 or cant_secuencia >= 2 or cant_acciones >= 3
    
    print(f"🔍 Análisis de fragmentación:")
    print(f"   📊 Numeraciones: {cant_numeracion}")
    print(f"   ⏭️ Secuencias: {cant_secuencia}")
    print(f"   ⚡ Acciones: {cant_acciones}")
    print(f"   {'✅ MÚLTIPLES TAREAS detectadas' if es_multiple else '📌 Tarea única'}")
    
    if not es_multiple:
        return [{
            'texto': texto,
            'tipo_accion': _detectar_tipo_accion_rapida(texto),
            'posicion': 1,
            'es_principal': True
        }]
    
    # ========================================================
    # FRAGMENTACIÓN MEJORADA
    # ========================================================
    
    fragmentos = []
    
    # 1. Extraer contexto base (primera oración antes de las tareas)
    contexto_base = ""
    
    # Buscar el primer indicador de tarea
    primer_match = None
    for patron in patrones_numeracion + patrones_secuencia:
        match = re.search(patron, texto, re.IGNORECASE)
        if match and (not primer_match or match.start() < primer_match.start()):
            primer_match = match
    
    if primer_match:
        contexto_previo = texto[:primer_match.start()].strip()
        # Extraer solo información de fecha/lugar del contexto (primera oración)
        oraciones_contexto = contexto_previo.split('.')
        if oraciones_contexto:
            contexto_base = oraciones_contexto[0].strip()
            # Limitar contexto a 100 caracteres
            if len(contexto_base) > 100:
                # Buscar hasta la primera coma después de fecha
                partes = contexto_base.split(',')
                contexto_base = ','.join(partes[:2])  # Solo las primeras 2 partes
    
    # 2. Dividir texto en segmentos por los indicadores
    patron_division = '|'.join(patrones_numeracion + patrones_secuencia)
    
    # Usar finditer para mantener el texto original
    matches = list(re.finditer(f'({patron_division})', texto, re.IGNORECASE))
    
    if not matches:
        # Si no hay matches, devolver como tarea única
        return [{
            'texto': texto,
            'tipo_accion': _detectar_tipo_accion_rapida(texto),
            'posicion': 1,
            'es_principal': True
        }]
    
    # 3. Construir fragmentos entre cada match
    posicion = 1
    for i, match in enumerate(matches):
        inicio = match.end()  # Después del indicador
        
        # Buscar el final (siguiente indicador o fin del texto)
        if i + 1 < len(matches):
            fin = matches[i + 1].start()
        else:
            fin = len(texto)
        
        fragmento_texto = texto[inicio:fin].strip()
        
        # Filtrar fragmentos muy cortos
        if len(fragmento_texto) < 10:
            continue
        
        # 🔥 CRÍTICO: Combinar SOLO con contexto base reducido (no todo el texto)
        if contexto_base and posicion == 1:
            # Solo el primer fragmento lleva contexto de fecha/lugar
            texto_completo = f"{contexto_base}. {fragmento_texto}"
        else:
            # Fragmentos subsecuentes son independientes
            texto_completo = fragmento_texto
        
        tipo = _detectar_tipo_accion_rapida(fragmento_texto)
        
        fragmentos.append({
            'texto': texto_completo,
            'texto_original': fragmento_texto,
            'tipo_accion': tipo,
            'posicion': posicion,
            'es_principal': posicion == 1
        })
        
        print(f"   {posicion}. [{tipo}] {fragmento_texto[:60]}...")
        
        posicion += 1
    
    if not fragmentos:
        # Fallback: devolver texto completo
        return [{
            'texto': texto,
            'tipo_accion': _detectar_tipo_accion_rapida(texto),
            'posicion': 1,
            'es_principal': True
        }]
    
    print(f"\n✂️ Texto fragmentado en {len(fragmentos)} partes")
    
    return fragmentos


def _detectar_tipo_accion_rapida(texto: str) -> str:
    """
    Detección rápida de tipo de acción sin IA.
    VERSIÓN MEJORADA: Más patrones coloquiales.
    """
    texto_lower = texto.lower()
    
    # Prioridad de detección (más específico primero)
    if any(p in texto_lower for p in ['alarma', 'despierta', 'avisa', 'recordatorio a las', 'despertador', 'avísame a las']):
        return 'alarma'
    
    if any(p in texto_lower for p in ['meet', 'zoom', 'teams', 'videollamada', 'video llamada', 'enlace', 'link de', 'crear el enlace', 'google meet']):
        return 'meet'
    
    if any(p in texto_lower for p in ['calendario', 'agenda', 'cita', 'reunión', 'entrevista', 'aparta', 'bloquea', 'aparta ese espacio']):
        return 'calendario'
    
    if any(p in texto_lower for p in ['mapa', 'ubicación', 'dirección', 'donde esta', 'como llego', 'dame el mapa', 'ubicacion exacta']):
        return 'mapa'
    
    if any(p in texto_lower for p in ['llama', 'teléfono', 'contacta por tel', 'marca al']):
        return 'llamada'
    
    if any(p in texto_lower for p in ['whatsapp', 'wsp', 'mensaje', 'escribe por']):
        return 'whatsapp'
    
    if any(p in texto_lower for p in ['yape', 'paga', 'transfi', 'deposita']):
        return 'pago'
    
    if any(p in texto_lower for p in ['correo', 'email', 'mail', 'envía un correo']):
        return 'email'
    
    return 'general'

def _detectar_tipo_accion_rapida(texto: str) -> str:
    """
    Detección rápida de tipo de acción sin IA.
    Usada para pre-clasificar fragmentos.
    """
    texto_lower = texto.lower()
    
    # Prioridad de detección (más específico primero)
    if any(p in texto_lower for p in ['alarma', 'despierta', 'avisa', 'recordatorio a las']):
        return 'alarma'
    
    if any(p in texto_lower for p in ['meet', 'zoom', 'teams', 'videollamada', 'video llamada', 'enlace', 'link de']):
        return 'meet'
    
    if any(p in texto_lower for p in ['calendario', 'agenda', 'cita', 'reunión', 'entrevista', 'aparta', 'bloquea']):
        return 'calendario'
    
    if any(p in texto_lower for p in ['mapa', 'ubicación', 'dirección', 'donde esta', 'como llego']):
        return 'mapa'
    
    if any(p in texto_lower for p in ['llama', 'teléfono', 'contacta por tel', 'marca al']):
        return 'llamada'
    
    if any(p in texto_lower for p in ['whatsapp', 'wsp', 'mensaje', 'escribe por']):
        return 'whatsapp'
    
    if any(p in texto_lower for p in ['yape', 'paga', 'transfi', 'deposita']):
        return 'pago'
    
    if any(p in texto_lower for p in ['correo', 'email', 'mail', 'envía un correo']):
        return 'email'
    
    return 'general'
# ========================================================
# FUNCIONES DE UTILIDAD
# ========================================================

def enriquecer_alerta_con_contexto(titulo: str, descripcion: str) -> Dict:
    """
    Extrae automáticamente fecha, hora, ubicación y MÚLTIPLES acciones.
    VERSIÓN PROFESIONAL: Adaptable a cualquier estilo de comunicación.
    
    Estrategia:
    1. Limpieza de texto
    2. Fragmentación inteligente (si es necesario)
    3. Análisis por fragmento (fechas, horas, ubicaciones)
    4. Consolidación de resultados
    """
    extractor = ExtractorContexto()
    
    # ========================================================
    # 1. LIMPIEZA DE TEXTO
    # ========================================================
    texto_sucio = f"{titulo} {descripcion}"
    texto_limpio = texto_sucio
    
    if "[Mensaje]" in texto_sucio:
        partes = texto_sucio.split("[Mensaje]")
        if len(partes) > 1:
            texto_limpio = partes[1].strip()
            print("🧹 Texto limpiado: Instrucciones removidas")
    elif "Procesando..." in texto_sucio or "[Instrucción]" in texto_sucio:
        try:
            texto_limpio = re.sub(r'^.*?(?=\[Mensaje\])', '', texto_sucio, flags=re.DOTALL)
        except:
            texto_limpio = texto_sucio
    
    # Mostrar preview
    preview = texto_limpio[:100] + "..." if len(texto_limpio) > 100 else texto_limpio
    print(f"🔍 Analizando: {preview}")
    
    # ========================================================
    # 2. FRAGMENTACIÓN INTELIGENTE
    # ========================================================
    fragmentos = fragmentar_texto_inteligente(texto_limpio)
    
    # ========================================================
    # 3. ANÁLISIS POR FRAGMENTO
    # ========================================================
    
    # Contenedores para resultados consolidados
    fecha_hora_calendario = None  # Para calendario/meet
    fecha_hora_alarma = None      # Para alarma específica
    ubicacion_final = None
    personas_final = None
    acciones_detectadas = []
    link_meet = None
    
    for fragmento in fragmentos:
        texto_frag = fragmento['texto']
        tipo_frag = fragmento['tipo_accion']
        pos = fragmento['posicion']
        
        print(f"\n📌 Fragmento {pos} [{tipo_frag}]:")
        

        if tipo_frag == 'alarma':
            # Buscar patrón específico de alarma
            patron_hora_alarma = r'(\d{1,2})\s+de\s+la\s+(mañana|tarde|noche)'
            match_hora = re.search(patron_hora_alarma, texto_frag.lower())
            
            if match_hora:
                hora_num = int(match_hora.group(1))
                periodo = match_hora.group(2)
                
                if periodo == 'tarde' and hora_num < 12:
                    hora_num += 12
                elif periodo == 'noche' and hora_num < 12:
                    hora_num += 12
                
                hora_manual = datetime.strptime(f"{hora_num}:00", "%H:%M").time()
                print(f"   🕐 Hora alarma detectada manualmente: {hora_manual}")
        # --------------------------------------------------------
        # A. EXTRAER FECHA/HORA DE ESTE FRAGMENTO
        # --------------------------------------------------------
        try:
            fh = extractor.extraer_fecha_hora(
                texto_frag,
                datetime.now(pytz.timezone('America/Lima'))
            )
            
            if fh and fh.get('fecha'):
                print(f"   📅 Fecha: {fh['fecha']}")
                print(f"   🕐 Hora: {fh.get('hora', 'No especificada')}")
                
                # 🔥 LÓGICA CRÍTICA: Asignar según tipo
                if tipo_frag == 'alarma':
                    # Alarma usa su propia fecha/hora
                    fecha_hora_alarma = fh
                    print(f"   ⏰ Asignada a ALARMA")
                
                elif tipo_frag in ['calendario', 'meet', 'general']:
                    # Calendario usa la fecha principal
                    if not fecha_hora_calendario or fragmento['es_principal']:
                        fecha_hora_calendario = fh
                        print(f"   📆 Asignada a CALENDARIO")
        
        except Exception as e:
            print(f"   ⚠️ Error extrayendo fecha: {e}")
        
        # --------------------------------------------------------
        # B. EXTRAER UBICACIÓN (solo una vez)
        # --------------------------------------------------------
        if not ubicacion_final:
            try:
                ubi = extractor.extraer_ubicacion(texto_frag)
                if ubi and ubi.get('direccion'):
                    ubicacion_final = ubi
                    print(f"   📍 Ubicación: {ubi['direccion']}")
            except:
                pass
        
        # --------------------------------------------------------
        # C. EXTRAER PERSONAS (solo una vez)
        # --------------------------------------------------------
        if not personas_final:
            try:
                pers = extractor.extraer_personas(texto_frag)
                if pers:
                    personas_final = pers
                    print(f"   👤 Personas: {len(pers)}")
            except:
                pass
        
        # --------------------------------------------------------
        # D. REGISTRAR ACCIÓN
        # --------------------------------------------------------
        accion_mapeo = {
            'alarma': 'poner_alarma',
            'calendario': 'agendar_calendario',
            'meet': 'crear_meet',
            'mapa': 'ver_ubicacion',
            'llamada': 'llamar',
            'whatsapp': 'whatsapp',
            'email': 'email',
            'pago': 'abrir_yape'
        }
        
        accion_agregar = accion_mapeo.get(tipo_frag)
        if accion_agregar and accion_agregar not in acciones_detectadas:
            acciones_detectadas.append(accion_agregar)
            print(f"   ✅ Acción agregada: {accion_agregar}")
    
    # ========================================================
    # 4. CREAR TIMESTAMPS FINALES
    # ========================================================
    
    # Timestamp para calendario/meet
    if fecha_hora_calendario:
        try:
            f = fecha_hora_calendario['fecha']
            h = fecha_hora_calendario.get('hora')
            if f and h:
                fecha_hora_calendario['timestamp'] = f"{f}T{h}"
        except:
            pass
    
    # Timestamp para alarma (separado)
    timestamp_alarma = None
    hora_alarma_str = None
    
    if fecha_hora_alarma:
        try:
            f_alarm = fecha_hora_alarma['fecha']
            h_alarm = fecha_hora_alarma.get('hora')
            if f_alarm and h_alarm:
                timestamp_alarma = f"{f_alarm}T{h_alarm}"
                hora_alarma_str = h_alarm.strftime('%H:%M:%S') if hasattr(h_alarm, 'strftime') else str(h_alarm)
        except:
            pass
    
    # ========================================================
    # 5. VALIDACIONES Y FALLBACKS
    # ========================================================
    
    # Si no hay ubicación pero se detectó acción de mapa, buscar en texto completo
    if 'ver_ubicacion' in acciones_detectadas and not ubicacion_final:
        try:
            ubicacion_final = extractor.extraer_ubicacion(texto_limpio)
        except:
            pass
    
    # Si ubicación es muy genérica, remover acción de mapa
    if ubicacion_final and ubicacion_final.get('direccion'):
        dir_lower = ubicacion_final['direccion'].lower()
        if dir_lower in ['hospital', 'clínica', 'universidad'] or len(dir_lower) < 10:
            if 'ver_ubicacion' in acciones_detectadas:
                acciones_detectadas.remove('ver_ubicacion')
                print("   ⚠️ Ubicación muy genérica, acción de mapa removida")
    
    # ========================================================
    # 6. CONSTRUIR RESPUESTA FINAL
    # ========================================================
    
    contexto = {
        'fecha_hora': fecha_hora_calendario,
        'hora_alarma': hora_alarma_str,
        'timestamp_alarma': timestamp_alarma,
        'ubicacion': ubicacion_final,
        'personas': personas_final,
        'tipo_accion': 'multiple' if len(acciones_detectadas) > 1 else (acciones_detectadas[0] if acciones_detectadas else 'general'),
        'acciones_sugeridas': acciones_detectadas,
        'completitud': _calcular_completitud(fecha_hora_calendario, ubicacion_final, personas_final),
        'link_meet': link_meet
    }
    
    # ========================================================
    # 7. LOG FINAL
    # ========================================================
    print(f"\n{'='*60}")
    print(f"📋 RESUMEN FINAL:")
    print(f"{'='*60}")
    print(f"✅ Acciones: {acciones_detectadas}")
    print(f"📅 Fecha calendario: {fecha_hora_calendario.get('timestamp') if fecha_hora_calendario else 'N/A'}")
    print(f"⏰ Fecha alarma: {timestamp_alarma or 'N/A'}")
    print(f"📍 Ubicación: {ubicacion_final.get('direccion') if ubicacion_final else 'N/A'}")
    print(f"👥 Personas: {len(personas_final) if personas_final else 0}")
    print(f"{'='*60}\n")
    
    return contexto


def _calcular_completitud(fecha_hora, ubicacion, personas):
    """Función auxiliar para calcular completitud (0-10)"""
    puntos = 0
    if fecha_hora: puntos += 4
    if ubicacion: puntos += 3
    if personas: puntos += 3
    return min(puntos, 10)
# ========================================================
# TESTS (ejecutar con: python contexto_extractor.py)
# ========================================================

if __name__ == "__main__":
    # Casos de prueba
    tests = [
        "Reunión con Carlos Méndez mañana a las 3pm en Av. Larco 1234, Miraflores. Llamar al 987654321 para confirmar.",
        "Despiértame mañana a las 6am para correr",  # 🔥 Nuevo test de Alarma
        "Videollamada con María el viernes 10am por Google Meet",
        "Yapear S/ 150 a Juan (987111222) por alquiler",
        "Llamar a la inmobiliaria (014567890) para consultar depto",
        "Escribir por WhatsApp a Pedro sobre el proyecto"
    ]
    
    extractor = ExtractorContexto()
    
    for i, texto in enumerate(tests, 1):
        print(f"\n{'='*60}")
        print(f"TEST {i}: {texto}")
        print('='*60)
        
        contexto = extractor.extraer_todo(texto)
        acciones = extractor.determinar_acciones_sugeridas(contexto)
        
        print(f"\n📅 Fecha/Hora: {contexto['fecha_hora']}")
        print(f"📍 Ubicación: {contexto['ubicacion']}")
        print(f"👤 Personas: {contexto['personas']}")
        print(f"🎯 Tipo: {contexto['tipo_accion']}")
        print(f"✅ Completitud: {contexto['completitud']}/10")
        print(f"🔘 Acciones sugeridas: {acciones}")
