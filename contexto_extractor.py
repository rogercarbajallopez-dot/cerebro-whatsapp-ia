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
        try:
            fecha_parseada = date_parser.parse(texto, fuzzy=True, default=ref)
            if fecha_parseada.date() != ref.date():  # Solo si encontró algo diferente
                resultado['fecha'] = fecha_parseada.date()
        except:
            pass
        
        # 4. DETECTAR HORAS
        patrones_hora = [
            r'(\d{1,2})\s*(am|pm)',              # 3pm, 10am
            r'(\d{1,2}):(\d{2})\s*(am|pm)?',     # 3:30pm, 15:00
            r'a\s+las?\s+(\d{1,2})',             # a las 3
        ]
        
        for patron in patrones_hora:
            match = re.search(patron, texto_lower)
            if match:
                try:
                    if 'am' in texto_lower or 'pm' in texto_lower:
                        hora_str = match.group(0)
                        hora_obj = datetime.strptime(hora_str.strip(), '%I%p' if ':' not in hora_str else '%I:%M%p')
                    else:
                        hora = int(match.group(1))
                        minutos = int(match.group(2)) if len(match.groups()) > 1 and match.group(2) else 0
                        hora_obj = datetime.strptime(f"{hora}:{minutos}", "%H:%M")
                    
                    resultado['hora'] = hora_obj.time()
                except:
                    pass
        
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
        
        Returns:
            {'direccion': str, 'lugar_nombre': str, 'lat': float, 'lng': float}
        """
        # Patrones comunes en Perú
        patrones_direccion = [
            r'(Av\.|Avenida|Jr\.|Jirón|Calle|Ca\.|Psje\.|Pasaje)\s+[\w\s]+\d+',
            r'(en|a)\s+[A-Z][\w\s]+(Miraflores|San Isidro|Surco|La Molina|Barranco|Lima)',
        ]
        
        ubicacion = {'direccion': None, 'lugar_nombre': None}
        
        for patron in patrones_direccion:
            match = re.search(patron, texto, re.IGNORECASE)
            if match:
                ubicacion['direccion'] = match.group(0).strip()
                break
        
        # Detectar nombres de lugares conocidos
        lugares_conocidos = ['Larcomar', 'Jockey Plaza', 'Real Plaza', 'Open Plaza', 
                            'Clinica', 'Hospital', 'Universidad', 'Municipalidad']
        
        for lugar in lugares_conocidos:
            if lugar.lower() in texto.lower():
                ubicacion['lugar_nombre'] = lugar
                break
        
        return ubicacion if ubicacion['direccion'] or ubicacion['lugar_nombre'] else None

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
    Función principal para usar en el backend.
    
    Args:
        titulo: Título de la alerta
        descripcion: Descripción completa
    
    Returns:
        Dict listo para guardar en columna metadata
    """
    extractor = ExtractorContexto()
    texto_completo = f"{titulo}. {descripcion}"
    
    contexto = extractor.extraer_todo(texto_completo)
    contexto['acciones_sugeridas'] = extractor.determinar_acciones_sugeridas(contexto)
    
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
