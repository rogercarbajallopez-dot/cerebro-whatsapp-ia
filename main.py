# ====================================================
# WHATSAPP IA 18.6 - CORE: ETIQUETADO PERSISTENTE + DESCRIPCIONES RICAS
# ====================================================

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, status, Body, Request
from fastapi.responses import Response
from fastapi.security import APIKeyHeader
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

# 1. CARGA DE SECRETOS
load_dotenv()
API_KEY_GOOGLE = os.getenv('GOOGLE_API_KEY')
APP_PASSWORD = os.getenv('MI_APP_PASSWORD') 
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

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
header_scheme = APIKeyHeader(name="x-api-key") 

# ID DE USUARIO POR DEFECTO (MVP)
USUARIO_ID_MVP = "00000000-0000-0000-0000-000000000000"

# --- MODELOS DE DATOS (ACTUALIZADOS) ---
class MensajeEntrada(BaseModel):
    mensaje: str
    modo_profundo: bool = False

class ActualizarAlerta(BaseModel):
    # CORRECCIÓN: Ahora aceptamos opcionalmente ambos campos para que el PATCH funcione
    estado: Optional[str] = None 
    etiqueta: Optional[str] = None 

# --- FUNCIONES DE SOPORTE ---
async def verificar_llave(api_key: str = Depends(header_scheme)):
    if APP_PASSWORD and api_key != APP_PASSWORD:
        raise HTTPException(status_code=403, detail="Acceso Denegado")
    return api_key

def detectar_mime_real(nombre: str, mime: str) -> str:
    if nombre.endswith('.opus'): return 'audio/ogg'
    return mimetypes.guess_type(nombre)[0] or mime

# --- LIFESPAN (INICIO) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global nlp
    print("🚀 Iniciando Sistema v18.6 (Core Optimizado)...")
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
# 🧠 LÓGICA DE IA (EL PORTERO Y EL ANALISTA)
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

async def procesar_informacion_valor(mensaje: str, clasificacion: Dict, origen: str = "webhook") -> Dict:
    """
    Esto SOLO se ejecuta si el mensaje es IMPORTANTE (VALOR).
    Guarda el análisis y crea alertas con ETIQUETAS.
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
        
        # 1. GUARDAR CONVERSACIÓN
        datos_conv = {
            "usuario_id": USUARIO_ID_MVP,
            "resumen": analisis['resumen_guardar'], 
            "tipo": analisis['tipo_evento'],
            "urgencia": clasificacion.get('urgencia', 'BAJA'),
            "metadata": {"raw_msg": mensaje if len(mensaje) < 200 else mensaje[:200] + "..."}, 
            "plataforma": origen
        }
        res_conv = supabase.table('conversaciones').insert(datos_conv).execute()
        conv_id = res_conv.data[0]['id']
        
        # 2. CREAR ALERTAS
        alertas_creadas = 0
        if analisis.get('tareas'):
            alertas = []
            for t in analisis['tareas']:
                alertas.append({
                    "usuario_id": USUARIO_ID_MVP,
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

async def crear_tarea_directa(mensaje: str) -> Dict:
    """
    Crea una alerta directa (Intención TAREA).
    CORRECCIÓN: Ahora extrae una descripción rica en detalles.
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
        
        # Insertamos con la descripción rica generada por la IA
        supabase.table('alertas').insert({
            "usuario_id": USUARIO_ID_MVP,
            "titulo": data['titulo'],
            "descripcion": data.get('descripcion', mensaje), # Fallback al mensaje original si falla
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

async def procesar_consulta_rapida(mensaje: str, modo_profundo: bool) -> str:
    """Responde consultas usando contexto de BD."""
    if not supabase: return "Error BD"
    
    contexto = ""
    if modo_profundo:
        res = supabase.table('conversaciones').select('resumen').order('created_at', desc=True).limit(5).execute()
        if res.data:
            contexto = "HISTORIAL:\n" + "\n".join([f"- {c['resumen']}" for c in res.data])
    else:
        # Traemos etiqueta para dar mejores respuestas sobre pendientes
        res = supabase.table('alertas').select('titulo, etiqueta, descripcion').eq('estado', 'pendiente').execute()
        if res.data:
            contexto = "PENDIENTES:\n" + "\n".join([f"- [{a.get('etiqueta','GEN')}] {a['titulo']} ({a.get('descripcion','')})" for a in res.data])
    
    prompt = f"Actúa como asistente. DATOS: {contexto}. PREGUNTA: {mensaje}. Responde breve y útil."
    model = genai.GenerativeModel(MODELO_IA)
    return model.generate_content(prompt).text

# ======================================================================
# 🚀 ENDPOINTS API
# ======================================================================

@app.post("/chat", dependencies=[Depends(verificar_llave)])
async def chat_endpoint(entrada: MensajeEntrada):
    """Endpoint principal para la App."""
    decision = await clasificar_intencion_portero(entrada.mensaje)
    
    if decision['tipo'] == 'TAREA':
        res = await crear_tarea_directa(entrada.mensaje)
        return {"respuesta": res['respuesta']}
    elif decision['tipo'] == 'VALOR': 
         res = await procesar_informacion_valor(entrada.mensaje, decision, "app_manual")
         return {"respuesta": res['respuesta'], "alertas_generadas": res.get('alertas_generadas', 0)}
    else:
        respuesta = await procesar_consulta_rapida(entrada.mensaje, entrada.modo_profundo)
        return {"respuesta": respuesta}

@app.post("/api/analizar", dependencies=[Depends(verificar_llave)])
async def analizar_archivos(files: List[UploadFile] = File(...)):
    texto = ""
    for f in files:
        c = await f.read()
        texto += f"\n{c.decode('utf-8', errors='ignore')}"
    
    res = await procesar_informacion_valor(texto[:30000], {"subtipo": "analisis_archivo", "urgencia": "MEDIA"}, "app_archivo")
    return {"status": "success", "data": res}

@app.get("/api/alertas", dependencies=[Depends(verificar_llave)])
async def obtener_alertas(estado: str = "pendiente"):
    if not supabase: return {"alertas": []}
    q = supabase.table('alertas').select('*').eq('usuario_id', USUARIO_ID_MVP).order('created_at', desc=True)
    if estado != "todas": q = q.eq('estado', estado)
    return {"alertas": q.execute().data}

@app.patch("/api/alertas/{alerta_id}", dependencies=[Depends(verificar_llave)])
async def actualizar_alerta(alerta_id: str, body: ActualizarAlerta):
    """
    CORRECCIÓN: Ahora permite actualizar ESTADO y ETIQUETA dinámicamente.
    """
    if not supabase: return {"status": "error"}
    
    # Construimos dinámicamente qué campos actualizar
    datos_actualizar = {}
    if body.estado: datos_actualizar['estado'] = body.estado
    if body.etiqueta: datos_actualizar['etiqueta'] = body.etiqueta
    
    if not datos_actualizar:
        return {"status": "no_change", "msg": "No se enviaron datos para actualizar"}

    res = supabase.table('alertas').update(datos_actualizar).eq('id', alerta_id).execute()
    return {"status": "success", "data": res.data}

# 🔥 WEBHOOK WHATSAPP 🔥
@app.post("/webhook")
async def webhook_whatsapp(request: Request):
    form_data = await request.form()
    data = dict(form_data)
    mensaje = data.get('Body', '').strip()
    
    if not mensaje: 
        return Response(content="<?xml version='1.0'?><Response/>", media_type="application/xml")

    print(f"📩 WhatsApp: {mensaje}")
    decision = await clasificar_intencion_portero(mensaje)
    tipo = decision.get('tipo', 'BASURA')
    
    if tipo == "VALOR":
        await procesar_informacion_valor(mensaje, decision, "whatsapp_webhook")
    elif tipo == "TAREA":
        await crear_tarea_directa(mensaje)

    return Response(content="<?xml version='1.0' encoding='UTF-8'?><Response></Response>", media_type="application/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)
