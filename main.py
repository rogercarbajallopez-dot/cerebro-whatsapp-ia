# ====================================================
# WHATSAPP IA 19.0 - CON AUTENTICACIÓN COMPLETA
# ====================================================

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, status, Body, Request
from fastapi.responses import Response
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
import google.generativeai as genai
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
import jwt  # 👈 REQUERIDO: pip install pyjwt

# 1. CARGA DE SECRETOS
load_dotenv()
API_KEY_GOOGLE = os.getenv('GOOGLE_API_KEY')
APP_PASSWORD = os.getenv('MI_APP_PASSWORD') 
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
SUPABASE_JWT_SECRET = os.getenv('SUPABASE_JWT_SECRET')  # 👈 NUEVO: Clave secreta para verificar tokens

# Configuración IA
if API_KEY_GOOGLE: 
    genai.configure(api_key=API_KEY_GOOGLE)
else:
    print("❌ ALERTA: No se encontró la API KEY de Google.")

MODELO_IA = "gemini-2.5-flash" 

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
bearer_scheme = HTTPBearer(auto_error=False)  # 👈 NUEVO: Esquema para leer el token Bearer

# 🔐 FUNCIÓN PARA VERIFICAR TOKEN JWT Y OBTENER USUARIO
async def obtener_usuario_actual(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)
) -> str:
    """
    Esta función extrae el ID del usuario del token JWT enviado por Flutter.
    Se ejecuta automáticamente en cada endpoint que lo necesite.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se proporcionó token de autenticación"
        )
    
    token = credentials.credentials
    
    try:
        # Decodificar el token JWT usando el secreto de Supabase
        # IMPORTANTE: Supabase usa HS256 y la audiencia 'authenticated'
        payload = jwt.decode(
            token, 
            SUPABASE_JWT_SECRET, 
            algorithms=["HS256"],
            audience="authenticated"
        )
        
        # Extraer el ID del usuario (campo 'sub' en el JWT)
        usuario_id = payload.get("sub")
        
        if not usuario_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido: No contiene ID de usuario"
            )
        
        return usuario_id
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado. Por favor, inicia sesión nuevamente."
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido"
        )
    except Exception as e:
        print(f"Error decodificando token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Error de autenticación"
        )

# Función de verificación legacy (para webhooks públicos o endpoints simples)
async def verificar_llave(api_key: str = Depends(api_key_header)):
    if APP_PASSWORD and api_key != APP_PASSWORD:
        # Si envían token JWT, la API key podría no ser necesaria, 
        # pero mantenemos esto por compatibilidad si se usa x-api-key
        pass 
    return api_key

# --- MODELOS DE DATOS ---
class MensajeEntrada(BaseModel):
    mensaje: str
    modo_profundo: bool = False

class ActualizarAlerta(BaseModel):
    estado: Optional[str] = None 
    etiqueta: Optional[str] = None 

# --- FUNCIONES DE SOPORTE ---
def detectar_mime_real(nombre: str, mime: str) -> str:
    if nombre.endswith('.opus'): return 'audio/ogg'
    return mimetypes.guess_type(nombre)[0] or mime

# --- LIFESPAN (INICIO) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global nlp
    print("🚀 Iniciando Sistema v19.0 (Con Auth Completa)...")
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

app = FastAPI(title="Cerebro WhatsApp IA", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_credentials=True)

# ======================================================================
# 🧠 LÓGICA DE IA (Actualizada para recibir usuario_id)
# ======================================================================

async def clasificar_intencion_portero(mensaje: str) -> Dict:
    """
    EL PORTERO: Decide si el mensaje vale la pena o es basura.
    """
    prompt = f"""
    Analiza el mensaje de WhatsApp y clasifica su VALOR PARA GUARDAR.
    MENSAJE: "{mensaje}"

    TIPOS:
    - BASURA: Saludos ("Hola"), agradecimientos ("Gracias"), preguntas de gestión. -> NO GUARDAR EN BD.
    - VALOR: Información sobre clientes, proyectos, acuerdos, datos nuevos. -> GUARDAR Y ANALIZAR.
    - TAREA: Orden directa de crear recordatorio o tarea ("Recuérdame...", "Agendar..."). -> CREAR ALERTA.

    JSON Schema:
    {{
        "tipo": "BASURA" | "VALOR" | "TAREA",
        "subtipo": "saludo | consulta | venta | reclamo | recordatorio",
        "urgencia": "ALTA | MEDIA | BAJA"
    }}
    """
    try:
        model = genai.GenerativeModel(MODELO_IA)
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(response.text)
    except:
        return {"tipo": "VALOR" if len(mensaje) > 15 else "BASURA"}

async def procesar_informacion_valor(mensaje: str, clasificacion: Dict, usuario_id: str, origen: str = "webhook") -> Dict:
    """
    Esto SOLO se ejecuta si el mensaje es IMPORTANTE (VALOR).
    Guarda el análisis y crea alertas con ETIQUETAS.
    🔑 AHORA USA EL ID DE USUARIO REAL.
    """
    if not supabase: return {"status": "error"}

    prompt = f"""
    Analiza esta información valiosa.
    MENSAJE: "{mensaje}"
    CONTEXTO: {clasificacion.get('subtipo')}
    
    INSTRUCCIONES:
    1. Genera un RESUMEN PROFESIONAL.
    2. Detecta TAREAS y asigna ETIQUETA: [NEGOCIO, ESTUDIO, PAREJA, SALUD, PERSONAL, OTROS].
    
    JSON Schema:
    {{
        "resumen_guardar": "Texto profesional resumido",
        "tipo_evento": "reunion | acuerdo | dato_cliente | reclamo | venta",
        "tareas": [
            {{ 
                "titulo": "...", 
                "prioridad": "ALTA/MEDIA", 
                "descripcion": "Detalles completos (hora, lugar, personas)", 
                "etiqueta": "NEGOCIO" | "ESTUDIO" | "PAREJA" | "SALUD" | "PERSONAL" | "OTROS" 
            }}
        ]
    }}
    """
    try:
        model = genai.GenerativeModel(MODELO_IA)
        resp = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        analisis = json.loads(resp.text)
        
        # 1. GUARDAR CONVERSACIÓN (Asociada al usuario real)
        datos_conv = {
            "usuario_id": usuario_id,  # 👈 ID REAL
            "resumen": analisis['resumen_guardar'], 
            "tipo": analisis['tipo_evento'],
            "urgencia": clasificacion.get('urgencia', 'BAJA'),
            "metadata": {"raw_msg": mensaje if len(mensaje) < 200 else mensaje[:200] + "..."}, 
            "plataforma": origen
        }
        res_conv = supabase.table('conversaciones').insert(datos_conv).execute()
        conv_id = res_conv.data[0]['id']
        
        # 2. CREAR ALERTAS (Asociadas al usuario real)
        alertas_creadas = 0
        if analisis.get('tareas'):
            alertas = []
            for t in analisis['tareas']:
                alertas.append({
                    "usuario_id": usuario_id, # 👈 ID REAL
                    "conversacion_id": conv_id,
                    "titulo": t['titulo'],
                    "descripcion": t.get('descripcion', f"Derivado de: {analisis['resumen_guardar']}"),
                    "prioridad": t['prioridad'],
                    "tipo": "auto_detectada",
                    "estado": "pendiente",
                    "etiqueta": t.get('etiqueta', 'OTROS')
                })
            supabase.table('alertas').insert(alertas).execute()
            alertas_creadas = len(alertas)

        return {
            "status": "guardado", 
            "resumen": analisis['resumen_guardar'], 
            "alertas_generadas": alertas_creadas,
            "respuesta": f"✅ Info guardada: {analisis['resumen_guardar']}"
        }
        
    except Exception as e:
        print(f"Error procesando valor: {e}")
        return {"status": "error", "respuesta": f"Error: {str(e)}"}

async def crear_tarea_directa(mensaje: str, usuario_id: str) -> Dict:
    """
    Crea una alerta directa (Intención TAREA).
    🔑 AHORA USA EL ID DE USUARIO REAL.
    """
    prompt = f"""
    Actúa como asistente personal experto.
    Extrae una tarea estructurada del mensaje del usuario: '{mensaje}'.
    
    INSTRUCCIONES CLAVE:
    1. 'titulo': Corto y directo (Ej: "Cita médica Neoplásicas").
    2. 'descripcion': DEBE contener todos los detalles clave encontrados: HORA exacta, LUGAR, FECHA, NOMBRES y CONTEXTO (Ej: "Cirugía estómago. Hora cita: 8am. Alarma solicitada: 6am").
    3. 'etiqueta': Clasifica en [NEGOCIO, ESTUDIO, PAREJA, SALUD, PERSONAL, OTROS].
    
    JSON Schema: 
    {{
        'titulo': '...', 
        'descripcion': '...',
        'prioridad': 'ALTA' | 'MEDIA' | 'BAJA',
        'etiqueta': '...'
    }}
    """
    try:
        model = genai.GenerativeModel(MODELO_IA)
        resp = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        data = json.loads(resp.text)
        
        # Insertamos con usuario_id real
        supabase.table('alertas').insert({
            "usuario_id": usuario_id, # 👈 ID REAL
            "titulo": data['titulo'],
            "descripcion": data.get('descripcion', mensaje),
            "prioridad": data['prioridad'],
            "tipo": "manual",
            "estado": "pendiente",
            "etiqueta": data.get('etiqueta', 'OTROS')
        }).execute()
        
        return {
            "status": "tarea_creada", 
            "respuesta": f"✅ Tarea: {data['titulo']} \n📂 {data.get('etiqueta','OTROS')} \n📝 {data.get('descripcion','')}"
        }
    except Exception as e:
        print(f"Error tarea directa: {e}")
        return {"status": "error", "respuesta": "No pude procesar la tarea."}

async def procesar_consulta_rapida(mensaje: str, usuario_id: str, modo_profundo: bool) -> str:
    """
    Responde consultas usando contexto de BD.
    🔑 AHORA FILTRA POR ID DE USUARIO.
    """
    if not supabase: return "Error BD"
    
    contexto = ""
    if modo_profundo:
        # Traer historial SOLO DEL USUARIO ACTUAL
        res = supabase.table('conversaciones').select('resumen').eq('usuario_id', usuario_id).order('created_at', desc=True).limit(5).execute()
        if res.data:
            contexto = "HISTORIAL:\n" + "\n".join([f"- {c['resumen']}" for c in res.data])
    else:
        # Traer pendientes SOLO DEL USUARIO ACTUAL
        res = supabase.table('alertas').select('titulo, etiqueta, descripcion').eq('usuario_id', usuario_id).eq('estado', 'pendiente').execute()
        if res.data:
            contexto = "PENDIENTES:\n" + "\n".join([f"- [{a.get('etiqueta','GEN')}] {a['titulo']} ({a.get('descripcion','')})" for a in res.data])
    
    prompt = f"Actúa como asistente. DATOS: {contexto}. PREGUNTA: {mensaje}. Responde breve y útil."
    model = genai.GenerativeModel(MODELO_IA)
    return model.generate_content(prompt).text

# ======================================================================
# 🚀 ENDPOINTS API (PROTEGIDOS CON AUTH)
# ======================================================================

@app.post("/chat")
async def chat_endpoint(
    entrada: MensajeEntrada,
    usuario_id: str = Depends(obtener_usuario_actual) # 👈 SE REQUIERE TOKEN
):
    """
    Endpoint principal para la App.
    Requiere que Flutter envíe el Token en Headers.
    """
    decision = await clasificar_intencion_portero(entrada.mensaje)
    
    if decision['tipo'] == 'TAREA':
        res = await crear_tarea_directa(entrada.mensaje, usuario_id)
        return {"respuesta": res['respuesta']}
    elif decision['tipo'] == 'VALOR': 
         res = await procesar_informacion_valor(entrada.mensaje, decision, usuario_id, "app_manual")
         return {"respuesta": res['respuesta'], "alertas_generadas": res.get('alertas_generadas', 0)}
    else:
        respuesta = await procesar_consulta_rapida(entrada.mensaje, usuario_id, entrada.modo_profundo)
        return {"respuesta": respuesta}

@app.post("/api/analizar")
async def analizar_archivos(
    files: List[UploadFile] = File(...),
    usuario_id: str = Depends(obtener_usuario_actual) # 👈 SE REQUIERE TOKEN
):
    """
    Analizar archivos. 
    Requiere autenticación para saber a quién guardar la data.
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
    usuario_id: str = Depends(obtener_usuario_actual) # 👈 SE REQUIERE TOKEN
):
    """
    Obtener alertas.
    Solo devuelve las alertas que pertenecen al usuario autenticado.
    """
    if not supabase: return {"alertas": []}
    
    q = supabase.table('alertas').select('*').eq('usuario_id', usuario_id).order('created_at', desc=True)
    
    if estado != "todas": 
        q = q.eq('estado', estado)
    
    return {"alertas": q.execute().data}

@app.patch("/api/alertas/{alerta_id}")
async def actualizar_alerta(
    alerta_id: str, 
    body: ActualizarAlerta,
    usuario_id: str = Depends(obtener_usuario_actual) # 👈 SE REQUIERE TOKEN
):
    """
    Actualizar una alerta específica.
    Verifica que la alerta pertenezca al usuario antes de modificarla.
    """
    if not supabase: return {"status": "error"}
    
    # 1. Verificar propiedad de la alerta
    alerta_existente = supabase.table('alertas').select('usuario_id').eq('id', alerta_id).execute()
    
    if not alerta_existente.data:
        raise HTTPException(status_code=404, detail="Alerta no encontrada")
        
    if alerta_existente.data[0]['usuario_id'] != usuario_id:
        raise HTTPException(status_code=403, detail="No tienes permiso para modificar esta alerta")
    
    # 2. Construir datos a actualizar
    datos_actualizar = {}
    if body.estado: datos_actualizar['estado'] = body.estado
    if body.etiqueta: datos_actualizar['etiqueta'] = body.etiqueta
    
    if not datos_actualizar:
        return {"status": "no_change", "msg": "No se enviaron datos para actualizar"}

    res = supabase.table('alertas').update(datos_actualizar).eq('id', alerta_id).execute()
    return {"status": "success", "data": res.data}

# 🔥 WEBHOOK WHATSAPP (SIN AUTH - PÚBLICO)
@app.post("/webhook")
async def webhook_whatsapp(request: Request):
    """
    Este endpoint es PÚBLICO porque Twilio no tiene nuestro Token JWT.
    Aquí se gestiona por número de teléfono (pendiente de implementar búsqueda).
    """
    form_data = await request.form()
    data = dict(form_data)
    mensaje = data.get('Body', '').strip()
    
    # ID Genérico para webhooks (Twilio) ya que no tenemos login ahí todavía
    # En el futuro, buscaríamos el usuario por su número 'From'
    usuario_id_webhook = "00000000-0000-0000-0000-000000000000" 
    
    if not mensaje: 
        return Response(content="<?xml version='1.0'?><Response/>", media_type="application/xml")

    print(f"📩 WhatsApp: {mensaje}")
    decision = await clasificar_intencion_portero(mensaje)
    tipo = decision.get('tipo', 'BASURA')
    
    if tipo == "VALOR":
        await procesar_informacion_valor(mensaje, decision, usuario_id_webhook, "whatsapp_webhook")
    elif tipo == "TAREA":
        await crear_tarea_directa(mensaje, usuario_id_webhook)

    return Response(content="<?xml version='1.0' encoding='UTF-8'?><Response></Response>", media_type="application/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)
