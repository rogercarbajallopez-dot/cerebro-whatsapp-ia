# ====================================================
# WHATSAPP IA 19.0 - CON AUTENTICACIÓN COMPLETA
# ====================================================
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, status, Body, Request
from fastapi.responses import Response
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from gmail_service import GmailService
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from analizador_correos import AnalizadorCorreos
import gzip
import pytesseract
from PIL import Image
import hashlib
import httpx
import asyncio
# --- FIX PARA CHROMADB EN RENDER/LINUX ---
import sys
__import__('pysqlite3')
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
# -----------------------------------------
from faster_whisper import WhisperModel
import tempfile
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
from fastapi import Header, Request, BackgroundTasks, Form
from itertools import groupby
#import google.generativeai as genai
#from google.generativeai.types import content_types

try:
    # Intentamos importar el SDK moderno de Google
    from google import genai
    from google.genai import types
    GEMINI_DISPONIBLE = True
except ImportError:
    GEMINI_DISPONIBLE = False
    print("⚠️ La librería google-genai no se encontró. Gemini no funcionará.")

from collections.abc import Iterable
from contextlib import asynccontextmanager
import os
import json
import requests
import re
import mimetypes
import spacy
from supabase import create_client, Client
from datetime import datetime, date, time, timedelta, timezone
from dotenv import load_dotenv
from typing import List, Dict, Optional, Any
from pydantic import BaseModel
import jwt  # Se mantiene por compatibilidad con el archivo original
from datetime import datetime, timedelta
import pytz
from contexto_extractor import ExtractorContexto, enriquecer_alerta_con_contexto

# ========== WHISPER CONFIG ==========

# Inicializar Whisper (modelo "base" es balance entre velocidad y precisión)
whisper_model = None

def get_whisper_model():
    global whisper_model
    if whisper_model is None:
        print("📥 Cargando modelo Whisper...")
        whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        print("✅ Modelo Whisper cargado")
    return whisper_model


# 1. CARGA DE SECRETOS
load_dotenv()
API_KEY_GOOGLE = os.getenv('GOOGLE_API_KEY')
APP_PASSWORD = os.getenv('MI_APP_PASSWORD') 
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
SUPABASE_JWT_SECRET = os.getenv('SUPABASE_JWT_SECRET')

MODELO_IA = "gemini-2.5-flash" 
import firebase_admin
from firebase_admin import credentials, messaging

# --- INICIO CONFIGURACIÓN FIREBASE (AGREGAR AQUÍ) ---
if not firebase_admin._apps:
    # 1. Rutas posibles de la llave (Render vs Local)
    ruta_render = "/etc/secrets/serviceAccountKey.json"
    ruta_local = "serviceAccountKey.json" # Asegúrate que este nombre coincida con tu archivo
    
    credencial_final = None
    
    if os.path.exists(ruta_render):
        print("🔒 Usando credenciales seguras de RENDER")
        credencial_final = ruta_render
    elif os.path.exists(ruta_local):
        print("💻 Usando credenciales LOCALES")
        credencial_final = ruta_local
    
    if credencial_final:
        try:
            cred = credentials.Certificate(credencial_final)
            firebase_admin.initialize_app(cred)
            print("✅ Firebase conectado exitosamente")
        except Exception as e:
            print(f"❌ Error crítico conectando Firebase: {e}")
    else:
        print("⚠️ ALERTA: No se encontró serviceAccountKey.json. Sin notificaciones.")

def serializar_universal(obj):
    """Convierte fechas de Python a Texto para que la BD no falle."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, time):
        return obj.strftime("%H:%M:%S")
    if isinstance(obj, timedelta):
        return str(obj)
    if isinstance(obj, dict):
        return {k: serializar_universal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serializar_universal(i) for i in obj]
    return obj
# ==========================================


# Función auxiliar para enviar (Ponerla aquí para que esté disponible globalmente)
def enviar_push(token: str, titulo: str, cuerpo: str, data_extra: dict = None):
    """
    Envía notificación push via Firebase.
    CORREGIDO: Convierte todos los valores a string.
    """
    if not token or not firebase_admin._apps:
        return
    try:
        # 🔥 CORRECCIÓN: Firebase solo acepta strings en data
        data_limpia = {}
        if data_extra:
            for key, value in data_extra.items():
                # Convertir TODO a string
                if isinstance(value, (list, dict)):
                    data_limpia[key] = json.dumps(value)  # JSON como string
                elif value is None:
                    data_limpia[key] = ""
                else:
                    data_limpia[key] = str(value)  # Números, bools, etc
        
        msg = messaging.Message(
            notification=messaging.Notification(
                title=titulo, 
                body=cuerpo
            ),
            data=data_limpia,  # ✅ Ahora todos son strings
            token=token
        )
        
        messaging.send(msg)
        print(f"🚀 Notificación enviada: {titulo[:30]}...")
        
    except Exception as e:
        print(f"❌ Error enviando push: {e}")
# --- FIN CONFIGURACIÓN FIREBASE ---

# ==========================================
# 🔔 NUEVA FUNCIÓN: Busca el token por ti
# ==========================================
def enviar_notificacion_inteligente(usuario_id: str, titulo: str, cuerpo: str):
    # Esta función es para cuando NO tienes el token a mano (ej. desde el Webhook)
    if not firebase_admin._apps: return
    try:
        # Buscamos el token en la base de datos
        response = supabase.table("usuarios").select("fcm_token").eq("id", usuario_id).execute()
        if not response.data or not response.data[0].get("fcm_token"):
            return # No tiene celular vinculado
            
        token_detectado = response.data[0]["fcm_token"]
        
        # Reutilizamos tu función original para hacer el envío
        enviar_push(token_detectado, titulo, cuerpo) 
        
    except Exception as e:
        print(f"Error en envío inteligente: {e}")

# ✅ CREAR CLIENTE GLOBAL (Librería nueva)
gemini_client = None
if GEMINI_DISPONIBLE and API_KEY_GOOGLE:
    try:
        gemini_client = genai.Client(api_key=API_KEY_GOOGLE)
        print("✅ Gemini Client inicializado correctamente")
    except Exception as e:
        print(f"❌ Error creando cliente Gemini: {e}")
        gemini_client = None
else:
    if not GEMINI_DISPONIBLE:
        print("⚠️ Librería google-genai no disponible")
    if not API_KEY_GOOGLE:
        print("⚠️ GOOGLE_API_KEY no configurada")

# Conexión Supabase
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase: CONECTADO")
    except Exception as e:
        print(f"❌ Error Supabase: {e}")

# Variables Globales
nlp = None
api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)

# 🔐 FUNCIÓN PARA VERIFICAR TOKEN (ACTUALIZADA PARA ECC/SUPABASE DIRECTO)
async def obtener_usuario_actual(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)
) -> str:
    """
    Verifica el token usando la API de Supabase directamente.
    Esto funciona tanto para proyectos nuevos (ECC) como antiguos (HS256)
    y es más seguro que la decodificación manual.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere autenticación (Token faltante)"
        )
    
    token = credentials.credentials
    
    try:
        # 1. LÓGICA ORIGINAL (INTACTA): Validamos el token con Supabase Auth
        user_response = supabase.auth.get_user(token)
        
        if not user_response or not user_response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido o expirado"
            )
        
        # Capturamos los datos reales de la sesión validada
        user_id = user_response.user.id
        user_email = user_response.user.email  # Capturamos el email también
        
        # 🔥 NUEVO (Solo agregamos esto): Capturar el teléfono que envía Flutter
        meta = user_response.user.user_metadata or {}
        # Flutter lo manda como 'phone_number' dentro de 'data'
        telefono_user = meta.get('phone_number')

        # ==============================================================================
        # 2. BLOQUE COMPLEMENTARIO (AUTO-SINCRONIZACIÓN)
        # Objetivo: Solucionar el error de "Foreign Key" sin tocar la lógica del token.
        # ==============================================================================
        try:
            # Verificamos silenciosamente si este ID ya tiene su "casillero" en la tabla pública
            # Esto evita el error que tenías al guardar tareas.
            existe_en_db = supabase.table('usuarios').select('id').eq('id', user_id).execute()
            
            # Si la lista está vacía, significa que el usuario existe en Auth pero no en la BD
            if not existe_en_db.data:
                print(f"🔄 Usuario {user_id} validado, pero faltaba en tabla pública. Sincronizando...")
                
                # Lo creamos automáticamente para que no vuelva a fallar
                supabase.table('usuarios').insert({
                    "id": user_id,
                    "email": user_email,
                    "telefono": telefono_user
                    # "created_at": datetime.now().isoformat() # Descomenta si tu tabla requiere fecha manual
                }).execute()
                print("✅ Usuario sincronizado correctamente.")

            # 🔥 AGREGADO NECESARIO: Si el usuario YA EXISTE (Tu caso), actualizamos el teléfono
            else:
                if telefono_user:
                    supabase.table('usuarios').update({
                        "telefono": telefono_user
                    }).eq('id', user_id).execute()

        except Exception as e_sync:
            # Si falla este paso extra, NO bloqueamos el acceso. Solo lo registramos.
            # Así aseguramos que la función principal (autenticar) siempre prevalezca.
            print(f"⚠️ Aviso: La auto-sincronización encontró un detalle: {e_sync}")
        # ==============================================================================

        # 3. RETORNO ORIGINAL
        return user_id
        
    except HTTPException as he:
        # Re-lanzamos las excepciones HTTP tal cual (para no perder los códigos 401)
        raise he
    except Exception as e:
        print(f"⚠️ Error de Auth: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión inválida. Por favor, inicia sesión nuevamente."
        )
        
# Función de verificación legacy (para webhooks públicos)
async def verificar_llave(api_key: str = Depends(api_key_header)):
    if APP_PASSWORD and api_key != APP_PASSWORD:
        pass # Permitir paso si no hay pass configurado
    return api_key

# --- MODELOS DE DATOS ---
class MensajeEntrada(BaseModel):
    mensaje: str
    modo_profundo: bool = False

class ActualizarAlerta(BaseModel):
    estado: Optional[str] = None 
    etiqueta: Optional[str] = None
    titulo: Optional[str] = None       # <--- NUEVO
    descripcion: Optional[str] = None  # <--- NUEVO
    fecha_limite: Optional[str] = None # <--- NUEVO (Para reagendar)
    metadata: Optional[Dict[str, Any]] = None 

# --- FUNCIONES DE SOPORTE ---
def obtener_fecha_contexto():
    """Retorna la fecha y hora actual en Lima/Perú para que la IA se ubique."""
    zona_peru = pytz.timezone('America/Lima')
    ahora = datetime.now(zona_peru)
    return ahora.strftime("%Y-%m-%d %H:%M:%S") + f" (Día: {ahora.strftime('%A')})"

def detectar_mime_real(nombre: str, mime: str) -> str:
    if nombre.endswith('.opus'): return 'audio/ogg'
    return mimetypes.guess_type(nombre)[0] or mime

# ==============================================================================
# ⏰ CRON JOB: ESTRATEGIA "EXECUTIVE ASSISTANT" (6 AM / 6 PM)
# ==============================================================================
scheduler = AsyncIOScheduler()

async def generar_briefing(tipo: str):
    """
    tipo="matutino": Prioridad a lo de HOY (Urgente).
    tipo="nocturno": Prioridad a lo de MAÑANA (Planificación).
    """
    print(f"⏰ Ejecutando Briefing {tipo}...")
    
    # 1. Definir Fechas (Zona Horaria Perú)
    zona_peru = pytz.timezone('America/Lima')
    hoy = datetime.now(zona_peru)
    
    if tipo == "matutino":
        # Filtro: Tareas pendientes para HOY o atrasadas
        filtro_fecha = hoy.strftime("%Y-%m-%d")
        mensaje_intro = "☀️ *Buenos días. Tu Plan de Hoy:*"
    else:
        # Filtro: Tareas para MAÑANA
        manana = hoy + timedelta(days=1)
        filtro_fecha = manana.strftime("%Y-%m-%d")
        mensaje_intro = "🌙 *Cierre del día. Para mañana tienes:*"

    # 2. Consultar Usuarios (Asumiendo que tienes una tabla de usuarios con FCM Token)
    # NOTA: Necesitas guardar el token FCM en tu BD para saber a quién enviar.
    try:
        usuarios = supabase.table('usuarios').select('id, fcm_token').execute()
        
        for usuario in usuarios.data:
            user_id = usuario['id']
            token = usuario.get('fcm_token')
            
            if not token: continue # Si no tiene token, saltamos

            # 3. CONSULTA INTELIGENTE (Matriz Eisenhower en SQL)
            # Traemos tareas pendientes de la fecha objetivo
            res_tareas = supabase.table('alertas')\
                .select('*')\
                .eq('usuario_id', user_id)\
                .eq('estado', 'pendiente')\
                .lte('fecha_limite', filtro_fecha if tipo == 'matutino' else filtro_fecha)\
                .execute() # 'lte' es "menor o igual" para atrapar atrasados en la mañana
            
            tareas = res_tareas.data
            
            if not tareas:
                if tipo == "matutino":
                    cuerpo = "¡No tienes pendientes urgentes! Disfruta tu café. ☕"
                    enviar_push(token, "Resumen Diario", cuerpo)
                continue

            # 4. ALGORITMO DE PRIORIZACIÓN (Python)
            # Ordenamos: 
            #  1ro: Etiquetas Críticas (SALUD, NEGOCIO)
            #  2do: Prioridad (ALTA > MEDIA)
            def puntaje_importancia(t):
                score = 0
                etiqueta = (t.get('etiqueta') or '').upper()
                prioridad = (t.get('prioridad') or '').upper()
                
                # Matriz de Importancia
                if etiqueta in ['SALUD', 'NEGOCIO', 'FAMILIA']: score += 10
                elif etiqueta in ['ESTUDIO']: score += 5
                
                # Matriz de Urgencia
                if prioridad == 'ALTA': score += 5
                elif prioridad == 'MEDIA': score += 2
                
                return score

            # Ordenamos la lista de mayor a menor importancia
            tareas_ordenadas = sorted(tareas, key=puntaje_importancia, reverse=True)
            
            # 5. Generar el Mensaje (Top 3-5 tareas)
            top_tareas = tareas_ordenadas[:5]
            cuerpo = mensaje_intro + "\n"
            
            for t in top_tareas:
                icono = "🔴" if t.get('prioridad') == 'ALTA' else "⚪"
                cuerpo += f"{icono} {t['titulo']} ({t.get('etiqueta', 'General')})\n"
            
            if len(tareas) > 5:
                cuerpo += f"... y {len(tareas)-5} más."

            # 6. ENVIAR NOTIFICACIÓN
            # 'ir_a': 'hoy' o 'manana' sirve para que Flutter abra la pestaña correcta
            enviar_push(
                token, 
                "Asistente IA", 
                cuerpo, 
                data_extra={"ir_a": "hoy" if tipo == "matutino" else "manana"}
            )
            
    except Exception as e:
        print(f"❌ Error en Cron Job: {e}")

# --- LIFESPAN (INICIO) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global nlp
    print("🚀 Iniciando Sistema v19.0 (Con Auth Completa)...")
    
    # --- INICIO SCHEDULER ---
    # 6:00 AM - Morning Briefing
    scheduler.add_job(generar_briefing, CronTrigger(hour=6, minute=0, timezone='America/Lima'), args=["matutino"])
    # 6:00 PM - Evening Planning
    scheduler.add_job(generar_briefing, CronTrigger(hour=18, minute=0, timezone='America/Lima'), args=["nocturno"])
    
    # 2. NUEVA: Tarea de Sincronización de Correos
    scheduler.add_job(
        tarea_programada_global, 
        CronTrigger(hour="7,9,11,14,16,18,20", minute="0", timezone="America/Lima")
    )



    scheduler.start()
    # --- FIN SCHEDULER ---
    
    print("🧠 Cargando modelo de lenguaje...")
    nlp = spacy.load("es_core_news_sm")
    print("✅ NLP Listo")
    yield
    print("👋 Apagando sistema")
    scheduler.shutdown() # No olvides apagarlo al salir

app = FastAPI(title="Cerebro WhatsApp IA", lifespan=lifespan)
# 👇 AGREGA ESTO AQUÍ 👇
@app.get("/")
async def root():
    return {
        "estado": "en linea",
        "mensaje": "Cerebro IA operando correctamente 🤖",
        "version": "v19.0"
    }
# 👆 FIN DEL AGREGADO 👆
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_credentials=True)



# ========== CHROMA DB SETUP (OPTIMIZADO) ==========
# Usamos /tmp, advertencia: se borra al reiniciar el servidor.
chroma_client = chromadb.PersistentClient(
    path="/tmp/chroma_data",
    settings=Settings(anonymized_telemetry=False)
)

collection_mensajes = chroma_client.get_or_create_collection(
    name="mensajes_whatsapp",
    metadata={"description": "Mensajes indexados para Nexus"}
)

# Variable global para el modelo
embedding_model = None

def get_embedding_model():
    global embedding_model
    if embedding_model is None:
        print("📥 Cargando modelo de embeddings (LITE)...")
        # Usamos 'all-MiniLM-L6-v2' que es 5 veces más ligero que el 'paraphrase'
        # Esto es CRÍTICO para que no explote la RAM de Render
        embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        print("✅ Modelo de embeddings cargado")
    return embedding_model

# ======================================================================
# 🧠 LÓGICA DE IA (SIN CAMBIOS)
# ======================================================================

async def clasificar_intencion_portero(mensaje: str) -> Dict:
    """
    EL PORTERO: Clasifica la intención para decidir si GUARDAR (BD) o solo RESPONDER.
    """
    # Contexto de fecha (Añadiendo la definición de 'ahora' que faltaba)
    zona_horaria = pytz.timezone('America/Lima')
    ahora = datetime.now(zona_horaria).strftime("%Y-%m-%d %H:%M")

    prompt = f"""
    Actúa como el MODERADOR SEMÁNTICO de una IA avanzada.
    Tu objetivo es analizar la INTENCIÓN PROFUNDA del usuario, no sus palabras literales, y etiquetar el mensaje entrante según su utilidad para la Base de Datos.
    
    CONTEXTO:
    - Fecha actual: {ahora}
    - El usuario habla con naturalidad (jerga, oraciones complejas, errores).

    MENSAJE DEL USUARIO: "{mensaje}"

    ---------------------------------------------------
    ANÁLISIS DE CATEGORÍAS (Lógica de Decisión):
    ---------------------------------------------------
    
    1. CONSULTA (Chat Efímero / General / Búsqueda / Conversación): 
       - CRITERIO: El usuario busca una RESPUESTA INMEDIATA o INTERACCIÓN.
       - INCLUYE:
         * Conultas, preguntas, requierimiento de información que estea guardada en la memoria del sitema o base de datos y neceista una respuesta inmediata
         * toda consulta, solicitud de informacion, pregunta, incio de conversación  debe interpretarse como que el usuario requiere respuesta inmediata 
         * Búsquedas ("Búscame si llueve", "Investiga X"). -> Esto es CONSULTA porque quiere el dato YA.
         * Preguntas de cultura general, noticias o dudas simples ("¿Qué hora es?", "¿Lloverá hoy?").
         * Recuperación ("¿Recuerdas quién soy?", "¿Qué jugué ayer?"). -> Esto es CONSULTA (RAG).
         * Saludos ("Hola", "Buenas noches"), agradecimientos ("Gracias").
       - Lo que NO INCLUYE son todas las llamadas de accion que no sean consultas, preguntas, busquedas, saludos como por ejejmplo ejecuatr agenadar calendario, programar alarma, pagar, ejecutar recordatorios, acciones que requieren programar un evento y acciona runa tarea.
       -> ACCIÓN SISTEMA: NO GUARDAR.
    
    2. TAREA (Acción o Evento Futuro / Compromiso):
       - CRITERIO: El usuario necesita que el sistema "haga algo" en el futuro o gestione una agenda.
       - CLAVE: Implica TIEMPO FUTURO, PRESENTE o GESTIÓN DE ESTADO (borrar, agendar, recordar, avisar, ETC).
       - Por ejemplo: Órdenes directas ("Recuérdame pagar la luz", "Agendar cita").
       - Declaración de compromisos o citas ("Mañana tengo dentista a las 5", "El lunes viajo").
       - CORRECCIONES de tareas anteriores ("No, era a las 4pm", "Cambia la fecha").
       - Solo si el mensaje NO contiene información personal nueva.
       - NO es tarea si el usuario pide buscar información para consumirla AHORA MISMO.
       -> ACCIÓN SISTEMA: CREAR O MODIFICAR ALERTA.
       
    3. VALOR (Memoria, Perfilado, Datos Personales O MIXTO y ANÁLISIS DE ERRORES):
       - CRITERIO: El usuario comparte un dato sobre SU identidad, gustos, salud o vida personal.
       - El usuario cuenta algo de su vida, gustos, familia ("Soy alérgico a las nueces").
       - OBJETIVO: El sistema debe "aprender" esto para siempre.
       - MENSAJES MIXTOS: Si el usuario pide una tarea Y ADEMÁS da un dato personal ("Agendar gym y recuerda que mi perro es Toby").
       - RECLAMOS O CONSULTAS TÉCNICAS: "¿Por qué no pudiste agendar?", "¿Qué pasó con la tarea anterior?", "¿Qué sabes de mí?".
       - Conversaciones profundas o archivos adjuntos.
       -> ACCIÓN SISTEMA: GUARDAR Y ANALIZAR CONTEXTO.
    
    ---------------------------------------------------
    INSTRUCCIÓN DE SALIDA:
    Analiza la frase. Si es ambigua, pregúntate: "¿El usuario quiere un dato AHORA (Consulta) o una acción LUEGO (Tarea)?".

    Responde SOLO el JSON:
    {{
        "tipo": "CONSULTA" | "VALOR" | "TAREA",
        "subtipo": "chat_general | dato_personal | evento_pendiente | reclamo_sistema",
        "urgencia": "ALTA | MEDIA | BAJA"
    }}
    """
    try:
        if not gemini_client:
            return {"tipo": "CONSULTA"}
            
        response = gemini_client.models.generate_content(
            model=MODELO_IA,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0
            )
        )
        return json.loads(response.text)
    except:
        # Fallback de seguridad: Si el mensaje es largo o parece una queja, es VALOR.
        es_queja = any(x in mensaje.lower() for x in ["por qué", "qué pasó", "error", "no pudiste"])
        return {"tipo": "VALOR" if (len(mensaje) > 20 or es_queja) else "CONSULTA"}


async def procesar_informacion_valor(mensaje: str, clasificacion: Dict, usuario_id: str, origen: str = "webhook") -> Dict:
    """
    Motor de Análisis: 
    1. Resumen (Histórico).
    2. Perfilado (Memoria a largo plazo en 'perfil_usuario').
    3. Tareas (Alertas en 'alertas').
    """
    if not supabase: return {"status": "error", "respuesta": "Error de conexión BD"}

    # 1. Contexto Temporal EXACTO (Perú)
    # Esto es vital para que "mañana" se calcule bien.
    zona_horaria = pytz.timezone('America/Lima')
    fecha_obj = datetime.now(zona_horaria)
    fecha_actual = fecha_obj.strftime("%Y-%m-%d %H:%M:%S (%A)") # Ej: 2026-01-05 16:30:00 (Lunes)

    # 2. Prompt Optimizado (Preservando tu estructura original pero con objetivos claros)
    prompt = f"""
    Actúa como un Asistente de Inteligencia Artificial Avanzada (Backend).
    Estás procesando información entrante de una conversación.
    
    CONTEXTO:
    - Fecha y Hora actual (Lima, Perú): {fecha_actual}
    - Subtipo detectado: {clasificacion.get('subtipo')}
    
    TEXTO A ANALIZAR: "{mensaje}"
    
    TUS 3 OBJETIVOS:
    1. RESUMEN: Sintetiza lo ocurrido o acordado (Datos duros).
    2. PERFILADO (MEMORIA): Extrae datos ATEMPORALES sobre el usuario (Gustos, trabajo, familia, salud). 
       - Solo guarda datos permanentes.
       - Si no hay nada nuevo sobre la identidad del usuario, deja la lista vacía.
    3. TAREAS: Detecta acciones pendientes.
       - IMPORTANTE: Si dice "mañana", calcula la fecha exacta basándote en que HOY es {fecha_actual}.
    
    JSON Schema:
    {{
        "resumen_guardar": "Texto profesional resumido",
        "tipo_evento": "reunion | acuerdo | dato_cliente | personal | salud | otro",
        "aprendizajes_usuario": ["Dato 1", "Dato 2"],
        "tareas": [
            {{ 
                "titulo": "Acción corta", 
                "prioridad": "ALTA" | "MEDIA" | "BAJA", 
                "descripcion": "Incluye FECHA EXACTA calculada y detalles", 
                "etiqueta": "NEGOCIO" | "ESTUDIO" | "PAREJA" | "SALUD" | "PERSONAL" | "OTROS" 
            }}
        ]
    }}
    """

    try:
        if not gemini_client:
            raise Exception("Cliente no disponible")
        
        response = gemini_client.models.generate_content(
            model=MODELO_IA,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        analisis = json.loads(response.text)
        
        # 4. GUARDAR CONVERSACIÓN (Historial)
        datos_conv = {
            "usuario_id": usuario_id,
            "resumen": analisis.get('resumen_guardar', 'Información procesada'), 
            "tipo": analisis.get('tipo_evento', 'otro'),
            "urgencia": clasificacion.get('urgencia', 'BAJA'),
            "plataforma": origen,
            "metadata": {
                "raw_msg": mensaje if len(mensaje) < 1000 else mensaje[:1000] + "...",
                "nuevos_conocimientos": analisis.get('aprendizajes_usuario', [])
            }
        }
        res_conv = supabase.table('conversaciones').insert(datos_conv).execute()
        
        # Obtenemos el ID de la conversación recién creada para vincular la memoria
        conv_id = res_conv.data[0]['id'] if res_conv.data else None
        
        # ==============================================================================
        # 5. BLOQUE DE MEMORIA (PERFILADO)
        # ==============================================================================
        nuevos_datos = analisis.get('aprendizajes_usuario', [])
        memoria_guardada = 0
        
        if nuevos_datos:
            datos_perfil = []
            for dato in nuevos_datos:
                datos_perfil.append({
                    "usuario_id": usuario_id,
                    "dato": dato,                # Lo que aprendió la IA
                    "categoria": "AUTO_IA",      # Etiqueta automática
                    "origen": f"conv_{conv_id}"  # Trazabilidad
                })
            
            # Upsert: Evita duplicados si la IA aprende lo mismo dos veces
            try:
                supabase.table('perfil_usuario').upsert(
                    datos_perfil, 
                    on_conflict="usuario_id, dato"
                ).execute()
                memoria_guardada = len(datos_perfil)
            except Exception as e_mem:
                print(f"⚠️ Nota Memoria: {e_mem}")

        # ==============================================================================

        # 6. CREAR ALERTAS (TAREAS)
        alertas_creadas = 0
        tareas_detectadas = analisis.get('tareas', [])
        
        if tareas_detectadas:
            alertas = []
            for t in tareas_detectadas:
                # 🔥 NUEVO: Enriquecer cada tarea
                contexto_tarea = enriquecer_alerta_con_contexto(
                    titulo=t.get('titulo', 'Recordatorio'),
                    descripcion=t.get('descripcion', analisis['resumen_guardar'])
                )

                alertas.append({
                    "usuario_id": usuario_id,
                    "conversacion_id": conv_id,
                    "titulo": t.get('titulo', 'Recordatorio'),
                    "descripcion": t.get('descripcion', f"Derivado de: {analisis['resumen_guardar']}"),
                    "prioridad": t.get('prioridad', 'MEDIA'),
                    "tipo": "auto_detectada",
                    "estado": "pendiente",
                    "etiqueta": t.get('etiqueta', 'OTROS'),
                    "metadata": serializar_universal(contexto_tarea)
                })
            
            if alertas:
                # Guardamos en BD y capturamos la respuesta 'res_alertas' para tener los IDs
                res_alertas = supabase.table('alertas').insert(alertas).execute()
                alertas_creadas = len(alertas)

            debe_notificar = False
            mensaje_pedido_confirmacion = any(word in mensaje.lower() for word in [
                "confir", "avisa", "notifica", "recuerda esto", "guardame"
            ])
            
            tiene_alta_prioridad = any(t.get('prioridad') == 'ALTA' for t in tareas_detectadas)
            
            if mensaje_pedido_confirmacion or tiene_alta_prioridad:
                debe_notificar = True
            
            if debe_notificar:
                try:
                    # 1. Obtener Token del usuario
                    user_data = supabase.table('usuarios').select('fcm_token').eq('id', usuario_id).execute()
                    
                    if user_data.data and user_data.data[0].get('fcm_token'):
                        token = user_data.data[0]['fcm_token']
                        
                        # 2. Crear mensaje agrupado
                        cantidad = len(alertas)
                        
                        if cantidad == 1:
                            # Si es solo una tarea, mostrar detalles completos
                            item = alertas[0]
                            prio = item.get('prioridad', 'MEDIA')
                            emoji = "🔴" if prio == 'ALTA' else ("🟡" if prio == 'MEDIA' else "🟢")
                            
                            titulo = f"{emoji} Nueva Tarea: {item['titulo']}"
                            cuerpo = item['descripcion']
                        else:
                            # Si son varias, agrupar
                            titulo = f"📋 {cantidad} Tareas Nuevas Guardadas"
                            
                            # Listar títulos
                            lista_tareas = "\n".join([f"• {a['titulo']}" for a in alertas[:3]]) # Mostrar máximo 3
                            if cantidad > 3:
                                lista_tareas += f"\n... y {cantidad - 3} más"
                            
                            cuerpo = lista_tareas
                        
                        # 3. Enviar UNA SOLA notificación
                        enviar_push(
                            token=token,
                            titulo=titulo,
                            cuerpo=cuerpo,
                            data_extra={
                                "tipo": "TAREA",
                                "cantidad": cantidad,
                                "click_action": "FLUTTER_NOTIFICATION_CLICK"
                            }
                        )
                        
                except Exception as e_push:
                    print(f"⚠️ Error enviando notificación: {e_push}")

        # 7. Retorno final
        feedback_extra = f"\n🧠 Aprendí {memoria_guardada} cosas nuevas." if memoria_guardada > 0 else ""

        return {
            "status": "guardado", 
            "resumen": analisis['resumen_guardar'], 
            "alertas_generadas": alertas_creadas,
            "aprendizajes": memoria_guardada,
            "respuesta": f"✅ Info guardada: {analisis['resumen_guardar']}{feedback_extra}"
        }
        
    except Exception as e:
        print(f"❌ Error procesando valor: {e}")
        return {"status": "error", "respuesta": f"Error procesando: {str(e)}"}



async def crear_tarea_directa(mensaje: str, usuario_id: str) -> Dict:
    """
    FUSIÓN: Estructura robusta del Código B + Inteligencia de fechas/acciones del Código A.
    1. Usa el Prom5pt de Lista (A) para detectar múltiples acciones.
    2. Mantiene la seguridad de BD y actualización de Meet (B).
    3. Genera una notificación rica con detalles.
    4. Analiza intención (Crear/Modificar/Completar).
    5. Actualiza Base de Datos (Memoria).
    6. Envía COMANDOS SILENCIOSOS al celular para actualizar Alarmas/Calendario (Ejecución).
    """
    
    # --- 1. CONTEXTO TEMPORAL (Base del Código B) ---
    zona_horaria = pytz.timezone('America/Lima')
    ahora = datetime.now(zona_horaria)
    fecha_actual = ahora.strftime("%Y-%m-%d %H:%M:%S (%A)")

    # --- 2. PRE-ANÁLISIS (Base del Código B) ---
    extractor = ExtractorContexto()
    contexto = enriquecer_alerta_con_contexto(
        titulo="Procesando...", 
        descripcion=mensaje
    )

    
    # --- 🔴 INYECCIÓN INTELIGENTE (Del Código A) ---
    # Calculamos una fecha de referencia segura por si el regex falla
    datos_fecha = contexto.get('fecha_hora')
    if datos_fecha and isinstance(datos_fecha, dict):
        fecha_referencia = datos_fecha.get('fecha', ahora.strftime("%Y-%m-%d"))
    else:
        fecha_referencia = ahora.strftime("%Y-%m-%d")

    # --- 2. OBTENER CONTEXTO REAL (NUEVO: Ojos del sistema) ---
    # Leemos la BD para saber qué existe antes de decidir.
    lista_pendientes_txt = "No hay tareas pendientes."
    try:
        res_pendientes = supabase.table('alertas')\
            .select('id, titulo, fecha_limite, metadata')\
            .eq('usuario_id', usuario_id)\
            .eq('estado', 'pendiente')\
            .execute()
        
        if res_pendientes.data:
            # Formateamos incluyendo ID (UUID) y Título para desambiguación de nombres
            lista_pendientes_txt = "\n".join([
                f"- ID_REF: {t['id']} | Tarea: {t['titulo']} | Fecha: {t.get('fecha_limite')}" 
                for t in res_pendientes.data
            ])
    except Exception as e:
        print(f"⚠️ Error leyendo contexto BD: {e}")

    


    
    # --- 🔴 PROMPT POTENCIADO (Del Código A - Pide Lista) ---
    prompt = f"""
        Actúa como un Asistente Ejecutivo Experto.
        HOY ES: {fecha_actual}
        FECHA BASE DEL TEXTO: {fecha_referencia}
        INVENTARIO DE TAREAS PENDIENTES: {lista_pendientes_txt}
        MENSAJE DEL USUARIO: "{mensaje}"

        OBJETIVO: Desglosar el mensaje en una LISTA de acciones técnicas con sus fechas exactas e identificar si el usuario quiere CREAR (nuevo), MODIFICAR (existente) o COMPLETAR (existente)..

        REGLAS DE PRECISIÓN (CRÍTICO):
        1. COINCIDENCIA DE NOMBRES: Si el usuario pide modificar una "Entrevista con Pablo", y en la lista hay "Pablo Méndez" y "Pablo Margarete":
           - Si el usuario dijo "Pablo Méndez", ELIGE SOLO ESE ID.
           - Si el usuario solo dijo "Pablo" y hay ambigüedad, NO ELIJAS NINGUNO (Asume "crear" tarea nueva o error, para no dañar datos).
        2. MODIFICAR: Cambios de hora/fecha/título y de mas detalles en tareas existentes.
        3. COMPLETAR: Palabras como , "completado", "hecho", "realizado", "check","listo", "borrar", "ya pasó", "cancelar" y otros terminos similares.
        4. CREAR: Solo si NO coincide claramente con nada de la lista.
        5. UUID: Usa el "ID_REF" exacto de la lista para modificar/completar.

        INSTRUCCIONES:
        1. INSTRUCCIONES DE INTENCIÓN (Clasifica con rigor):
            1.1. ALARMA ("poner_alarma"):
            - Úsala para despertares o avisos puntuales de reloj.
            - NO la uses solo por la palabra "recordatorio" si implica un evento largo.

            1.2. CALENDARIO ("agendar_calendario"):
            - Para eventos, citas, reuniones, entrevistas o bloques de tiempo.
            - Si menciona "Meet/Videollamada", crea ESTE item Y TAMBIÉN el item de "crear_meet".

            1.3. MEET ("crear_meet"):
            - EXCLUSIVO para generar enlaces de videollamada (Zoom/Teams/Meet).
            - Solo si el usuario pide explícitamente video/virtual.

            1.4. MAPA ("ver_ubicacion"):
            - Siempre que haya una dirección, lugar o intención de ir/llegar.

            1.5. LLAMADA ("llamar"):
            - Llamadas telefónicas convencionales (App Teléfono) o por Audio WhatsApp.

            1.6. WHATSAPP ("enviar_whatsapp"):
            - Para enviar MENSAJES de texto/chat a otra persona.

            1.7. PAGOS ("abrir_yape"):
            - Yape, Plin, Transferencias, Deudas.

            1.8. CONTACTOS ("guardar_contacto"):
            - Registrar, guardar, agregar o modificar números de agenda.

            1.9. CORREO ("enviar_correo"):
            - Enviar emails.
            
        2. Para CADA acción, calcula la "fecha_iso" exacta.

        REGLAS DE SALIDA (JSON ARRAY):
        [
            {{
                "accion_macro": "crear" | "modificar" | "completar",
                "id_tarea_bd": "UUID_EXACTO_DE_LA_LISTA" (String, OBLIGATORIO para modificar/completar, null si es crear),
                "datos": {{
                    "titulo": "Nombre corto",
                    "descripcion": "Descripción detallada",
                    "tipo_accion": "poner_alarma" | "agendar_calendario" | "crear_meet" | "ver_ubicacion",
                    "prioridad": "ALTA" | "MEDIA",
                    "etiqueta": "NEGOCIO" | "PERSONAL",
                    "fecha_iso": "YYYY-MM-DDTHH:MM:SS" (OBLIGATORIO),
                    "dato_extra": "Link, Dirección o Teléfono"
                }}
            }}
        ]
        
        REGLAS CRÍTICAS:
        - "fecha_iso": Formato ISO ESTRICTO. Si dice "mañana a las 5pm", calcula la fecha real.
        - Si hay "meet" o "videollamada", tipo_accion es "crear_meet".
        
        RESPONDE SOLO CON EL ARRAY JSON.
    """

    datos_finales = {}
    acciones_para_metadata = [] # Lista para guardar las sub-acciones

    # --- 3. LLAMADA A IA (Estructura B con lógica de A) ---
    try:
        if not gemini_client: raise Exception("Cliente Gemini no disponible")

        resp = gemini_client.models.generate_content(
            model=MODELO_IA,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )

        texto_limpio = resp.text.replace("```json", "").replace("```", "").strip()
        lista_acciones = json.loads(texto_limpio)
        
        # Aseguramos que sea lista, incluso si la IA devuelve un solo objeto
        if isinstance(lista_acciones, dict): lista_acciones = [lista_acciones]

        print(f"🤖 IA detectó {len(lista_acciones)} acciones.")

    # Obtenemos Token para notificaciones (Necesario en todos los casos)
        token_usuario = None
        try:
            user_data = supabase.table('usuarios').select('fcm_token').eq('id', usuario_id).execute()
            if user_data.data: token_usuario = user_data.data[0].get('fcm_token')
        except: pass

        resultado_acumulado = ""
        contexto_final = contexto # Para retornar al final

        # --- BUCLE DE EJECUCIÓN INTELIGENTE ---
        for item in lista_acciones:
            macro = item.get('accion_macro', 'crear')
            datos = item.get('datos', {})
            id_bd = item.get('id_tarea_bd')
            
            # Saneamiento de fecha
            fecha_iso = datos.get('fecha_iso')
            if fecha_iso and "T" not in fecha_iso: fecha_iso = f"{fecha_iso}T09:00:00"

            # =================================================================
            # CASO A: COMPLETAR / ELIMINAR 
            # =================================================================
            if macro == 'completar' and id_bd:
                supabase.table('alertas').update({'estado': 'completado'}).eq('id', id_bd).execute()
                
                if token_usuario:
                    enviar_push(
                        token=token_usuario,
                        titulo="Tarea Completada",
                        cuerpo=f"Completado: {datos.get('titulo', 'Tarea')}",
                        data_extra={
                            "tipo": "SYNC_COMMAND",       
                            "comando": "ELIMINAR_NATIVO", 
                            "alerta_id_bd": str(id_bd),   
                            "titulo": datos.get('titulo', '')
                        }
                    )
                resultado_acumulado += "✅ Tarea marcada como lista. "

            # =================================================================
            # CASO B: MODIFICAR 
            # =================================================================
            elif macro == 'modificar' and id_bd:
                payload = {'updated_at': datetime.now().isoformat()}
                if fecha_iso: payload['fecha_limite'] = fecha_iso
                if datos.get('titulo'): payload['titulo'] = datos['titulo']
                
                # Preservar metadata existente
                try:
                    curr = supabase.table('alertas').select('metadata').eq('id', id_bd).execute()
                    if curr.data:
                        meta = curr.data[0].get('metadata')
                        if isinstance(meta, str): meta = json.loads(meta)
                        if fecha_iso: meta['fecha_hora_especifica'] = fecha_iso
                        payload['metadata'] = meta
                except: pass

                supabase.table('alertas').update(payload).eq('id', id_bd).execute()

                if token_usuario and fecha_iso:
                    enviar_push(
                        token=token_usuario,
                        titulo="Agenda Actualizada",
                        cuerpo=f"Reprogramado: {datos.get('titulo')}",
                        data_extra={
                            "tipo": "SYNC_COMMAND",          
                            "comando": "REPROGRAMAR_NATIVO", 
                            "alerta_id_bd": str(id_bd),
                            "nueva_fecha": fecha_iso,
                            "nuevo_titulo": datos.get('titulo', ''),
                            "tipo_accion_nativa": datos.get('tipo_accion', 'poner_alarma')
                        }
                    )
                resultado_acumulado += f"🔄 Reprogramado para {fecha_iso}. "

            # =================================================================
            # CASO C: CREAR (TU LÓGICA ORIGINAL RESTAURADA)
            # =================================================================
            else:
                # 1. Preparación de variables (Tu código original)
                acciones_para_metadata = []
                # Si la IA devolvió una lista en 'crear', la procesamos (compatibilidad)
                sub_lista = [item] if macro == 'crear' else lista_acciones
                
                titulo_principal = datos.get('titulo', 'Nueva Tarea')
                descripcion_principal = datos.get('descripcion', mensaje)
                fecha_limite_principal = fecha_iso
                
                # Reconstruimos la metadata como en tu original
                acc_meta = {
                    "tipo": datos.get('tipo_accion', 'agendar_calendario'),
                    "titulo": titulo_principal,
                    "fecha_hora_especifica": fecha_iso,
                    "dato_extra": datos.get('dato_extra')
                }
                acciones_para_metadata.append(acc_meta)

                if not fecha_limite_principal:
                    fecha_limite_principal = f"{fecha_referencia}T09:00:00"

                contexto['acciones_programadas'] = acciones_para_metadata
                contexto['fecha_hora_especifica'] = fecha_iso
                if 'crear_meet' in str(acciones_para_metadata):
                    contexto['link_meet'] = "https://meet.google.com/new"

                # Limpieza de metadata
                metadata_segura = serializar_universal(contexto)

                datos_finales = {
                    "usuario_id": usuario_id,
                    "titulo": titulo_principal,
                    "descripcion": descripcion_principal,
                    "prioridad": datos.get('prioridad', 'MEDIA'),
                    "tipo": "manual",
                    "estado": "pendiente",
                    "etiqueta": datos.get('etiqueta', 'OTROS'),
                    "fecha_limite": fecha_limite_principal,
                    "metadata": metadata_segura 
                }

                # 2. Insertar en BD
                res = supabase.table('alertas').insert(datos_finales).execute()

                # 3. Lógica Legacy Meet (Tu lógica async original)
                if res.data and 'crear_meet' in str(acciones_para_metadata):
                    try:
                        import asyncio
                        await asyncio.sleep(2)
                        recarga = supabase.table('alertas').select('metadata').eq('id', res.data[0]['id']).execute()
                        if recarga.data:
                            meta_recargada = recarga.data[0].get('metadata', {})
                            if isinstance(meta_recargada, dict) and meta_recargada.get('link_meet'):
                                contexto['link_meet'] = meta_recargada['link_meet']
                    except: pass

                # 4. PUSH CREACIÓN (TU FORMATO ORIGINAL + CORRECCIÓN METADATA)
                if token_usuario:
                    try:
                        # 🔥 AQUÍ ESTÁ LA CORRECCIÓN CLAVE QUE PEDISTE 🔥
                        meta_push = datos_finales['metadata']
                        # Aseguramos que sea string para el push
                        if isinstance(meta_push, dict): meta_push = json.dumps(meta_push)

                        enviar_push(
                            token=token_usuario,
                            titulo=f"⚡ Agenda: {datos_finales['titulo']}",
                            cuerpo=f"Agendado: {datos_finales['fecha_limite']}",
                            data_extra={
                                "tipo": "TAREA_EJECUTABLE",
                                "alerta_id": str(res.data[0]['id']) if res.data else "0",
                                "ejecutar_automatico": "true",
                                "titulo": datos_finales['titulo'],
                                "acciones_json": json.dumps(acciones_para_metadata),
                                "metadata": meta_push # ✅ USAMOS LA VERSIÓN LIMPIA DE DATOS_FINALES
                            }
                        )
                    except Exception as e_p: print(f"Error Push Crear: {e_p}")

                resultado_acumulado += f"✅ Agendado: {datos_finales['titulo']}. "
                contexto_final = contexto

        # Retorno final unificado
        return {
            "status": "procesado",
            "respuesta": resultado_acumulado,
            "metadata": contexto_final,
            "acciones": lista_acciones # Para debug visual
        }    

    # --- 🔵 MANEJO DE ERRORES BD (Robustez del Código B) ---
    except Exception as e_bd:
        print(f"🛑 Error BD: {e_bd}")
        # Intento de auto-creación de usuario (User Rescue)
        if "foreign key" in str(e_bd).lower() or "violates" in str(e_bd).lower():
            try:
                auth_user = supabase.auth.get_user(usuario_id)
                if auth_user:
                    supabase.table('usuarios').insert({
                        'id': usuario_id,
                        'email': auth_user.user.email,
                        'nombre': 'Usuario Recuperado'
                    }).execute()
                    # Reintento recursivo (solo una vez)
                    return await crear_tarea_directa(mensaje, usuario_id) 
            except:
                pass
        
        return {
            "status": "error_db", 
            "respuesta": "No pude guardar la tarea. Por favor reinicia la sesión."
        }

   
async def procesar_consulta_rapida(mensaje: str, usuario_id: str, modo_profundo: bool) -> str:
    """
    Responde consultas conectando:
    1. PERFIL (Memoria a Largo Plazo: Quién es el usuario).
    2. HISTORIAL (Memoria a Corto/Mediano Plazo: Qué ha pasado).
    3. CONSULTAS, PREGUNTAS (Agenda: Qué tiene pendiente).
    4. INTERNET (Google Search: Para datos actuales).
    """
    if not supabase: return "Error: No hay conexión a base de datos o IA."
    
    # Garantizamos la hora Perú para que el contexto temporal sea exacto
    zona_horaria = pytz.timezone('America/Lima')
    fecha_obj = datetime.now(zona_horaria)
    fecha_actual = fecha_obj.strftime("%Y-%m-%d %H:%M:%S (%A)")
    
    contexto_bd = ""

    try:
        # ==============================================================================
        # 1. RECUPERAR PERFIL DEL USUARIO (Tu código original)
        # ==============================================================================
        res_perfil = supabase.table('perfil_usuario')\
            .select('dato')\
            .eq('usuario_id', usuario_id)\
            .execute()
        
        if res_perfil.data:
            lista_perfil = [f"- {p['dato']}" for p in res_perfil.data]
            texto_perfil = "\n".join(lista_perfil)
        else:
            texto_perfil = "(Aún no tengo datos personales registrados de este usuario)"

        # ==============================================================================
        # 2. CONSTRUCCIÓN DE CONTEXTO (Tu lógica original preservada)
        # ==============================================================================
        if modo_profundo:
            # --- MODO PROFUNDO ---
            res_conv = supabase.table('conversaciones')\
                .select('resumen, tipo, created_at')\
                .eq('usuario_id', usuario_id)\
                .order('created_at', desc=True)\
                .limit(100)\
                .execute()
            
            res_alertas = supabase.table('alertas')\
                .select('titulo, estado, etiqueta')\
                .eq('usuario_id', usuario_id)\
                .order('created_at', desc=True)\
                .limit(30)\
                .execute()

            datos_texto = []
            if res_conv.data:
                for c in reversed(res_conv.data):
                    datos_texto.append(f"- [{c['created_at'][:10]}] ({c.get('tipo','General')}) {c['resumen']}")
            
            tareas_hist = [f"- [{a['estado']}] {a['titulo']}" for a in res_alertas.data] if res_alertas.data else []
            
            contexto_bd = (
                f"HISTORIAL CRONOLÓGICO (100 últimos eventos):\n" + "\n".join(datos_texto) + 
                f"\n\nHISTORIAL DE TAREAS:\n" + "\n".join(tareas_hist)
            )

        else:
            # --- MODO RÁPIDO ---
            res_alertas = supabase.table('alertas')\
                .select('titulo, descripcion, etiqueta, fecha_limite')\
                .eq('usuario_id', usuario_id)\
                .eq('estado', 'pendiente')\
                .execute()
            
            res_recent = supabase.table('conversaciones')\
                .select('resumen, created_at')\
                .eq('usuario_id', usuario_id)\
                .order('created_at', desc=True)\
                .limit(15)\
                .execute()

            pendientes_txt = "\n".join([f"- [PENDIENTE] {a['titulo']} ({a.get('descripcion','')})" for a in res_alertas.data]) if res_alertas.data else "No hay pendientes."
            reciente_txt = "\n".join([f"- [HACE POCO: {c['created_at'][:10]}] {c['resumen']}" for c in res_recent.data]) if res_recent.data else ""
            
            contexto_bd = f"PENDIENTES AHORA:\n{pendientes_txt}\n\nCONTEXTO RECIENTE:\n{reciente_txt}"

        # 🔥 NUEVO: AGREGAR ESTO - BUSCADOR DE MEMORIA INTELIGENTE
        # Buscamos en la base de datos recuerdos que se parezcan al tema que habla el usuario
        memoria_vectorial = ""
        try:
            print(f"🧠 Buscando recuerdos semánticos para: {mensaje}")
            memoria_vectorial = await buscar_contexto_historico(usuario_id, mensaje)
        except Exception as e:
            print(f"⚠️ Error buscando vectores: {e}")
            memoria_vectorial = "(No se pudo acceder a la memoria profunda)"
        
        # 👆👆👆 FIN DE LO NUEVO PARTE 1 👆👆👆

        # ==============================================================================
        # 3. CEREBRO DE LA RESPUESTA (MODIFICADO PARA INTERNET)
        # ==============================================================================
        prompt = f"""
        Actúa como un Asistente Personal de Inteligencia Artificial altamente eficiente y empático.
        
        FECHA ACTUAL: {fecha_actual}
        
        CONOCIMIENTO SOBRE EL USUARIO (PERFIL):
        ---------------------------------------
        {texto_perfil}
        ---------------------------------------
        
        CONTEXTO / MEMORIA (LO QUE HA PASADO):
        ---------------------------------------
        {contexto_bd}
        ---------------------------------------
        
        🔥 MEMORIA PROFUNDA (RECUERDOS SIMILARES DEL PASADO):
        ---------------------------------------
        {memoria_vectorial}
        ---------------------------------------

        CONSULTA DEL USUARIO: "{mensaje}"
        
        [INSTRUCCIONES DE RESPUESTA]
        1. Responde de forma natural, como un humano eficiente y preciso.

        DIRECTRICES DE RESPUESTA:
        1. INTERNET: Si el usuario pregunta por noticias, clima, dólar o datos actuales, USA TU HERRAMIENTA DE BÚSQUEDA (Google Search).
        2. PERSONALIZACIÓN: Usa los datos del PERFIL para adaptar tu respuesta.
        3. HISTORIAL: Si pregunta algo específico del pasado, usa el CONTEXTO.
        4. MEMORIA: Si pregunta "¿Qué me dijo Juan?", busca en MEMORIA PROFUNDA. Si pregunta "¿Qué hice hoy?", busca en MEMORIA RECIENTE.
        5. TONO: Eres un asistente útil. Sé claro y directo.
        6. FILTRO: Si pregunta algo específico del historial, usa los datos de CONTEXTO. Si es una duda general, responde con tu conocimiento base.
        
        [REGLAS NEGATIVAS - MUY IMPORTANTE]
        - NO escribas "Clasificación de tareas".
        - NO escribas "Resumen".
        - NO expliques tu proceso de pensamiento.
        - Solo entrega la respuesta final optimizada y eficiente.
        """

        # 4. CONFIGURACIÓN CON GOOGLE SEARCH ✅
        # ==============================================================================
        herramienta_google = types.Tool(
            google_search=types.GoogleSearch()
        )
        
        response = gemini_client.models.generate_content(
            model=MODELO_IA,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[herramienta_google]
            )
        )
        
             
        return response.text

    except Exception as e:
        print(f"Error en consulta rápida: {e}")
        return "Lo siento, tuve un problema conectando con tu memoria."


# ==============================================================================
# 🧠 CEREBRO IA: MEMORIA Y VECTORES
# ==============================================================================

async def generar_embedding(texto: str):
    """Convierte texto en una lista de números (vector) usando Gemini"""
    if not GEMINI_DISPONIBLE: return None
    try:
        # 2. Usamos TU variable global exacta
        global gemini_client 
        
        # Si por alguna razón está vacía, intentamos reconectar
        if gemini_client is None and API_KEY_GOOGLE:
             gemini_client = genai.Client(api_key=API_KEY_GOOGLE)

        if not gemini_client:
            print("⚠️ No hay cliente Gemini disponible para embeddings")
            return []

        # 3. 🔥 LA CORRECCIÓN: Lista de modelos a probar (Prioridad: Moderno -> Clásico)
        # Esto soluciona tu error 404. Si el 004 no existe en tu región, usa el 001.
        modelos = ["gemini-embedding-001", "models/gemini-embedding-001"]
        
        for modelo_actual in modelos:
            try:
                # Limpiamos el texto para evitar errores de API con vacíos
                texto_limpio = texto.replace("\n", " ").strip()
                if not texto_limpio: return []

                # Llamada corregida para google-genai SDK
                result = gemini_client.models.embed_content(
                    model=modelo_actual,
                    contents=texto_limpio
                )
                
                # Validación de respuesta
                if result.embeddings:
                    # ✅ ÉXITO: Vector generado correctamente
                    # print(f"✅ Embedding generado con éxito usando: {modelo_actual}") 
                    return result.embeddings[0].values
            
            except Exception as e_modelo:
                # Si falla este modelo, solo imprimimos aviso y el bucle prueba el siguiente
                print(f"⚠️ Aviso: Falló {modelo_actual} ({e_modelo}). Probando siguiente...")
                continue

        # Si llegamos aquí, fallaron ambos
        print("❌ Error: No se pudo generar embedding con ningún modelo.")
        return []

    except Exception as e:
        print(f"⚠️ Error generando embedding: {e}")
        return None

async def buscar_contexto_historico(usuario_id: str, consulta: str):
    """Busca conversaciones pasadas similares a la consulta actual"""
    vector_consulta = await generar_embedding(consulta)
    if not vector_consulta: return ""

    try:
        # Llamamos a la función RPC 'match_conversaciones' que creaste en SQL
        res = supabase.rpc(
            'match_conversaciones', 
            {
                'query_embedding': vector_consulta,
                'match_threshold': 0.6, # 60% de similitud mínima
                'match_count': 3,       # Traer los 3 recuerdos más relevantes
                'p_usuario_id': usuario_id
            }
        ).execute()
        
        if not res.data: return ""

        contexto = "\n🔍 MEMORIA HISTÓRICA:\n"
        for item in res.data:
            # Asumiendo que tu tabla conversaciones tiene columna 'resumen'
            resumen = item.get('resumen', 'Sin resumen')
            contexto += f"- {resumen}\n"
            
        return contexto
    except Exception as e:
        print(f"❌ Error buscando memoria: {e}")
        return ""

def limpiar_json_gemini(texto_sucio: str) -> dict:
    """
    Limpia la respuesta de la IA para obtener un JSON válido.
    Elimina bloques de código markdown y busca el primer '{' y último '}'.
    """
    try:
        # 1. Si ya es un dict, devolverlo
        if isinstance(texto_sucio, dict):
            return texto_sucio
            
        # 2. Eliminar marcadores de código markdown (```json ... ```)
        texto_limpio = re.sub(r'```json\s*', '', texto_sucio)
        texto_limpio = re.sub(r'```', '', texto_limpio)
        
        # 3. Buscar el JSON entre llaves por si hay texto extra
        inicio = texto_limpio.find('{')
        fin = texto_limpio.rfind('}') + 1
        
        if inicio != -1 and fin != -1:
            texto_limpio = texto_limpio[inicio:fin]
            
        return json.loads(texto_limpio)
    except Exception as e:
        print(f"⚠️ Error limpiando JSON: {e}")
        # Retornamos una estructura vacía segura en caso de error fatal
        return {"nuevo_resumen": "Error procesando resumen.", "tareas": [], "datos_clave": []}





async def ejecutar_logica_sincronizacion(
    usuario_id: str, 
    email_gmail: str, 
    server_auth_code: str = None, 
    gmail_token: str = None,
    gmail_refresh_token: str = None
):
    
    """
    Sincroniza correos desde Gmail y los vincula con la cuenta correcta.
    Ahora soporta múltiples cuentas Gmail por usuario.
    """
    if not gemini_client:
        raise HTTPException(status_code=500, detail="IA no disponible")
    
    # --- 🔥 ZONA DE CONFIGURACIÓN (NUEVO) ---
    # Asegúrate que estos coincidan con tu Google Cloud Console y tu Flutter
    GOOGLE_CLIENT_ID = "269344577878-gnf64lmpd3hcnlfsl1i5brduqvqq49na.apps.googleusercontent.com"
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET") # <--- ⚠️ PEGA TU SECRET AQUÍ (GOCSPX-...)
    # Validación de seguridad para que no falle silenciosamente
    if not GOOGLE_CLIENT_SECRET:
        print("❌ ERROR CRÍTICO: No se encontró GOOGLE_CLIENT_SECRET en las variables de entorno.")
        raise HTTPException(status_code=500, detail="Error de configuración del servidor (Secret faltante)")
    # Esta URL no se usa realmente en este flujo post-mensaje, pero es requerida por el protocolo
    REDIRECT_URI = "" 
    # ----------------------------------------

    try:
        
        
        nuevo_refresh_token = None
        token_recien_horneado = False # 🔥 NUEVA BANDERA
        # --- 🔥 BLOQUE DE INTERCAMBIO ASÍNCRONO (CERO BLOQUEOS) ---
        if server_auth_code:
            print(f"🔄 Canjeando código de autorización para: {email_gmail}")
            token_url = "https://oauth2.googleapis.com/token"
            payload = {
                'client_id': GOOGLE_CLIENT_ID,
                'client_secret': GOOGLE_CLIENT_SECRET,
                'code': server_auth_code,
                'grant_type': 'authorization_code',
                'redirect_uri': REDIRECT_URI
            }
            
            # Usamos httpx para no detener el servidor entero mientras Google responde
            async with httpx.AsyncClient() as client:
                res = await client.post(token_url, data=payload)
                data_google = res.json()

            if res.status_code == 200 and 'access_token' in data_google:
                gmail_token = data_google['access_token']
                nuevo_refresh_token = data_google.get('refresh_token') # ¡El Tesoro!
                token_recien_horneado = True # 🔥 LE AVISAMOS AL SISTEMA QUE YA ES FRESCO
                print(f"✅ Token canjeado exitosamente. Hay Refresh Token: {nuevo_refresh_token is not None}")
            else:
                cuenta_previa = await asyncio.to_thread(
                    lambda: supabase.table('cuentas_gmail').select('refresh_token').eq('usuario_id', usuario_id).eq('email_gmail', email_gmail).execute()
                )
                if cuenta_previa.data and cuenta_previa.data[0].get('refresh_token'):
                    print("⚠️ Código quemado, pero YA TENEMOS el refresh_token en BD. Ignorando error y continuando el flujo.")
                else:
                    # Solo matamos el proceso si el código es inválido Y no tenemos respaldo en la BD.
                    print(f"⚠️ Error fatal canjeando token: {data_google}")
                    raise HTTPException(status_code=401, detail=f"Código Auth inválido o expirado. Detalles: {data_google}")
        
        # [CIRUGÍA 3]: Generar fecha compatible con PostgreSQL para evitar fallos silenciosos de Supabase.
        ahora_iso = datetime.now(timezone.utc).isoformat()

        datos_cuenta = {
            'usuario_id': usuario_id,
            'email_gmail': email_gmail,
            'access_token': gmail_token, # Guardamos el token más reciente
            'activo': True,
            'updated_at': ahora_iso
        }

        # 🔥 MODIFICADO: Guardamos el Refresh Token si lo conseguimos
        if gmail_token:
            datos_cuenta['access_token'] = gmail_token
        if nuevo_refresh_token:
            datos_cuenta['refresh_token'] = nuevo_refresh_token
        elif gmail_refresh_token: # Fallback por si viene en el body directo
            datos_cuenta['refresh_token'] = gmail_refresh_token

        # 🚨 YA NO GUARDAMOS client_id NI client_secret EN ESTA TABLA.
        
        def _actualizar_bd():
            try:
                cuenta_exist = supabase.table('cuentas_gmail').select('id').eq('usuario_id', usuario_id).eq('email_gmail', email_gmail).execute()
                if cuenta_exist.data:
                    cid = cuenta_exist.data[0]['id']
                    supabase.table('cuentas_gmail').update(datos_cuenta).eq('id', cid).execute()
                    print(f"✅ BD: Cuenta {email_gmail} actualizada correctamente.")
                    return cid
                else:
                    nueva = supabase.table('cuentas_gmail').insert(datos_cuenta).execute()
                    print(f"✅ BD: Cuenta {email_gmail} insertada correctamente.")
                    return nueva.data[0]['id'] if nueva.data else None
            except Exception as e_bd:
                print(f"❌ Error en Supabase al guardar la cuenta: {e_bd}")
                raise e_bd

        # Ejecutamos la BD sin bloquear FastAPI
        cuenta_gmail_id = await asyncio.to_thread(_actualizar_bd)

        # --- 3. MOTOR AUTÓNOMO DE RENOVACIÓN DE TOKENS ---
        if token_recien_horneado:
            # Si acabamos de canjear el código con éxito, usamos ese token directamente
            print("⚡ Usando Access Token recién canjeado (Ahorrando llamada a Google).")
            gmail_token_fresco = gmail_token
        else:
            # Solo si el token viene viejo de Flutter (background), aplicamos la renovación
            def _obtener_refresh_token():
                res = supabase.table('cuentas_gmail').select('refresh_token').eq('usuario_id', usuario_id).eq('email_gmail', email_gmail).execute()
                if res.data and res.data[0].get('refresh_token'):
                    return res.data[0]['refresh_token']
                return None

            refresh_token_db = await asyncio.to_thread(_obtener_refresh_token)

            if not refresh_token_db:
                print("❌ No hay Refresh Token en la BD. Imposible operar.")
                raise HTTPException(status_code=401, detail="Debes volver a iniciar sesión con Google (Falta Refresh Token).")

            try:
                print("🔄 Generando un Access Token 100% fresco desde el Backend...")
                credenciales = Credentials(
                    token=None,
                    refresh_token=refresh_token_db,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=GOOGLE_CLIENT_ID,
                    client_secret=GOOGLE_CLIENT_SECRET
                )
                credenciales.refresh(GoogleAuthRequest())
                gmail_token_fresco = credenciales.token
                print("✅ Access Token renovado con éxito por el Backend.")
            except Exception as e:
                print(f"❌ Error fatal renovando token con Google: {e}")
                raise HTTPException(status_code=401, detail="El Refresh Token expiró o fue revocado. Vuelve a loguearte.")

        
        # 3. Inicializar servicio de Gmail
        from gmail_service import GmailService
        gmail = GmailService(access_token=gmail_token_fresco)
        
        # Ejecutamos el Batch Request en un hilo para no bloquear
        correos_gmail = await asyncio.to_thread(gmail.obtener_correos_no_leidos, 15)
        
        if not correos_gmail:
            return {
                "status": "success",
                "mensaje": "No hay correos nuevos en Gmail",
                "estadisticas": {"procesados": 0}
            }

        # ==============================================================================
        # 🔥 INICIO DE LA MODIFICACIÓN (FILTRO DE IDEMPOTENCIA)
        # ==============================================================================
        
        print(f"📥 Gmail devolvió {len(correos_gmail)} correos candidatos. Verificando duplicados...")

        # A. Extraemos solo los IDs de los correos que acabamos de bajar
        lista_ids_nuevos = [c['id'] for c in correos_gmail]

        def _buscar_duplicados():
            return supabase.table('correos_analizados').select('id_correo_gmail').in_('id_correo_gmail', lista_ids_nuevos).execute()

        try:
            existentes_response = await asyncio.to_thread(_buscar_duplicados)
            
            # C. Creamos una lista de "placas" que ya conocemos
            ids_ya_procesados = {item['id_correo_gmail'] for item in (existentes_response.data or [])}
            
        except Exception as e:
            print(f"⚠️ Advertencia: No se pudo verificar duplicados en Supabase ({e}). Se procesarán todos.")
            ids_ya_procesados = set()

        # D. EL FILTRO: Solo dejamos pasar los que NO están en la lista de procesados
        correos_a_procesar = [c for c in correos_gmail if c['id'] not in ids_ya_procesados]

        print(f"🛡️ Filtro aplicado: {len(ids_ya_procesados)} descartados. {len(correos_a_procesar)} irán a la IA.")

        # E. Si después del filtro no queda nada, terminamos aquí para no gastar dinero ni tiempo
        if not correos_a_procesar:
             return {
                "status": "success",
                "mensaje": "Todos los correos recientes ya habían sido analizados previamente.",
                "estadisticas": {
                    "procesados": 0, 
                    "omitidos_por_duplicidad": len(ids_ya_procesados)
                }
            }
            
       # 5. Obtener datos del usuario
        def _get_usuario():
            return supabase.table('usuarios').select('nombre, email').eq('id', usuario_id).execute()
            
        user_data = await asyncio.to_thread(_get_usuario)
        
        nombre_usuario = ""
        if user_data.data:
            nombre = user_data.data[0].get('nombre', '')
            email = user_data.data[0].get('email', '')
            nombre_usuario = nombre if nombre else email.split('@')[0]
        
       
        # 6. Procesar correos con el analizador inteligente
        resultado = await analizador_correos.procesar_lote_correos(
            correos=correos_a_procesar,
            usuario_id=usuario_id,
            gemini_client=gemini_client,
            supabase_client=supabase,
            nombre_usuario=nombre_usuario,
            cuenta_gmail_id=cuenta_gmail_id  # 🔥 NUEVO: Pasar ID de cuenta
        )
        
        # 7. Enviar notificaciones PUSH (solo correos críticos)
        if resultado.get('correos_criticos'):
            def _get_fcm():
                return supabase.table('usuarios').select('fcm_token').eq('id', usuario_id).execute()
            try:
                fcm_data = await asyncio.to_thread(_get_fcm)
                
                if fcm_data.data and fcm_data.data[0].get('fcm_token'):
                    token_fcm = fcm_data.data[0]['fcm_token']
                    correo_top = resultado['correos_criticos'][0]
                    
                    enviar_push(
                        token=token_fcm,
                        titulo=f"📧 Correo Urgente: {correo_top['correo']['asunto'][:50]}...",
                        cuerpo=f"De: {correo_top['correo']['de']}\n{correo_top['clasificacion']['resumen_corto']}",
                        data_extra={
                            "tipo": "CORREO_URGENTE",
                            "correo_id": correo_top['correo']['id'],
                            "ir_a": "correos"
                        }
                    )
            except Exception as e_notif:
                print(f"⚠️ Error enviando notificación: {e_notif}")
        
        # 8. Retornar estadísticas
        return {
            "status": "success",
            "mensaje": f"Analizados {resultado['procesados']} correos de {email_gmail or 'cuenta desconocida'}",
            "email_cuenta": email_gmail,
            "estadisticas": {
                "procesados": resultado['procesados'],
                "spam_descartado": resultado['spam_descartado'],
                "baja_prioridad": resultado['accion_baja'],
                "media_prioridad": resultado['accion_media'],
                "alta_prioridad": resultado['accion_alta']
            },
            "correos_importantes": len(resultado['correos_criticos']),
            "top_correo": resultado['correos_criticos'][0]['correo']['asunto'] if resultado['correos_criticos'] else None
        }
    
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        print(f"Error sincronizando correos: {e}")
        raise HTTPException(status_code=500, detail=str(e))












# ==============================================================================
# ======================================================================
# 🚀 ENDPOINTS API (ACTUALIZADOS CON AUTH)
# ======================================================================

@app.post("/chat")
async def chat_endpoint(
    entrada: MensajeEntrada,
    usuario_id: str = Depends(obtener_usuario_actual)
):
    """
    Cerebro Principal:
    1. TAREA -> Agenda con fecha calculada.
    2. VALOR -> Guarda historial y ACTUALIZA PERFIL (Memoria).
    3. CONSULTA -> Responde usando contexto, pero no ensucia la BD.
    """
    try:
        # 1. El Portero decide la intención (Igual que antes)
        decision = await clasificar_intencion_portero(entrada.mensaje)
        
        # CASO 1: Tarea explícita ("Recuérdame...")
        if decision['tipo'] == 'TAREA':
            # 🔥 CORRECCIÓN: Pasar SOLO el mensaje del usuario, SIN instrucciones
            res = await crear_tarea_directa(entrada.mensaje, usuario_id)
            return {"respuesta": res['respuesta'], "metadata": res.get('metadata', {})}
            
        # CASO 2: Información Valiosa ("Te paso el reporte", "Mi hija cumple años el...")
        elif decision['tipo'] == 'VALOR': 
             # Llamamos a tu función actualizada que ahora incluye MEMORIA
             res = await procesar_informacion_valor(entrada.mensaje, decision, usuario_id, "app_manual")
             
             # Agregamos 'nuevos_aprendizajes' al retorno por si el Frontend quiere mostrar "¿Sabías que aprendí esto?"
             return {
                 "respuesta": res['respuesta'], 
                 "alertas_generadas": res.get('alertas_generadas', 0),
                 "nuevos_aprendizajes": res.get('aprendizajes', 0) 
             }
             
        # CASO 3: Chat General / CONSULTA ("Hola", "¿Cómo estás?", "¿Qué tengo pendiente?")
        else:
            # Aquí responde dudas usando RAG (Memoria), pero NO guarda el "Hola" en la base de datos
            respuesta = await procesar_consulta_rapida(entrada.mensaje, usuario_id, entrada.modo_profundo)
            return {"respuesta": respuesta}

    except Exception as e:
        print(f"Error crítico en chat_endpoint: {e}")
        return {"respuesta": "Lo siento, tuve un problema interno procesando tu mensaje. Inténtalo de nuevo."}

@app.post("/api/analizar")
async def analizar_archivos(
    files: List[UploadFile] = File(...),
    usuario_id: str = Depends(obtener_usuario_actual)
):
    """
    Analizar archivos. Ahora autenticado.
    """
    texto = ""
    for f in files:
        c = await f.read()
        texto += f"\n{c.decode('utf-8', errors='ignore')}"
    
    res = await procesar_informacion_valor(texto[:30000], {"subtipo": "analisis_archivo", "urgencia": "MEDIA"}, usuario_id, "app_archivo")
    return {"status": "success", "data": res}

@app.get("/api/alertas")
async def obtener_alertas(
    estado: str = "pendiente",
    usuario_id: str = Depends(obtener_usuario_actual)
):
    """
    Obtener alertas DEL USUARIO ACTUAL.
    """
    if not supabase: return {"alertas": []}
    
    q = supabase.table('alertas').select('*').eq('usuario_id', usuario_id).order('created_at', desc=True)
    
    if estado == "completada":
        # Solo las completadas NO archivadas o de últimas 2 semanas
        q = q.eq('estado', 'completada').or_(
            'archivado_en.is.null,archivado_en.gt.{}'.format(
                (datetime.now() - timedelta(days=14)).isoformat()
            )
        )
    elif estado != "todas":
        q = q.eq('estado', estado)
    
    return {"alertas": q.order('created_at', desc=True).execute().data}

@app.get("/api/alertas/prioritarias")
async def obtener_alertas_prioritarias(
    limite: int = 20,
    usuario_id: str = Depends(obtener_usuario_actual)
):
    """
    Obtiene alertas ordenadas por score de urgencia.
    Usa la vista SQL creada anteriormente.
    """
    if not supabase:
        return {"alertas": []}
    
    try:
        # Usar la vista SQL que ordena por score
        resultado = supabase.from_('alertas_prioritarias')\
            .select('*')\
            .eq('usuario_id', usuario_id)\
            .limit(limite)\
            .execute()
        
        return {"alertas": resultado.data, "total": len(resultado.data)}
    
    except Exception as e:
        print(f"Error obteniendo prioritarias: {e}")
        # Fallback: Query normal
        return {"alertas": [], "error": str(e)}

@app.patch("/api/alertas/{alerta_id}")
async def actualizar_alerta(
    alerta_id: str, 
    body: ActualizarAlerta,
    usuario_id: str = Depends(obtener_usuario_actual)
):
    """
    Actualizar alerta (solo si pertenece al usuario).
    """
    if not supabase: return {"status": "error"}
    
    # Verificar que la alerta pertenezca al usuario
    alerta_existente = supabase.table('alertas').select('usuario_id').eq('id', alerta_id).execute()
    
    if not alerta_existente.data or alerta_existente.data[0]['usuario_id'] != usuario_id:
        raise HTTPException(status_code=403, detail="No tienes permiso para modificar esta alerta")
    
    # 2. Construcción DINÁMICA de datos (La solución real)
    # exclude_unset=True hace que solo se incluyan los campos que realmente enviaste desde Flutter
    datos_actualizar = body.dict(exclude_unset=True)
    
    if not datos_actualizar:
        return {"status": "no_change", "msg": "No se enviaron datos para actualizar"}

    # 3. Guardar en Supabase
    try:
        res = supabase.table('alertas').update(datos_actualizar).eq('id', alerta_id).execute()
        return {"status": "success", "data": res.data}
    except Exception as e:
        # Importante: Retornar error 400 permite a Flutter saber que algo falló
        raise HTTPException(status_code=400, detail=str(e))

# 🔥 WEBHOOK WHATSAPP (CON RECONOCIMIENTO BIOMÉTRICO/TELÉFONO)
@app.post("/webhook")
async def webhook_whatsapp(request: Request):
    """
    1. Recibe el mensaje de Twilio.
    2. Extrae el número de teléfono limpio.
    3. Busca en la BD 'usuarios' a quién pertenece ese número (MATCH).
    4. Si existe, usa su UUID real. Si no, usa el ID 'Invitado'.
    5. Procesa la intención y responde.
    """
    # 1. RECIBIR DATOS RAW
    form_data = await request.form()
    data = dict(form_data)
    mensaje = data.get('Body', '').strip()
    remitente_raw = data.get('From', '') # Llega como 'whatsapp:+51999...'
    
    # 2. LIMPIEZA DE DATOS (EL MATCH)
    # Quitamos "whatsapp:" para quedarnos solo con el número (+51...)
    telefono_usuario = remitente_raw.replace("whatsapp:", "").strip()

    if not mensaje: 
        return Response(content="<?xml version='1.0'?><Response/>", media_type="application/xml")

    print(f"📩 WhatsApp de {telefono_usuario}: {mensaje}")

    # ==============================================================================
    # 3. IDENTIFICACIÓN DE USUARIO (La Lógica de Vinculación)
    # ==============================================================================
    # Por defecto, asumimos que es un desconocido (Invitado)
    usuario_activo_id = "00000000-0000-0000-0000-000000000000"
    usuario_nombre = "Invitado" # Para ser amables en la respuesta
    
    try:
        # CONSULTA CLAVE: Buscamos el UUID usando el teléfono como llave
        res_user = supabase.table('usuarios')\
            .select('id, nombre')\
            .eq('telefono', telefono_usuario)\
            .execute()
            
        # Si la lista NO está vacía, ¡HAY MATCH!
        if res_user.data and len(res_user.data) > 0:
            usuario_activo_id = res_user.data[0]['id'] # Tomamos el UUID real
            # Tomamos el nombre si existe, si no, le decimos "Usuario"
            usuario_nombre = res_user.data[0].get('nombre') or "Usuario"
            print(f"✅ MATCH EXITOSO: {usuario_nombre} ({usuario_activo_id})")
        else:
            print(f"⚠️ Teléfono {telefono_usuario} no encontrado en BD. Tratando como Invitado.")
            
    except Exception as e:
        print(f"❌ Error en el proceso de match: {e}")
        # Si falla la BD, seguimos como invitado para no colgar el chat

    # ==============================================================================
    # 4. CEREBRO: CLASIFICACIÓN (Tu lógica original intacta)
    # ==============================================================================
    decision = await clasificar_intencion_portero(mensaje)
    tipo = decision.get('tipo', 'CONSULTA')
    respuesta_texto = ""

    # ==============================================================================
    # 5. EJECUCIÓN (Usando el ID que encontramos en el paso 3)
    # ==============================================================================
    if tipo == "VALOR":
        await procesar_informacion_valor(mensaje, decision, usuario_activo_id, "whatsapp_webhook")
        
        # Notificación Urgente
        urgencia = decision.get("urgencia", "MEDIA")
        if urgencia in ["ALTA", "CRITICA", "URGENTE"]:
            enviar_notificacion_inteligente(
                usuario_activo_id, 
                "🚨 Atención Requerida", 
                decision.get('resumen', 'Nueva alerta importante')
            )
        respuesta_texto = "Recibido. He procesado la información."

    elif tipo == "TAREA":
        # ¡AQUÍ ES DONDE OCURRE LA MAGIA!
        # Se guarda la tarea con el ID real, por eso aparece en tu App.
        await crear_tarea_directa(mensaje, usuario_activo_id)
        respuesta_texto = f"✅ Listo {usuario_nombre}, tarea agendada."

    else: 
        # TIPO: "CONSULTA" (Chat)
        print("💬 Chat IA activado...")
        # Respondemos usando el historial real de ese usuario
        respuesta_texto = await procesar_consulta_rapida(mensaje, usuario_activo_id, modo_profundo=False)

    # ==============================================================================
    # 6. SALIDA XML (Para que le llegue el mensaje al celular)
    # ==============================================================================
    xml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Message>{respuesta_texto}</Message>
    </Response>"""
    
    return Response(content=xml_response, media_type="application/xml")

# Instancia global del analizador
analizador_correos = AnalizadorCorreos()

# ==============================================================================
# 📧 ENDPOINTS DE CORREOS (CON GMAIL API REAL)
# ==============================================================================

@app.post("/api/sincronizar-correos")
async def sincronizar_correos(
    request: Request,
    usuario_id: str = Depends(obtener_usuario_actual)
):
    body = await request.json()
    
    # Llamamos a la lógica respetando tus nombres de variables
    res = await ejecutar_logica_sincronizacion(
        usuario_id=usuario_id,
        gmail_token = body.get('gmail_access_token'),
        email_gmail = body.get('email_gmail'),
        server_auth_code = body.get('server_auth_code'),
        gmail_refresh_token=body.get('refresh_token')
    )
    
    if "error" in res:
        raise HTTPException(status_code=400, detail=res["error"])
    return res




@app.post("/api/enviar-correo")
async def enviar_correo_endpoint(
    request: Request,
    usuario_id: str = Depends(obtener_usuario_actual)
):
    """
    Envía un correo usando la cuenta de Gmail del usuario.
    """
    # --- 🔥 ZONA DE CONFIGURACIÓN ---
    GOOGLE_CLIENT_ID = "269344577878-gnf64lmpd3hcnlfsl1i5brduqvqq49na.apps.googleusercontent.com"
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    try:
        body = await request.json()
        
        gmail_token = body.get('gmail_access_token')
        destinatario = body.get('destinatario')
        asunto = body.get('asunto')
        cuerpo = body.get('cuerpo')
        thread_id = body.get('thread_id')  # Para respuestas
        email_gmail = body.get('email_gmail')
        # ✅ NUEVA VALIDACIÓN: Exigimos email_gmail en lugar del token viejo
        if not all([email_gmail, destinatario, asunto, cuerpo]):
            raise HTTPException(status_code=400, detail="Faltan parámetros: email_gmail, destinatario, asunto o cuerpo")
        # --- MOTOR AUTÓNOMO DE RENOVACIÓN DE TOKENS ---
        def _obtener_refresh_token():
            res = supabase.table('cuentas_gmail').select('refresh_token').eq('usuario_id', usuario_id).eq('email_gmail', email_gmail).execute()
            if res.data and res.data[0].get('refresh_token'):
                return res.data[0]['refresh_token']
            return None

        refresh_token_db = await asyncio.to_thread(_obtener_refresh_token)

        if not refresh_token_db:
            raise HTTPException(status_code=401, detail="No hay Refresh Token asociado a esta cuenta.")

        try:
            credenciales = Credentials(
                token=None,
                refresh_token=refresh_token_db,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=GOOGLE_CLIENT_ID,
                client_secret=GOOGLE_CLIENT_SECRET
            )
            credenciales.refresh(GoogleAuthRequest())
            gmail_token_fresco = credenciales.token
        except Exception as e:
            raise HTTPException(status_code=401, detail="El Refresh Token expiró. Vuelve a loguearte.")

        # Enviar correo usando el token recién fabricado
        from gmail_service import GmailService
        gmail = GmailService(access_token=gmail_token_fresco)
        exito = await asyncio.to_thread(gmail.enviar_correo, destinatario, asunto, cuerpo, thread_id)
        if exito:
            return {"status": "success", "mensaje": "Correo enviado"}
        else:
            raise HTTPException(status_code=500, detail="Error enviando correo")
    
    except Exception as e:
        print(f"Error en endpoint enviar: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# EN TU BACKEND (Python)
@app.get("/api/correos-pendientes")
async def obtener_correos_pendientes(
    usuario_id: str = Depends(obtener_usuario_actual),
    filtro: str = "todos"  # 🔥 NUEVO PARÁMETRO (por defecto 'todos')
):
    try:
        query = supabase.table('correos_analizados')\
            .select('*')\
            .eq('usuario_id', usuario_id)\
            .order('fecha', desc=True)\
            .limit(50) # Ojo con el límite, quizás quieras subirlo para "todos"

        # 🔥 LÓGICA DE FILTRADO
        if filtro == "pendientes":
            query = query.eq('requiere_accion', True)
        # Si filtro es "todos", NO aplicamos el .eq('requiere_accion', True)
        
        correos = query.execute()
        return {"correos": correos.data}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/correos/{correo_id}/marcar-leido")
async def marcar_como_leido(
    correo_id: str, 
    usuario_id: str = Depends(obtener_usuario_actual)
):
    # --- 🔥 ZONA DE CONFIGURACIÓN ---
    GOOGLE_CLIENT_ID = "269344577878-gnf64lmpd3hcnlfsl1i5brduqvqq49na.apps.googleusercontent.com"
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

    try:
        # 1. Obtener metadatos y el Refresh Token (NO el Access Token)
        # 🔴 CORRECCIÓN IDOR: Añadimos .eq('usuario_id', usuario_id) por seguridad
        def _obtener_datos():
            return supabase.table('correos_analizados')\
                .select('metadata, cuenta_gmail_id, cuentas_gmail(refresh_token)')\
                .eq('id', correo_id)\
                .eq('usuario_id', usuario_id)\
                .single()\
                .execute()

        datos_correo = await asyncio.to_thread(_obtener_datos)
            
        if not datos_correo.data:
            raise HTTPException(status_code=404, detail="Correo no encontrado o no autorizado")

        gmail_msg_id = datos_correo.data['metadata'].get('correo_id_gmail')
        
        # Extraemos la "Llave Inmortal"
        refresh_token_db = None
        if datos_correo.data.get('cuentas_gmail'):
            refresh_token_db = datos_correo.data['cuentas_gmail'].get('refresh_token')

        if not refresh_token_db:
             raise HTTPException(status_code=401, detail="No hay Refresh Token. El usuario debe re-autenticar.")

        # 2. 🔥 MOTOR AUTÓNOMO DE RENOVACIÓN DE TOKENS (El aniquilador del 401)
        def _renovar_token():
            credenciales = Credentials(
                token=None,
                refresh_token=refresh_token_db,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=GOOGLE_CLIENT_ID,
                client_secret=GOOGLE_CLIENT_SECRET
            )
            credenciales.refresh(GoogleAuthRequest())
            return credenciales.token

        try:
            gmail_token_fresco = await asyncio.to_thread(_renovar_token)
        except Exception as e:
            print(f"❌ Error renovando token para marcar leído: {e}")
            raise HTTPException(status_code=401, detail="El Refresh Token expiró o fue revocado. Vuelve a loguearte.")

        # 3. Sincronizar con GMAIL (Nube) PRIMERO
        # 🔴 CORRECCIÓN SPLIT-BRAIN: Primero aseguramos Google, luego nuestra BD
        if gmail_msg_id:
            try:
                from gmail_service import GmailService
                service = GmailService(access_token=gmail_token_fresco)
                
                # Ejecutamos en un hilo separado para no bloquear FastAPI
                exito_gmail = await asyncio.to_thread(service.marcar_como_leido, gmail_msg_id)
                
                if exito_gmail:
                    print(f"✅ Sincronizado con Gmail (Leído): {gmail_msg_id}")
                else:
                     print(f"⚠️ Falló en Gmail internamente, pero el token era válido: {gmail_msg_id}")
                     
            except Exception as e_gmail:
                # Si Gmail se cae por red, logueamos pero continuamos para no romper la app
                print(f"⚠️ Error de red con Gmail al marcar leído: {e_gmail}")

        # 4. Actualizar en SUPABASE (Local) DESPUÉS
        def _actualizar_bd():
            return supabase.table('correos_analizados')\
                .update({'leido': True, 'requiere_accion': False})\
                .eq('id', correo_id)\
                .execute()

        await asyncio.to_thread(_actualizar_bd)

        return {"mensaje": "Marcado como leído correctamente y sincronizado con Google."}

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ Error crítico en marcar_como_leido: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==============================================================================
# 📧 ENDPOINTS DE CORREOS RESPONDIDOS
# ==============================================================================

@app.get("/api/correos-respondidos")
async def obtener_correos_respondidos(
    limite: int = 50,
    usuario_id: str = Depends(obtener_usuario_actual)
):
    """
    Obtiene el historial de correos respondidos por el usuario.
    Incluye la respuesta que se envió.
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="BD no disponible")
    
    try:
        # Consulta con JOIN para obtener info completa
        correos = supabase.table('correos_analizados')\
            .select('*')\
            .eq('usuario_id', usuario_id)\
            .eq('respondido', True)\
            .order('fecha_respuesta', desc=True)\
            .limit(limite)\
            .execute()
        
        return {
            "status": "success",
            "correos": correos.data,
            "total": len(correos.data)
        }
    
    except Exception as e:
        print(f"Error obteniendo respondidos: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/correos/{correo_id}/marcar-respondido")
async def marcar_como_respondido(
    correo_id: str,
    body: dict = Body(...),
    usuario_id: str = Depends(obtener_usuario_actual)
):
    """
    Marca un correo como respondido y guarda la respuesta enviada.
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="BD no disponible")
    
    try:
        # Verificar que el correo pertenezca al usuario
        correo = supabase.table('correos_analizados')\
            .select('id')\
            .eq('id', correo_id)\
            .eq('usuario_id', usuario_id)\
            .execute()
        
        if not correo.data:
            raise HTTPException(status_code=404, detail="Correo no encontrado")
        
        # Actualizar
        supabase.table('correos_analizados').update({
            'respondido': True,
            'fecha_respuesta': body.get('fecha_respuesta'),
            'leido': True,
            'requiere_accion': False,
            'metadata': {
                **correo.data[0].get('metadata', {}),
                'respuesta_enviada': body.get('respuesta_enviada')
            }
        }).eq('id', correo_id).execute()
        
        return {"status": "success", "message": "Correo marcado como respondido"}
    
    except Exception as e:
        print(f"Error marcando respondido: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/correos/{correo_id}/revertir-respondido")
async def revertir_respondido(
    correo_id: str,
    usuario_id: str = Depends(obtener_usuario_actual)
):
    """
    Revierte un correo respondido a pendiente.
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="BD no disponible")
    
    try:
        correo = supabase.table('correos_analizados')\
            .select('id')\
            .eq('id', correo_id)\
            .eq('usuario_id', usuario_id)\
            .execute()
        
        if not correo.data:
            raise HTTPException(status_code=404, detail="Correo no encontrado")
        
        supabase.table('correos_analizados').update({
            'respondido': False,
            'fecha_respuesta': None,
            'requiere_accion': True,
        }).eq('id', correo_id).execute()
        
        return {"status": "success", "message": "Correo revertido"}
    
    except Exception as e:
        print(f"Error revirtiendo: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== ENDPOINTS NEXUS ====================

# =====================================================================
# 🧠 FUNCIÓN NÚCLEO (EL CEREBRO INTERNO)
# Esta función hace el trabajo pesado en segundo plano. No depende de HTTP.
# =====================================================================
async def procesar_cerebro_interno(usuario_id_real: str):
    print(f"🚀 [BACKGROUND] Iniciando Cerebro silencioso para usuario: {usuario_id_real}")
    
    try:
        # 1. Obtener mensajes pendientes
        response = supabase.table('mensajes_whatsapp')\
            .select('*')\
            .eq('usuario_id', usuario_id_real)\
            .eq('procesado_ia', False)\
            .order('chat_nombre', desc=False)\
            .order('timestamp', desc=False)\
            .execute()
        
        mensajes = response.data
        if not mensajes:
            print(f"💤 [BACKGROUND] Sin mensajes nuevos para {usuario_id_real}")
            return {"status": "sleep", "mensaje": "No hay mensajes nuevos."}
            
        resultados_log = []
        mensajes.sort(key=lambda x: x['chat_nombre'])
        
        # 2. Procesar por chat
        for chat_nombre, grupo in groupby(mensajes, key=lambda x: x['chat_nombre']):
            lista_mensajes = list(grupo)
            texto_total = " ".join([m['contenido'] for m in lista_mensajes])
            
            # Filtro de ruido
            if len(lista_mensajes) < 2 and len(texto_total) < 10:
                ids_ruido = [m['id'] for m in lista_mensajes]
                for mid in ids_ruido:
                    supabase.table('mensajes_whatsapp').update({'procesado_ia': True}).eq('id', mid).execute()
                continue

            print(f"🤖 [BACKGROUND] Analizando chat: {chat_nombre} ({len(lista_mensajes)} msgs)")
            
            try:
                # Memoria Previa
                memoria_db = supabase.table('memoria_chats')\
                    .select('*')\
                    .eq('chat_nombre', chat_nombre)\
                    .eq('usuario_id', usuario_id_real)\
                    .execute()
                    
                contexto_previo = memoria_db.data[0].get('resumen_actual', 'Sin historial previo.') if memoria_db.data else "Sin historial previo."

                # Indexación ChromaDB
                transcripcion = ""
                ids_a_procesar = []
                ultimo_timestamp = ""
                
                try:
                    emb_model = get_embedding_model()
                except:
                    emb_model = None

                for m in lista_mensajes:
                    autor = "YO" if m['es_mio'] else chat_nombre
                    transcripcion += f"[{m['timestamp']}] {autor}: {m['contenido']}\n"
                    ids_a_procesar.append(m['id'])
                    ultimo_timestamp = m['timestamp']

                    if emb_model and m['contenido'] and len(m['contenido']) > 5:
                        try:
                            vector = emb_model.encode([m['contenido']])[0].tolist()
                            collection_mensajes.add(
                                ids=[str(m['id'])],
                                embeddings=[vector],
                                documents=[m['contenido']],
                                metadatas=[{
                                    "chat_nombre": chat_nombre,
                                    "usuario_id": usuario_id_real,
                                    "fecha": m['timestamp'],
                                    "es_mio": m['es_mio']
                                }]
                            )
                        except Exception as e_chroma:
                            print(f"   ⚠️ Error indexando: {e_chroma}")

                # Prompt Gemini
                prompt = f"""
                Actúa como un Analista de Datos Personales experto.
                
                CONTEXTO ANTERIOR (Resumen de lo hablado antes):
                "{contexto_previo}"
                
                NUEVA CONVERSACIÓN (Mensajes recientes):
                {transcripcion}
                
                TU OBJETIVO:
                Generar un JSON válido con 3 campos obligatorios:
                
                1. "nuevo_resumen": Un párrafo que combine el contexto anterior con lo nuevo. Si el tema cambió drásticamente, descarta lo viejo irrelevante. Mantén fechas y acuerdos.
                2. "tareas": Una lista de objetos. Si no hay tareas, lista vacía []. Cada objeto debe tener:
                - "titulo": Breve (ej: "Comprar leche")
                - "descripcion": Detalles (ej: "Marca X, para mañana")
                - "prioridad": "ALTA", "MEDIA" o "BAJA"
                3. "intencion": "TRABAJO", "PERSONAL", "VENTAS" o "OTROS".

                IMPORTANTE: Responde SOLO con el JSON. No uses Markdown.
                """


                # Llamada IA
                respuesta_ai = model.generate_content(prompt).text
                datos_ia = limpiar_json_gemini(respuesta_ai)
                
                # Guardar Memoria
                datos_memoria = {
                    'chat_nombre': chat_nombre,
                    'usuario_id': usuario_id_real,
                    'resumen_actual': datos_ia.get('nuevo_resumen', 'No se generó resumen.'),
                    'ultima_actualizacion': datetime.utcnow().isoformat(),
                    'temas_abiertos': datos_ia.get('intencion', 'OTROS')
                }
                supabase.table('memoria_chats').upsert(datos_memoria).execute()

                # Guardar Tareas
                tareas_detectadas = datos_ia.get('tareas', [])
                for tarea in tareas_detectadas:
                    supabase.table('alertas').insert({
                        'usuario_id': usuario_id_real,
                        'titulo': f"⚡ {tarea.get('titulo', 'Nueva tarea')}",
                        'descripcion': f"Origen: {chat_nombre}. {tarea.get('descripcion', '')}",
                        'tipo': 'tarea_ia',
                        'prioridad': tarea.get('prioridad', 'MEDIA').upper(),
                        'metadata': {'origen': 'whatsapp_cerebro', 'chat': chat_nombre}
                    }).execute()

                # Marcar como procesados
                if ids_a_procesar:
                    supabase.table('mensajes_whatsapp').update({'procesado_ia': True}).in_('id', ids_a_procesar).execute()

                resultados_log.append({"chat": chat_nombre, "tareas_creadas": len(tareas_detectadas)})

            except Exception as e_chat:
                print(f"❌ Error en chat {chat_nombre}: {e_chat}")
                continue
                
        print(f"✅ [BACKGROUND] Cerebro finalizó para {usuario_id_real}")
        return {"status": "success", "resumen": resultados_log}
        
    except Exception as e:
        print(f"❌ [BACKGROUND] Error fatal en cerebro: {e}")
        return {"status": "error", "detalle": str(e)}

# main.py - VERSIÓN CORREGIDA PARA LEER EL TOKEN DE ANDROID

@app.post("/nexus/sync/batch")
async def sincronizar_batch_nexus(
    request: Request,
    background_tasks: BackgroundTasks,
    x_batch_size: str = Header(None),
    x_device_id: str = Header(None),
    content_encoding: str = Header(None),
    authorization: str = Header(None) # 1. RECIBIMOS EL TOKEN AQUÍ
    
):
    """
    Recibe mensajes de WhatsApp, VALIDA EL USUARIO y los procesa.
    """
    
    # 2. VALIDAR AUTENTICACIÓN (CRÍTICO)
    if not authorization:
        raise HTTPException(status_code=401, detail="Falta el Token de autenticación")

    try:
        # El formato es "Bearer <token>", extraemos solo el token
        token = authorization.split(" ")[1]
        
        # Le preguntamos a Supabase: "¿De quién es este token?"
        user_response = supabase.auth.get_user(token)
        
        if not user_response.user:
            raise HTTPException(status_code=401, detail="Token inválido o expirado")
            
        # 3. OBTENEMOS EL ID REAL DEL USUARIO
        USER_ID_REAL = user_response.user.id
        print(f"👤 Autenticado exitosamente: {USER_ID_REAL}")

    except Exception as e:
        print(f"❌ Error de autenticación: {e}")
        raise HTTPException(status_code=401, detail="Error de autenticación")

    try:
        # Descomprimir (Igual que antes)
        body = await request.body()
        if content_encoding == "gzip":
            body = gzip.decompress(body)
        mensajes_raw = json.loads(body)
        
        datos_para_insertar = []
        
        for msg in mensajes_raw:
            # Preparamos el objeto para Supabase
            datos_para_insertar.append({
                'id': msg['id'],
                'usuario_id': USER_ID_REAL, # Tu variable de usuario validado
                'chat_id': msg['chatId'],
                'chat_nombre': msg['chatNombre'],
                'contenido': msg['contenido'],
                'timestamp': msg['timestamp'],
                'es_mio': msg['esMio'],
                'tipo': msg['tipo'],
                'device_id': x_device_id,
                'sincronizado': True,
                'procesado_ia': False # <--- ESTO ES LO NUEVO: Entran como "pendientes"
            })

        # Insertamos todo de golpe (Bulk Insert es más eficiente)
        if datos_para_insertar:
            supabase.table('mensajes_whatsapp').upsert(datos_para_insertar).execute()
            print(f"✅ Ingesta Rápida: {len(datos_para_insertar)} mensajes guardados (Pendientes de análisis).")

            # 🔥 AQUÍ OCURRE LA MAGIA 🔥
            # Le decimos a FastAPI: "Cuando devuelvas el return 200, ejecuta esto"
            background_tasks.add_task(procesar_cerebro_interno, USER_ID_REAL)
        return {
            "status": "success",
            "mode": "ingesta_rapida", # Confirmación de que no gastaste tokens
            "mensajes_guardados": len(datos_para_insertar)
        }
        
    except Exception as e:
        print(f"❌ Error en Batch: {e}")
        raise HTTPException(500, f"Error interno: {str(e)}")


# =====================================================================
# 2️⃣ ENDPOINT: ACTIVACIÓN MANUAL (Para pruebas o forzado desde Flutter)
# =====================================================================
@app.post("/nexus/cerebro/activar")
async def activar_cerebro_inteligente(authorization: str = Header(None)):
    """Mantenemos este endpoint intacto por si quieres forzar la IA manualmente"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Falta Token")
    try:
        token = authorization.split(" ")[1]
        user_response = supabase.auth.get_user(token)
        if not user_response.user:
            raise HTTPException(status_code=401, detail="Token inválido")
        USER_ID_REAL = user_response.user.id
    except Exception as e:
        raise HTTPException(status_code=401, detail="Error auth")

    # Llamamos a la misma función interna y esperamos a que termine
    resultado = await procesar_cerebro_interno(USER_ID_REAL)
    return resultado






# main.py - AÑADIR ESTA FUNCIÓN

# main.py - VERSIÓN CORREGIDA Y COMPATIBLE



# main.py - AÑADIR NUEVO ENDPOINT
@app.get("/nexus/estadisticas/{usuario_id}")
async def obtener_estadisticas_nexus(usuario_id: str):
    """
    Obtiene estadísticas de mensajes de WhatsApp procesados para Flutter
    """
    try:
        # 1. Total de mensajes
        total_mensajes = supabase.table('mensajes_whatsapp')\
            .select('*', count='exact')\
            .eq('usuario_id', usuario_id)\
            .execute()
            
        # 2. Mensajes de hoy (Calculamos fecha inicio del día)
        hoy_str = datetime.utcnow().date().isoformat() # Ej: "2023-10-27"
        
        mensajes_hoy = supabase.table('mensajes_whatsapp')\
            .select('*', count='exact')\
            .eq('usuario_id', usuario_id)\
            .gte('created_at', hoy_str)\
            .execute()
            
        # 3. Alertas generadas (Urgentes)
        alertas = supabase.table('alertas')\
            .select('*', count='exact')\
            .eq('usuario_id', usuario_id)\
            .eq('tipo', 'urgente_whatsapp')\
            .execute()
            
        # 4. Chats activos (Contamos nombres únicos)
        # Nota: Traemos solo la columna chat_nombre para ser eficientes
        chats = supabase.table('mensajes_whatsapp')\
            .select('chat_nombre')\
            .eq('usuario_id', usuario_id)\
            .execute()
            
        # Usamos un Set de Python para eliminar duplicados y contar
        chats_unicos = len(set([msg['chat_nombre'] for msg in chats.data if msg.get('chat_nombre')]))
            
        # 5. Fecha del último mensaje sincronizado
        ultimo_mensaje = supabase.table('mensajes_whatsapp')\
            .select('created_at')\
            .eq('usuario_id', usuario_id)\
            .order('created_at', desc=True)\
            .limit(1)\
            .execute()
            
        ultimo_sync = None
        if ultimo_mensaje.data:
            ultimo_sync = ultimo_mensaje.data[0]['created_at']
            
        return {
            "total_mensajes": total_mensajes.count or 0,
            "mensajes_hoy": mensajes_hoy.count or 0,
            "alertas_generadas": alertas.count or 0,
            "chats_activos": chats_unicos,
            "ultimo_sync": ultimo_sync,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        print(f"❌ Error obteniendo estadísticas: {e}")
        # Importante: Retornamos un 500 para que Flutter sepa que falló
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/nexus/health")
async def nexus_health():
    """Health check de Nexus"""
    return {
        "status": "healthy",
        "service": "nexus",
        "timestamp": datetime.utcnow().isoformat()
    }


# ========== ENDPOINTS MULTIMEDIA ==========

@app.post("/nexus/transcribir_audio")
async def transcribir_audio(
    background_tasks: BackgroundTasks,
    archivo: UploadFile = File(...),
    mensaje_id: str = Form(...),   # <--- CORRECCIÓN: Form(...) lee del multipart body
    chat_nombre: str = Form(...)   # <--- CORRECCIÓN: Form(...) lee del multipart body
):
    """
    Transcribe un audio de WhatsApp usando Whisper
    """
    try:
        print(f"🎤 Recibiendo audio para transcripción: {archivo.filename}")
        
        # Guardar archivo temporalmente
        with tempfile.NamedTemporaryFile(delete=False, suffix=".opus") as temp_file:
            contenido = await archivo.read()
            temp_file.write(contenido)
            ruta_temp = temp_file.name
        
        # Encolar transcripción en background
        background_tasks.add_task(
            procesar_transcripcion,
            ruta_temp,
            mensaje_id,
            chat_nombre
        )
        
        return {
            "status": "encolado",
            "mensaje_id": mensaje_id,
            "mensaje": "Transcripción iniciada en background"
        }
        
    except Exception as e:
        print(f"❌ Error recibiendo audio: {e}")
        raise HTTPException(500, str(e))


async def procesar_transcripcion(
    ruta_audio: str,
    mensaje_id: str,
    chat_nombre: str
):
    """
    Procesa la transcripción de un audio
    """
    try:
        print(f"🔄 Transcribiendo audio: {mensaje_id}")
        
        # Obtener modelo Whisper
        model = get_whisper_model()
        
        # Transcribir
        segments, info = model.transcribe(
            ruta_audio,
            language="es",  # Español
            beam_size=5,     # Balance entre precisión y velocidad
            vad_filter=True  # Filtrar silencios
        )
        
        # Unir todos los segmentos
        texto_completo = " ".join([segment.text for segment in segments])
        
        print(f"✅ Transcripción completada: '{texto_completo[:50]}...'")
        print(f"   Idioma detectado: {info.language} (confianza: {info.language_probability:.2%})")
        
        # GUARDAR EN SUPABASE
        # NOTA: No llamamos a IA aquí. Solo guardamos y marcamos procesado_ia = FALSE
        # para que el 'Cerebro' lo analice después con todo el contexto.
        supabase.table('mensajes_whatsapp').update({
            'contenido': f"[AUDIO TRANSCRITO] {texto_completo}", # Actualizamos el contenido visible
            'metadata': {
                'es_audio': True,
                'transcripcion_original': texto_completo,
                'idioma': info.language,
                'confianza': info.language_probability
            },
            'procesado_ia': False # <--- IMPORTANTE: Esto dispara al Cerebro después
        }).eq('id', mensaje_id).execute()

        print(f"✅ Audio guardado. Pendiente de análisis por el Cerebro.")
        
        
        
    except Exception as e:
        print(f"❌ Error procesando transcripción: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        # Limpiar archivo temporal
        try:
            os.remove(ruta_audio)
        except:
            pass

@app.post("/nexus/procesar_imagen")
async def procesar_imagen(
    background_tasks: BackgroundTasks,
    archivo: UploadFile = File(...),
    mensaje_id: str = None,
    chat_nombre: str = None
):
    """
    Procesa una imagen de WhatsApp con OCR
    """
    try:
        print(f"🖼️ Recibiendo imagen: {archivo.filename}")
        
        # Leer contenido
        contenido = await archivo.read()
        
        # Generar hash para detectar duplicados
        hash_imagen = hashlib.sha256(contenido).hexdigest()
        
        # Verificar si ya procesamos esta imagen
        existente = supabase.table('imagenes_procesadas')\
            .select('*')\
            .eq('hash', hash_imagen)\
            .execute()
        
        if existente.data:
            print(f"♻️ Imagen duplicada: {hash_imagen[:8]}")
            return {
                "status": "duplicado",
                "hash": hash_imagen,
                "resultado_id": existente.data[0]['id']
            }
        
        # Guardar temporalmente
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
            temp_file.write(contenido)
            ruta_temp = temp_file.name
        
        # Encolar procesamiento
        background_tasks.add_task(
            procesar_ocr_imagen,
            ruta_temp,
            hash_imagen,
            mensaje_id,
            chat_nombre
        )
        
        return {
            "status": "procesando",
            "hash": hash_imagen,
            "mensaje_id": mensaje_id
        }
        
    except Exception as e:
        print(f"❌ Error recibiendo imagen: {e}")
        raise HTTPException(500, str(e))


async def procesar_ocr_imagen(
    ruta_imagen: str,
    hash_imagen: str,
    mensaje_id: str,
    chat_nombre: str
):
    """
    Extrae texto de una imagen usando OCR
    """
    try:
        print(f"🔍 Procesando OCR: {mensaje_id}")
        
        # Abrir imagen
        imagen = Image.open(ruta_imagen)
        
        # Extraer texto con Tesseract
        texto_extraido = pytesseract.image_to_string(imagen, lang='spa')
        
        tiene_texto = len(texto_extraido.strip()) > 10
        
        if tiene_texto:
            print(f"✅ Texto extraído: '{texto_extraido[:50]}...'")
            
            # Actualizar mensaje en Supabase
            supabase.table('mensajes_whatsapp').update({
                'metadata': {
                    'texto_ocr': texto_extraido,
                    'tiene_texto': True,
                    'procesado_ocr_en': datetime.utcnow().isoformat()
                }
            }).eq('id', mensaje_id).execute()
            
            # Procesar con IA
            await procesar_mensaje_whatsapp_ia(
                mensaje_id=mensaje_id,
                contenido=texto_extraido,  # ← Usar texto de imagen
                chat_nombre=chat_nombre,
                usuario_id='00000000-0000-0000-0000-000000000000'
            )
        else:
            print(f"ℹ️ Imagen sin texto significativo")
        
        # Guardar registro de imagen procesada
        supabase.table('imagenes_procesadas').insert({
            'hash': hash_imagen,
            'mensaje_id': mensaje_id,
            'texto_ocr': texto_extraido if tiene_texto else None,
            'tiene_texto': tiene_texto
        }).execute()
        
        print(f"✅ Imagen procesada completamente")
        
    except Exception as e:
        print(f"❌ Error procesando OCR: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        try:
            os.remove(ruta_imagen)
        except:
            pass

@app.post("/nexus/buscar_semantica")
async def buscar_semantica(
    query: str,
    usuario_id: str, # Recibimos usuario para futuros filtros, aunque Chroma es global por ahora
    limite: int = 5
):
    """
    Búsqueda semántica inteligente ("¿Cuándo es la cena?")
    """
    try:
        print(f"🔍 Búsqueda semántica: '{query}'")
        
        # 1. Obtener modelo y vectorizar la pregunta
        # Ejecutamos en un thread aparte porque model.encode bloquea el CPU
        import asyncio
        loop = asyncio.get_running_loop()
        model = get_embedding_model()
        
        # Generar vector (embedding)
        query_embedding = await loop.run_in_executor(None, lambda: model.encode([query])[0].tolist())
        
        # 2. Consultar ChromaDB
        resultados = collection_mensajes.query(
            query_embeddings=[query_embedding],
            n_results=limite
        )
        
        # 3. Formatear salida limpia
        respuestas = []
        if resultados['ids'] and resultados['ids'][0]:
            for i in range(len(resultados['ids'][0])):
                respuestas.append({
                    "mensaje_id": resultados['ids'][0][i],
                    "contenido": resultados['documents'][0][i],
                    "metadata": resultados['metadatas'][0][i],
                    "score": resultados['distances'][0][i] if 'distances' in resultados else 0
                })
                
        return {
            "query": query,
            "total_encontrados": len(respuestas),
            "resultados": respuestas
        }

    except Exception as e:
        print(f"❌ Error en búsqueda semántica: {e}")
        # Retornamos lista vacía en vez de error 500 para no romper la app cliente
        return {"query": query, "resultados": [], "error": str(e)}






# --- ENDPOINT PARA RENDER (Llamar desde cron-job.org cada 10 min) ---
@app.get("/api/health")
async def health_check():
    return {"status": "online", "timestamp": datetime.now().isoformat()}

async def tarea_programada_global():
    print("⏰ [CRON-MAIL] Iniciando sincronización automática...")
    try:
        # Buscamos cuentas activas en la BD (tu lógica original)
        res = await asyncio.to_thread(lambda: supabase.table('cuentas_gmail').select('usuario_id', 'email_gmail').eq('activo', True).execute())
        
        if not res.data:
            print("ℹ️ [CRON-MAIL] No hay cuentas activas para sincronizar.")
            return

        for cuenta in res.data:
            print(f"📧 [CRON-MAIL] Procesando: {cuenta['email_gmail']}")
            
            # BLINDAJE: try/except por cada cuenta
            try:
                # Ejecuta la misma lógica que el botón de Flutter
                await ejecutar_logica_sincronizacion(
                    usuario_id=cuenta['usuario_id'],
                    email_gmail=cuenta['email_gmail']
                )
                # Pausa de seguridad obligatoria para no saturar Gemini (Free Tier)
                await asyncio.sleep(10) 
            except Exception as error_cuenta:
                print(f"❌ [CRON-MAIL] Error aislando la cuenta {cuenta['email_gmail']}: {error_cuenta}")
                continue # Pasa a la siguiente cuenta sin romper el bucle general

    except Exception as error_global:
        print(f"❌ [CRON-MAIL] Error crítico en la consulta de BD: {error_global}")








if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)




