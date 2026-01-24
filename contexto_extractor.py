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
        if not resultado['fecha']:
            try:
                fecha_parseada = date_parser.parse(texto, fuzzy=True, default=ref)
                if fecha_parseada.date() != ref.date():
                    resultado['fecha'] = fecha_parseada.date()
                    print(f"✅ Fecha detectada (dateutil): {resultado['fecha']}")
            except Exception as e:
                print(f"⚠️ Error con dateutil: {e}")
        
        # 4. DETECTAR HORAS
        # 4. DETECTAR HORAS (MEJORADO)
        patrones_hora = [
            r'(\d{1,2})\s+(?:de\s+la\s+)?(mañana|tarde|noche)',  # 2 de la tarde
             # Alternativo: "a las 2 de la tarde"
            r'a\s+las?\s+(\d{1,2})\s+de\s+la\s+(mañana|tarde|noche)',
            r'(\d{1,2})\s*(?::|h)\s*(\d{2})',  # 6:00, 6h00
            r'(\d{1,2})\s*(am|pm)',  # 6am, 3pm
            r'a\s+las?\s+(\d{1,2})',  # a las 6
        ]

        hora_detectada = None
        modificador = None

        for patron in patrones_hora:
            match = re.search(patron, texto_lower)
            if match:
                try:
                    grupos = match.groups()
                    
                    # CASO 1: "2 de la tarde" (grupo 1 = hora, grupo 2 = modificador)
                    if len(grupos) >= 2 and grupos[1] in ['mañana', 'tarde', 'noche']:
                        hora_num = int(grupos[0])
                        modificador = grupos[1]
                        
                        if modificador == 'tarde' and hora_num < 12:
                            hora_num += 12  # "2 de la tarde" = 14:00
                        elif modificador == 'noche' and hora_num < 12:
                            hora_num += 12  # "8 de la noche" = 20:00
                        # "mañana" no se modifica: "6 de la mañana" = 06:00
                        
                        hora_detectada = datetime.strptime(f"{hora_num}:00", "%H:%M").time()
                        print(f"✅ Hora detectada: {hora_detectada} (de la {modificador})")
                        break
                    
                    # CASO 2: Formato "17:00"
                    elif len(grupos) >= 2 and grupos[1].isdigit():
                        hora_num = int(grupos[0])
                        minutos = int(grupos[1])
                        hora_detectada = datetime.strptime(f"{hora_num}:{minutos:02d}", "%H:%M").time()
                        print(f"✅ Hora detectada: {hora_detectada} (formato 24h)")
                        break
                    
                    # CASO 3: "a las 5" (sin modificador, asumir tarde si >=1 y <=6, mañana si >=7)
                    elif len(grupos) == 1:
                        hora_num = int(grupos[0])
                        if 1 <= hora_num <= 6:
                            hora_num += 12  # "a las 5" probablemente = 17:00
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


# ========================================================
# FUNCIONES DE UTILIDAD
# ========================================================

def enriquecer_alerta_con_contexto(titulo: str, descripcion: str) -> Dict:
    """
    Extrae automáticamente fecha, hora, ubicación y MÚLTIPLES acciones.
    VERSIÓN CORREGIDA: Detecta TODAS las tareas mencionadas.
    """
    extractor = ExtractorContexto()
    
    # Texto combinado para analizar
    texto_completo = f"{titulo} {descripcion}"
    texto_lower = texto_completo.lower()
    
    print(f"🔍 Analizando contexto: {texto_completo[:100]}...")
    
    # ========================================================
    # 1. EXTRAER FECHA Y HORA (HORA PRINCIPAL - para el evento)
    # ========================================================
    fecha_hora = extractor.extraer_fecha_hora(texto_completo)
    
    # ========================================================
    # 2. DETECTAR HORA DE LA ALARMA (si es diferente)
    # ========================================================
    hora_alarma = None
    
    # Buscar patrón específico de alarma: "alarma a las X de la Y"
    patron_alarma = r'alarma.*?(\d{1,2})\s+de\s+la\s+(mañana|tarde|noche)'
    match_alarma = re.search(patron_alarma, texto_lower)
    
    if match_alarma:
        hora_num = int(match_alarma.group(1))
        periodo = match_alarma.group(2)
        
        if periodo == 'tarde' and hora_num < 12:
            hora_num += 12
        elif periodo == 'noche' and hora_num < 12:
            hora_num += 12
        
        hora_alarma = datetime.strptime(f"{hora_num}:00", "%H:%M").time()
        print(f"⏰ Hora de alarma detectada: {hora_alarma} ({periodo})")
    
    # ========================================================
    # 3. EXTRAER UBICACIÓN
    # ========================================================
    ubicacion = extractor.extraer_ubicacion(texto_completo)
    
    # ========================================================
    # 4. EXTRAER PERSONAS
    # ========================================================
    personas = extractor.extraer_personas(texto_completo)
    
    # ========================================================
    # 5. 🔥 DETECCIÓN INTELIGENTE DE ACCIONES MÚLTIPLES
    # ========================================================
    acciones_sugeridas = []
    tipo_accion_principal = 'tarea_general'
    
    # A. ALARMA (detectar con múltiples palabras clave)
    if any(palabra in texto_lower for palabra in [
        'alarma', 'despertador', 'recordatorio', 'avísame', 
        'recuérdame', 'notificación'
    ]):
        acciones_sugeridas.append('poner_alarma')
        if 'alarma' in texto_lower or 'despertador' in texto_lower:
            tipo_accion_principal = 'alarma'
        print("🔔 ✅ Acción detectada: ALARMA")
    
    # B. CALENDARIO (evento principal)
    if any(palabra in texto_lower for palabra in [
        'agendar', 'calendario', 'cita', 'reunión', 'evento', 
        'aparta', 'bloquea', 'reserva'
    ]):
        acciones_sugeridas.append('agendar_calendario')
        if tipo_accion_principal == 'tarea_general':
            tipo_accion_principal = 'agendar_calendario'
        print("📅 ✅ Acción detectada: CALENDARIO")
    
    # C. GOOGLE MEET / VIDEOLLAMADA
    if any(palabra in texto_lower for palabra in [
        'meet', 'videollamada', 'zoom', 'teams', 'enlace', 
        'link', 'transmitir', 'compartir', 'video'
    ]):
        acciones_sugeridas.append('crear_meet')
        print("🎥 ✅ Acción detectada: MEET")
    
    # D. UBICACIÓN / MAPA
    if ubicacion and ubicacion.get('direccion'):
        # Verificar que no sea genérica
        dir_lower = ubicacion['direccion'].lower()
        if not any(generico in dir_lower for generico in [
            'instrucción', 'usuario está', 'mensaje', 'clasifícala'
        ]):
            acciones_sugeridas.append('ver_ubicacion')
            print("🗺️ ✅ Acción detectada: MAPA")
    
    # E. LLAMADA
    if personas and any(p.get('telefono') for p in personas):
        acciones_sugeridas.append('llamar')
        print("📞 ✅ Acción detectada: LLAMADA")
    
    # F. WHATSAPP
    if 'whatsapp' in texto_lower or 'wsp' in texto_lower:
        acciones_sugeridas.append('whatsapp')
        print("💬 ✅ Acción detectada: WHATSAPP")
    
    # ========================================================
    # 6. CREAR TIMESTAMP CORRECTO
    # ========================================================
    timestamp_final = None
    
    if fecha_hora and fecha_hora.get('fecha') and fecha_hora.get('hora'):
        try:
            # Combinar fecha y hora
            fecha_str = fecha_hora['fecha']
            hora_str = fecha_hora['hora']
            
            # Si es objeto time, convertir a string
            if hasattr(hora_str, 'strftime'):
                hora_str = hora_str.strftime('%H:%M:%S')
            
            timestamp_final = f"{fecha_str}T{hora_str}"
            print(f"🕐 Timestamp creado: {timestamp_final}")
        except Exception as e:
            print(f"⚠️ Error creando timestamp: {e}")
    
    # ========================================================
    # 7. RETORNAR CONTEXTO ENRIQUECIDO
    # ========================================================
    contexto = {
        'fecha_hora': fecha_hora,
        'hora_alarma': hora_alarma.strftime('%H:%M:%S') if hora_alarma else None,
        'ubicacion': ubicacion,
        'personas': personas,
        'tipo_accion': tipo_accion_principal,
        'acciones_sugeridas': list(set(acciones_sugeridas)),  # Eliminar duplicados
        'completitud': _calcular_completitud(fecha_hora, ubicacion, personas)
    }
    
    print(f"📋 Contexto final: {len(acciones_sugeridas)} acciones detectadas")
    print(f"   Acciones: {acciones_sugeridas}")
    
    return contexto


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
