"""
SERVICIO DE GMAIL CON OAUTH
Conecta con la API de Gmail para leer y enviar correos
"""

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from email.mime.text import MIMEText
import base64
from typing import List, Dict, Optional
import re
from datetime import datetime


class GmailService:
    """
    Servicio para interactuar con Gmail API
    """
    
    def __init__(self, access_token: str):
        """
        Inicializa el servicio con el token de acceso del usuario.
        
        Args:
            access_token: Token OAuth del usuario (viene desde Flutter)
        """
        self.credentials = Credentials(token=access_token)
        self.service = build('gmail', 'v1', credentials=self.credentials)
    
    def obtener_correos_no_leidos(self, cantidad: int = 50) -> List[Dict]:
        """
        Obtiene los últimos correos no leídos.
        
        Returns:
            Lista de correos con estructura:
            [
                {
                    'id': str,
                    'de': str,
                    'asunto': str,
                    'cuerpo': str,
                    'fecha': str (ISO),
                    'etiquetas': [str]
                }
            ]
        """
        try:
            # 1. Listar mensajes no leídos
            resultados = self.service.users().messages().list(
                userId='me',
                q='is:unread',
                maxResults=cantidad
            ).execute()
            
            mensajes = resultados.get('messages', [])
            
            if not mensajes:
                return []
            
            # 2. Obtener detalles de cada mensaje
            correos_procesados = []
            
            for mensaje in mensajes:
                try:
                    msg_detail = self.service.users().messages().get(
                        userId='me',
                        id=mensaje['id'],
                        format='full'
                    ).execute()
                    
                    correo_estructurado = self._parsear_mensaje(msg_detail)
                    if correo_estructurado:
                        correos_procesados.append(correo_estructurado)
                
                except HttpError as e:
                    print(f"Error obteniendo mensaje {mensaje['id']}: {e}")
                    continue
            
            return correos_procesados
        
        except HttpError as error:
            print(f'Error obteniendo correos: {error}')
            return []
    
    def obtener_correos_todos(self, cantidad: int = 500) -> List[Dict]:
        """
        Obtiene TODOS los correos (leídos y no leídos) para análisis histórico.
        Solo se ejecuta UNA VEZ por cuenta.
        """
        try:
            # Sin filtro 'is:unread' - traer todos
            resultados = self.service.users().messages().list(
                userId='me',
                maxResults=cantidad
            ).execute()
            
            mensajes = resultados.get('messages', [])
            
            if not mensajes:
                return []
            
            correos_procesados = []
            
            for mensaje in mensajes:
                try:
                    msg_detail = self.service.users().messages().get(
                        userId='me',
                        id=mensaje['id'],
                        format='full'
                    ).execute()
                    
                    correo_estructurado = self._parsear_mensaje(msg_detail)
                    if correo_estructurado:
                        correos_procesados.append(correo_estructurado)
                
                except HttpError as e:
                    print(f"Error obteniendo mensaje {mensaje['id']}: {e}")
                    continue
            
            return correos_procesados
        
        except HttpError as error:
            print(f'Error obteniendo correos: {error}')
            return []

    def _parsear_mensaje(self, mensaje: Dict) -> Optional[Dict]:
        """
        Convierte un mensaje de Gmail API al formato simplificado.
        """
        try:
            headers = mensaje['payload']['headers']
            
            # Extraer headers importantes
            de = self._obtener_header(headers, 'From')
            asunto = self._obtener_header(headers, 'Subject')
            fecha = self._obtener_header(headers, 'Date')
            
            # Extraer cuerpo
            cuerpo_texto = self._extraer_cuerpo(mensaje['payload'])
            cuerpo_html = self._extraer_cuerpo_html(mensaje['payload'])  # Nueva función
            # Limpiar email del remitente
            de_limpio = self._extraer_email(de)
            
            # Convertir fecha a ISO
            fecha_iso = self._parsear_fecha(fecha)
            
            return {
                'id': mensaje['id'],
                'de': de_limpio,
                'de_completo': de,  # Incluye nombre: "Juan Pérez <juan@example.com>"
                'asunto': asunto,
                'cuerpo': cuerpo_texto,
                'cuerpo_html': cuerpo_html,  # 🔥 NUEVO: Para UI
                'fecha': fecha_iso,
                'etiquetas': mensaje.get('labelIds', []),
                'thread_id': mensaje.get('threadId')
            }
        
        except Exception as e:
            print(f"Error parseando mensaje: {e}")
            return None
    
    # 🔥 FUNCIÓN NUEVA
    def _extraer_cuerpo_html(self, payload: Dict) -> str:
        """Extrae el HTML del correo (con imágenes inline)."""
        
        # 1. Buscar parte HTML
        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/html':
                    if 'data' in part['body']:
                        return self._decodificar_base64(part['body']['data'])
        
        # 2. Fallback: Si solo hay texto plano, convertirlo a HTML básico
        texto = self._extraer_cuerpo(payload)
        return f"<html><body><pre>{texto}</pre></body></html>"


    def _obtener_header(self, headers: List[Dict], nombre: str) -> str:
        """Obtiene el valor de un header específico."""
        for header in headers:
            if header['name'].lower() == nombre.lower():
                return header['value']
        return ''
    
    def _extraer_cuerpo(self, payload: Dict) -> str:
        """
        Extrae el cuerpo del correo (maneja multipart).
        """
        if 'body' in payload and 'data' in payload['body']:
            return self._decodificar_base64(payload['body']['data'])
        
        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/plain':
                    if 'data' in part['body']:
                        return self._decodificar_base64(part['body']['data'])
                
                # Si tiene sub-partes, buscar recursivamente
                if 'parts' in part:
                    cuerpo = self._extraer_cuerpo(part)
                    if cuerpo:
                        return cuerpo
        
        return ''
    
    def _decodificar_base64(self, data: str) -> str:
        """Decodifica el contenido base64url de Gmail."""
        try:
            # Gmail usa base64url (- y _ en lugar de + y /)
            data_bytes = base64.urlsafe_b64decode(data)
            return data_bytes.decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"Error decodificando: {e}")
            return ''
    
    def _extraer_email(self, texto: str) -> str:
        """
        Extrae solo el email de un string como 'Juan Pérez <juan@example.com>'
        """
        match = re.search(r'<(.+?)>', texto)
        if match:
            return match.group(1)
        return texto.strip()
    
    def _parsear_fecha(self, fecha_str: str) -> str:
        """
        Convierte fecha de Gmail al formato ISO.
        Ej: 'Fri, 17 Jan 2026 10:30:00 +0000' → '2026-01-17T10:30:00+00:00'
        """
        try:
            from email.utils import parsedate_to_datetime
            fecha_dt = parsedate_to_datetime(fecha_str)
            return fecha_dt.isoformat()
        except:
            return datetime.now().isoformat()
    
    # ================================================================
    # ENVÍO DE CORREOS
    # ================================================================
    
    def enviar_correo(
        self,
        destinatario: str,
        asunto: str,
        cuerpo: str,
        thread_id: Optional[str] = None
    ) -> bool:
        """
        Envía un correo desde la cuenta del usuario.
        
        Args:
            destinatario: Email del destinatario
            asunto: Asunto del correo
            cuerpo: Cuerpo del mensaje (texto plano)
            thread_id: ID del hilo (para responder en el mismo hilo)
        
        Returns:
            True si se envió correctamente
        """
        try:
            mensaje = MIMEText(cuerpo)
            mensaje['to'] = destinatario
            mensaje['subject'] = asunto
            
            # Codificar mensaje
            raw_mensaje = base64.urlsafe_b64encode(mensaje.as_bytes()).decode('utf-8')
            
            body = {'raw': raw_mensaje}
            
            # Si es respuesta, agregar threadId
            if thread_id:
                body['threadId'] = thread_id
            
            # Enviar
            self.service.users().messages().send(
                userId='me',
                body=body
            ).execute()
            
            print(f"✅ Correo enviado a {destinatario}")
            return True
        
        except HttpError as error:
            print(f'Error enviando correo: {error}')
            return False
    
    def marcar_como_leido(self, mensaje_id: str) -> bool:
        """
        Marca un correo como leído.
        """
        try:
            self.service.users().messages().modify(
                userId='me',
                id=mensaje_id,
                body={'removeLabelIds': ['UNREAD']}
            ).execute()
            return True
        except HttpError as error:
            print(f'Error marcando como leído: {error}')
            return False
