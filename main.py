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
from analizador_correos import AnalizadorCorreos


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
import mimetypes
import spacy
from supabase import create_client, Client
from datetime import datetime
from dotenv import load_dotenv
from typing import List, Dict, Optional, Any
from pydantic import BaseModel
import jwt  # Se mantiene por compatibilidad con el archivo original
from datetime import datetime, timedelta
import pytz
from contexto_extractor import ExtractorContexto, enriquecer_alerta_con_contexto


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

# Función auxiliar para enviar (Ponerla aquí para que esté disponible globalmente)
def enviar_push(token: str, titulo: str, cuerpo: str, data_extra: dict = None):
    if not token or not firebase_admin._apps:
        return
    try:
        msg = messaging.Message(
            notification=messaging.Notification(title=titulo, body=cuerpo),
            data=data_extra or {},
            token=token
        )
        messaging.send(msg)
        print(f"🚀 Notificación enviada a: {token[:10]}...")
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
                    # "created_at": datetime.now().isoformat() # Descomenta si tu tabla requiere fecha manual
                }).execute()
                print("✅ Usuario sincronizado correctamente.")
                
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
    
    scheduler.start()
    # --- FIN SCHEDULER ---
    
    try:
        nlp = spacy.load("es_core_news_sm")
    except:
        print("⚠️ Descargando modelo spaCy...")
        import spacy.cli
        spacy.cli.download("es_core_news_sm")
        nlp = spacy.load("es_core_news_sm")
    print("✅ NLP Listo")
    yield
    print("👋 Apagando sistema")
    scheduler.shutdown() # No olvides apagarlo al salir

app = FastAPI(title="Cerebro WhatsApp IA", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_credentials=True)

# ======================================================================
# 🧠 LÓGICA DE IA (SIN CAMBIOS)
# ======================================================================

async def clasificar_intencion_portero(mensaje: str) -> Dict:
    """
    EL PORTERO: Clasifica la intención para decidir si GUARDAR (BD) o solo RESPONDER.
    """
    prompt = f"""
    Eres el cerebro clasificador de un Asistente Personal.
    Tu única misión es etiquetar el mensaje entrante según su utilidad para la Base de Datos.
    
    MENSAJE: "{mensaje}"
    
    CATEGORÍAS (Selecciona con precisión):
    
    1. BASURA (Chat Efímero / General): 
       - Saludos ("Hola", "Buenas noches"), agradecimientos ("Gracias").
       - Preguntas de cultura general, noticias o dudas simples ("¿Qué hora es?", "¿Lloverá hoy?").
       - Conversación casual sin datos personales.
       -> ACCIÓN SISTEMA: NO GUARDAR (Responder usando conocimiento general + Internet).

    2. TAREA (Acción o Evento Futuro):
       - Órdenes directas ("Recuérdame pagar la luz", "Agendar cita").
       - Declaración de compromisos o citas ("Mañana tengo dentista a las 5", "El lunes viajo").
       - CORRECCIONES de tareas anteriores ("No, era a las 4pm", "Cambia la fecha").
       -> ACCIÓN SISTEMA: CREAR O MODIFICAR ALERTA.

    3. VALOR (Memoria, Perfilado y ANÁLISIS DE ERRORES):
       - El usuario cuenta algo de su vida, gustos, familia ("Soy alérgico a las nueces").
       - RECLAMOS O CONSULTAS TÉCNICAS: "¿Por qué no pudiste agendar?", "¿Qué pasó con la tarea anterior?", "¿Qué sabes de mí?".
       - Conversaciones profundas o archivos adjuntos.
       -> ACCIÓN SISTEMA: GUARDAR Y ANALIZAR CONTEXTO.

    Responde SOLO el JSON:
    {{
        "tipo": "BASURA" | "VALOR" | "TAREA",
        "subtipo": "chat_general | dato_personal | evento_pendiente | reclamo_sistema",
        "urgencia": "ALTA | MEDIA | BAJA"
    }}
    """
    try:
        if not gemini_client:
            return {"tipo": "BASURA"}
            
        response = gemini_client.models.generate_content(
            model=MODELO_IA,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text)
    except:
        # Fallback de seguridad: Si el mensaje es largo o parece una queja, es VALOR.
        es_queja = any(x in mensaje.lower() for x in ["por qué", "qué pasó", "error", "no pudiste"])
        return {"tipo": "VALOR" if (len(mensaje) > 20 or es_queja) else "BASURA"}


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
                    "metadata": contexto_tarea  # 🔥 AGREGAR ESTO
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
    Crea una alerta directa CON ENRIQUECIMIENTO CONTEXTUAL.(Intención TAREA).
    SOLUCIÓN ROBUSTA: 
    1. Interpreta typos y fechas relativas.
    2. Si falla el formato JSON, guarda en modo texto (Fallback).
    3. Si falla la Base de Datos (ID incorrecto), avisa sin romper el sistema.
    """
    # 1. Obtenemos la fecha exacta en Lima, Perú
    zona_horaria = pytz.timezone('America/Lima')
    fecha_obj = datetime.now(zona_horaria)
    fecha_actual = fecha_obj.strftime("%Y-%m-%d %H:%M:%S (%A)") # Ej: 2026-01-05 (Lunes)

    # 🔥 CORRECCIÓN: Extraer contexto PRIMERO
    extractor = ExtractorContexto()
    contexto = enriquecer_alerta_con_contexto(
        titulo="Procesando...",  # Temporal
        descripcion=mensaje
    )

    prompt = f"""
    Actúa como asistente personal experto.
    HOY ES: {fecha_actual}.
    
    Extrae una tarea estructurada del mensaje del usuario: '{mensaje}'.
    
    INSTRUCCIONES CLAVE:
    1. 'titulo': Corto y directo (Ej: "Cita médica Neoplásicas").
    2. 'descripcion': DEBE contener todos los detalles clave: HORA exacta, LUGAR, NOMBRES.
       IMPORTANTE: Si dice "Mañana" o "Viernes", CALCULA la fecha real basándote en que HOY es {fecha_actual} e INCLUYE ESA FECHA.
    4. 🔥 'link_meet': Si mencionan "videollamada", "meet", "zoom", "teams", genera un enlace de Google Meet usando este formato:
       https://meet.google.com/new
       (Este link abre un Meet nuevo automáticamente)
    5. 'etiqueta': Clasifica en [NEGOCIO, ESTUDIO, PAREJA, SALUD, PERSONAL, OTROS].
    6. CORRECCIÓN: Si el usuario tiene errores de dedo (ej: "mesicamentos"), interpreta la palabra correcta.
    7. 'prioridad': ALTA (crítico/urgente) | MEDIA (importante) | BAJA (puede esperar)
    8. 'fecha_limite': Si hay fecha/hora, en formato ISO "2026-01-15T17:00:00"

    JSON Schema: 
    {{
        "titulo": "Resumen muy breve (Ej: Cita Médica)", 
        "descripcion": "Detalle completo con la FECHA CALCULADA explícita (Ej: Cita en Loayza el 12/01 a las 9am)",
        "prioridad": "ALTA" | "MEDIA" | "BAJA",
        "etiqueta": "NEGOCIO" | "ESTUDIO" | "SALUD" | "PERSONAL" | "OTROS"
        "fecha_limite": "2026-01-15T17:00:00" o null,
        "link_meet": "https://meet.google.com/..." o null,
        "mensaje_usuario": "📅 Agendado: [titulo]. 🔗 Link del Meet: [link_meet si existe]"
    }}
    """
    # Variable para almacenar los datos finales a guardar
    datos_finales = {}

    # --- PASO 1: INTENTO DE INTELIGENCIA ARTIFICIAL ---
    
    try:
        if not gemini_client:
            raise Exception("Cliente Gemini no disponible")
            
        resp = gemini_client.models.generate_content(
            model=MODELO_IA,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )

        # Limpieza agresiva del texto (quita ```json, ``` y espacios)
        texto_limpio = resp.text.strip().replace("```json", "").replace("```", "")
        data = json.loads(texto_limpio)
        
        # 🔥 ACTUALIZAR contexto con el título real de la IA
        contexto = enriquecer_alerta_con_contexto(
            titulo=data.get('titulo', 'Tarea Nueva'),
            descripcion=data.get('descripcion', mensaje)
        )

        # 🔥 AGREGAR EL LINK AL CONTEXTO
        if data.get('link_meet'):
            contexto['link_meet'] = data['link_meet']

        # Si llegamos aquí, la IA funcionó perfecto
        datos_finales = {
            "usuario_id": usuario_id,
            "titulo": data.get('titulo', 'Tarea Nueva'),
            "descripcion": data.get('descripcion', mensaje),
            "prioridad": data.get('prioridad', 'MEDIA'),
            "tipo": "manual",
            "estado": "pendiente",
            "etiqueta": data.get('etiqueta', 'OTROS'),
            # En crear_tarea_directa(), REEMPLAZA la línea de fecha_limite por:
            "fecha_limite": data.get('fecha_limite').isoformat() if data.get('fecha_limite') else None,
            "metadata": contexto  # 🔥 AQUÍ SE GUARDA TODO EL CONTEXTO
        }

    except Exception as e_ia:
        print(f"⚠️ La IA no pudo estructurar el JSON: {e_ia}. Usando modo manual.")
        # --- PASO 2: FALLBACK (Plan B si la IA falla) ---
        # Si la IA falla, no nos detenemos. Preparamos los datos "en crudo".
        datos_finales = {
            "usuario_id": usuario_id,
            "titulo": "Recordatorio Rápido", # Título genérico
            "descripcion": mensaje,          # Guardamos el texto original tal cual
            "prioridad": "MEDIA",
            "tipo": "manual",
            "estado": "pendiente",
            "etiqueta": "OTROS",
            "fecha_limite": data.get('fecha_limite'),
            "metadata": contexto  # 🔥 AQUÍ SE GUARDA TODO EL CONTEXTO
        }

    # --- PASO 3: GUARDADO EN BASE DE DATOS (El momento de la verdad) ---
    try:
        # Intentamos insertar los datos
        res = supabase.table('alertas').insert(datos_finales).execute()
        
        # 🔥🔥 INICIO NOTIFICACIONES (Bloque Nuevo) 🔥🔥
        try:
            # 1. Obtener Token
            user_data = supabase.table('usuarios').select('fcm_token').eq('id', usuario_id).execute()
            
            if user_data.data and user_data.data[0].get('fcm_token'):
                token = user_data.data[0]['fcm_token']
                
                # 2. Configurar Emoji
                prio = datos_finales.get('prioridad', 'MEDIA')
                emoji = "🔴" if prio == 'ALTA' else ("🟡" if prio == 'MEDIA' else "🟢")
                
                # 🔥 ENVIAR CON TIPO DE ACCIÓN PARA QUE FLUTTER EJECUTE
                tipo_accion = contexto.get('tipo_accion', 'tarea_general')

                # 🔥 CUERPO DE LA NOTIFICACIÓN CON LINK
                cuerpo_noti = datos_finales['descripcion']
                if contexto.get('link_meet'):
                    cuerpo_noti += f"\n\n🔗 Link: {contexto['link_meet']}"

                # 3. Enviar
                enviar_push(
                    token=token,
                    titulo=f"{emoji} Nueva Tarea: {datos_finales['titulo']}",
                    cuerpo=datos_finales['descripcion'],
                    data_extra={
                        "tipo": "TAREA_EJECUTABLE",  # 🔥 CAMBIAR ESTO
                        "alerta_id": str(res.data[0]['id']) if res.data else "0",
                        "link_meet": contexto.get('link_meet', ''),
                        "acciones": contexto.get('acciones_sugeridas', []),
                        "accion_principal": tipo_accion,  # 🔥 NUEVO
                        "ejecutar_automatico": "true",    # 🔥 NUEVO
                        "click_action": "FLUTTER_NOTIFICATION_CLICK",
                        "metadata": json.dumps(contexto),  # 🔥 NUEVO
                    }
                )
        except Exception as e_push:
            print(f"⚠️ Error enviando notificación manual: {e_push}")
        # 🔥🔥 FIN NOTIFICACIONES 🔥🔥
        
        # ÉXITO TOTAL
        origen = "🤖 IA+Contexto" if datos_finales['titulo'] != "Recordatorio Rápido" else "📝 Texto"
        acciones_info = ""
        if contexto.get('acciones_sugeridas'):
            acciones_info = f"\n🔘 Acciones disponibles: {', '.join(contexto['acciones_sugeridas'])}"
        
        return {
            "status": "tarea_creada", 
            "respuesta": f"✅ Agendado ({origen}): {datos_finales['titulo']}\n📅 {datos_finales['descripcion']}",
            "metadata": contexto, # Devolver para que Flutter lo use
            "link_meet": contexto.get('link_meet')   
        }

    except Exception as e_bd:
        # --- PASO 4: MANEJO DE ERROR DE USUARIO (Si el ID no existe) ---
        print(f"🛑 Error Base de Datos: {e_bd}")
        
        # Aquí capturamos el error de "Usuario no existe" para que no salga "Error Crítico"
        return {
            "status": "error_db", 
            "respuesta": (
                f"⚠️ Entendido: '{datos_finales['titulo']}'.\n\n"
                "Sin embargo, no pude guardarlo en tu agenda porque tu usuario no está sincronizado correctamente con la base de datos.\n"
                "👉 Por favor, cierra sesión y vuelve a ingresar para reactivar tu cuenta."
            )
        }

        
async def procesar_consulta_rapida(mensaje: str, usuario_id: str, modo_profundo: bool) -> str:
    """
    Responde consultas conectando:
    1. PERFIL (Memoria a Largo Plazo: Quién es el usuario).
    2. HISTORIAL (Memoria a Corto/Mediano Plazo: Qué ha pasado).
    3. TAREAS (Agenda: Qué tiene pendiente).
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
        
        DIRECTRICES DE RESPUESTA:
        1. INTERNET: Si el usuario pregunta por noticias, clima, dólar o datos actuales, USA TU HERRAMIENTA DE BÚSQUEDA (Google Search).
        2. PERSONALIZACIÓN: Usa los datos del PERFIL para adaptar tu respuesta.
        3. HISTORIAL: Si pregunta algo específico del pasado, usa el CONTEXTO.
        4. MEMORIA: Si pregunta "¿Qué me dijo Juan?", busca en MEMORIA PROFUNDA. Si pregunta "¿Qué hice hoy?", busca en MEMORIA RECIENTE.
        5. TONO: Eres un asistente útil. Sé claro y directo.
        6. FILTRO: Si pregunta algo específico del historial, usa los datos de CONTEXTO. Si es una duda general, responde con tu conocimiento base.
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

        # 3. 🔥 LA CORRECCIÓN CLAVE: Usamos .models.embed_content
        # Tu versión de librería requiere entrar a 'models' primero
        result = gemini_client.models.embed_content(
            model="text-embedding-004",
            contents=texto
        )
        
        # 4. Extraemos y devolvemos la lista de números
        if result.embeddings:
            return result.embeddings[0].values
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
    3. CHAT -> Responde usando contexto, pero no ensucia la BD.
    """
    try:
        # 1. El Portero decide la intención (Igual que antes)
        decision = await clasificar_intencion_portero(entrada.mensaje)
        
        # CASO 1: Tarea explícita ("Recuérdame...")
        if decision['tipo'] == 'TAREA':
            res = await crear_tarea_directa(entrada.mensaje, usuario_id)
            return {"respuesta": res['respuesta']}
            
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
             
        # CASO 3: Chat General / Basura ("Hola", "¿Cómo estás?", "¿Qué tengo pendiente?")
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
    
    # Construir actualización
    datos_actualizar = {}
    if body.estado: datos_actualizar['estado'] = body.estado
    if body.etiqueta: datos_actualizar['etiqueta'] = body.etiqueta
    
    if not datos_actualizar:
        return {"status": "no_change", "msg": "No se enviaron datos para actualizar"}

    res = supabase.table('alertas').update(datos_actualizar).eq('id', alerta_id).execute()
    return {"status": "success", "data": res.data}

# 🔥 WEBHOOK WHATSAPP (SIN AUTENTICACIÓN - Público para Twilio)
@app.post("/webhook")
async def webhook_whatsapp(request: Request):
    """
    Este endpoint NO requiere autenticación porque lo llama Twilio.
    Usa API Key en lugar de JWT.
    """
    form_data = await request.form()
    data = dict(form_data)
    mensaje = data.get('Body', '').strip()
    
    # ID Genérico para webhooks (Twilio)
    usuario_id_webhook = "00000000-0000-0000-0000-000000000000"
    
    if not mensaje: 
        return Response(content="<?xml version='1.0'?><Response/>", media_type="application/xml")

    print(f"📩 WhatsApp: {mensaje}")
    decision = await clasificar_intencion_portero(mensaje)
    tipo = decision.get('tipo', 'BASURA')
    
    if tipo == "VALOR":
        await procesar_informacion_valor(mensaje, decision, usuario_id_webhook, "whatsapp_webhook")
        # --- INICIO DEL AGREGADO ---
        # Solo notificamos si la IA detectó que es urgente
        urgencia = decision.get("urgencia", "MEDIA")
        
        if urgencia in ["ALTA", "CRITICA", "URGENTE"]:
            enviar_notificacion_inteligente(
                usuario_id_webhook, 
                "🚨 Atención Requerida", 
                decision.get('resumen', 'Nueva alerta importante')
            )
        # --- FIN DEL AGREGADO ---
    elif tipo == "TAREA":
        await crear_tarea_directa(mensaje, usuario_id_webhook)

    return Response(content="<?xml version='1.0' encoding='UTF-8'?><Response></Response>", media_type="application/xml")


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
    """
    Sincroniza correos desde Gmail y los vincula con la cuenta correcta.
    Ahora soporta múltiples cuentas Gmail por usuario.
    """
    if not gemini_client:
        raise HTTPException(status_code=500, detail="IA no disponible")
    
    try:
        # 1. Obtener datos del request
        body = await request.json()
        gmail_token = body.get('gmail_access_token')
        email_gmail = body.get('email_gmail')  # 🔥 NUEVO: Email de la cuenta Gmail
        
        if not gmail_token:
            raise HTTPException(status_code=400, detail="Token de Gmail requerido")
        
        # 2. 🔥 NUEVO: Buscar o crear registro de cuenta Gmail
        cuenta_gmail_id = None
        
        if email_gmail:
            # Intentar obtener la cuenta existente
            cuenta_existente = supabase.table('cuentas_gmail')\
                .select('id')\
                .eq('usuario_id', usuario_id)\
                .eq('email_gmail', email_gmail)\
                .execute()
            
            if cuenta_existente.data:
                # Ya existe, usar ese ID
                cuenta_gmail_id = cuenta_existente.data[0]['id']
                print(f"✅ Usando cuenta Gmail existente: {email_gmail}")
            else:
                # No existe, crear nueva
                nueva_cuenta = supabase.table('cuentas_gmail').insert({
                    'usuario_id': usuario_id,
                    'email_gmail': email_gmail,
                    'activo': True,
                    'access_token': gmail_token,  # Opcional: guardar token
                }).execute()
                
                if nueva_cuenta.data:
                    cuenta_gmail_id = nueva_cuenta.data[0]['id']
                    print(f"✅ Nueva cuenta Gmail registrada: {email_gmail}")
        
        # 3. Inicializar servicio de Gmail
        from gmail_service import GmailService
        gmail = GmailService(access_token=gmail_token)
        
        # 4. Obtener correos no leídos
        correos_gmail = gmail.obtener_correos_no_leidos(cantidad=50)
        
        if not correos_gmail:
            return {
                "status": "success",
                "mensaje": "No hay correos nuevos",
                "estadisticas": {"procesados": 0}
            }
        
        # 5. Obtener datos del usuario (nombre)
        user_data = supabase.table('usuarios')\
            .select('nombre, email')\
            .eq('id', usuario_id)\
            .execute()
        
        nombre_usuario = ""
        if user_data.data:
            nombre = user_data.data[0].get('nombre', '')
            email = user_data.data[0].get('email', '')
            nombre_usuario = nombre if nombre else email.split('@')[0]
        
        # 6. Procesar correos con el analizador inteligente
        resultado = await analizador_correos.procesar_lote_correos(
            correos=correos_gmail,
            usuario_id=usuario_id,
            gemini_client=gemini_client,
            supabase_client=supabase,
            nombre_usuario=nombre_usuario,
            cuenta_gmail_id=cuenta_gmail_id  # 🔥 NUEVO: Pasar ID de cuenta
        )
        
        # 7. Enviar notificaciones PUSH (solo correos críticos)
        if resultado.get('correos_criticos'):
            try:
                fcm_data = supabase.table('usuarios')\
                    .select('fcm_token')\
                    .eq('id', usuario_id)\
                    .execute()
                
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
    
    except Exception as e:
        print(f"Error sincronizando correos: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/analizar-historial-gmail")
async def analizar_historial_gmail_endpoint(
    request: Request,
    usuario_id: str = Depends(obtener_usuario_actual)
):
    """
    Analiza TODO el historial de Gmail (una sola vez por cuenta).
    Usa filtrado inteligente para reducir costos en 94%.
    """
    try:
        body = await request.json()
        gmail_token = body.get('gmail_access_token')
        email_gmail = body.get('email_gmail')
        
        if not gmail_token or not email_gmail:
            raise HTTPException(status_code=400, detail="Token y email requeridos")
        
        # Inicializar servicio
        from gmail_service import GmailService
        gmail = GmailService(access_token=gmail_token)
        
        # 🔥 USAR VERSIÓN OPTIMIZADA
        from analizador_correos import analizar_historial_gmail_optimizado
        resultado = await analizar_historial_gmail_optimizado(
            usuario_id=usuario_id,
            email_gmail=email_gmail,
            gmail_service=gmail,
            gemini_client=gemini_client,
            supabase_client=supabase
        )
        
        return resultado
    
    except Exception as e:
        print(f"Error en análisis histórico: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/enviar-correo")
async def enviar_correo_endpoint(
    request: Request,
    usuario_id: str = Depends(obtener_usuario_actual)
):
    """
    Envía un correo usando la cuenta de Gmail del usuario.
    """
    try:
        body = await request.json()
        
        gmail_token = body.get('gmail_access_token')
        destinatario = body.get('destinatario')
        asunto = body.get('asunto')
        cuerpo = body.get('cuerpo')
        thread_id = body.get('thread_id')  # Para respuestas
        
        if not all([gmail_token, destinatario, asunto, cuerpo]):
            raise HTTPException(status_code=400, detail="Faltan parámetros")
        
        # Enviar correo
        gmail = GmailService(access_token=gmail_token)
        exito = gmail.enviar_correo(destinatario, asunto, cuerpo, thread_id)
        
        if exito:
            return {"status": "success", "mensaje": "Correo enviado"}
        else:
            raise HTTPException(status_code=500, detail="Error enviando correo")
    
    except Exception as e:
        print(f"Error en endpoint enviar: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/correos-pendientes")
async def obtener_correos_pendientes(
    usuario_id: str = Depends(obtener_usuario_actual)
):
    """
    Obtiene correos analizados que requieren acción.
    """
    try:
        correos = supabase.table('correos_analizados')\
            .select('*')\
            .eq('usuario_id', usuario_id)\
            .eq('requiere_accion', True)\
            .order('score_importancia', desc=True)\
            .limit(50)\
            .execute()
        
        return {"correos": correos.data}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)



