# ====================================================
# WHATSAPP IA 17.0 - MOTOR DE INTELIGENCIA CENTRAL
# Arquitectura: Clasificación de Intenciones + Gestión de Alertas
# ====================================================

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, status, Body
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

MODELO_IA = "gemini-1.5-flash" 

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

# --- MODELOS DE DATOS ---
class MensajeEntrada(BaseModel):
    mensaje: str
    modo_profundo: bool = False

class ActualizarAlerta(BaseModel):
    estado: str # 'pendiente', 'completada', 'descartada'

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
    print("🚀 Iniciando Sistema v17 (Motor Inteligente)...")
    try:
        nlp = spacy.load("es_core_news_sm")
    except:
        import spacy.cli
        spacy.cli.download("es_core_news_sm")
        nlp = spacy.load("es_core_news_sm")
    print("✅ NLP Listo")
    yield
    print("👋 Apagando sistema")

app = FastAPI(title="Cerebro WhatsApp IA", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ======================================================================
# 🧠 LÓGICA DE IA (CLASIFICACIÓN Y ANÁLISIS)
# ======================================================================

async def clasificar_intencion(mensaje: str) -> Dict:
    """Clasifica si el usuario quiere CONSULTAR, GUARDAR CONVERSACIÓN o CREAR TAREA."""
    prompt = f"""
    Analiza el mensaje y clasifica la intención en JSON exacto:
    MENSAJE: "{mensaje}"

    TIPOS:
    - CONSULTA: Usuario pregunta sobre datos guardados ("¿qué pendientes tengo?", "¿qué pasó con el cliente X?").
    - CONVERSACION: Usuario comparte un texto/chat para que lo analices y guardes ("Mira lo que me dijo Juan...", "Resumen de esto: ...").
    - TAREA_MANUAL: Usuario ordena crear un recordatorio explícito ("Recuérdame llamar a Ana mañana").

    JSON Schema:
    {{
        "tipo": "CONSULTA" | "CONVERSACION" | "TAREA_MANUAL",
        "subtipo": "busqueda_tema | venta | tarea_academica | reclamo | recordatorio",
        "urgencia": "ALTA" | "MEDIA" | "BAJA",
        "entidades": {{ "persona": "nombre o null", "tema": "tema o null" }}
    }}
    """
    try:
        model = genai.GenerativeModel(MODELO_IA)
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(response.text)
    except:
        # Fallback conservador: Asumir que es una conversación si es largo, consulta si es corto
        return {"tipo": "CONVERSACION" if len(mensaje) > 50 else "CONSULTA", "urgencia": "BAJA", "entidades": {}}

async def procesar_consulta(mensaje: str, modo_profundo: bool, clasificacion: Dict) -> Dict:
    """Responde preguntas SIN guardar en la BD."""
    contexto = ""
    fuente = ""

    if not supabase: return {"respuesta": "Error: Sin conexión a BD"}

    try:
        if modo_profundo:
            # BÚSQUEDA PROFUNDA: Busca en historial de conversaciones
            fuente = "Memoria Histórica (Conversaciones pasadas)"
            query = supabase.table('conversaciones').select('resumen, tipo, created_at').eq('usuario_id', USUARIO_ID_MVP).order('created_at', desc=True).limit(15)
            
            # Filtro básico por persona si existe (Implementación simple)
            persona = clasificacion.get('entidades', {}).get('persona')
            # Nota: Para filtro real por texto se requiere pgvector o filtro 'ilike'
            
            res = query.execute()
            if res.data:
                contexto = "HISTORIAL:\n" + "\n".join([f"- [{c['created_at'][:10]}] ({c['tipo']}) {c['resumen']}" for c in res.data])
            else:
                contexto = "No hay conversaciones guardadas."
        
        else:
            # BÚSQUEDA RÁPIDA: Busca en ALERTAS pendientes
            fuente = "Centro de Mando (Alertas Pendientes)"
            res = supabase.table('alertas').select('*').eq('usuario_id', USUARIO_ID_MVP).eq('estado', 'pendiente').execute()
            
            if res.data:
                contexto = "TAREAS PENDIENTES:\n" + "\n".join([f"- {a['titulo']} ({a['prioridad']}): {a['descripcion']}" for a in res.data])
            else:
                contexto = "No tienes tareas pendientes."

        # Generar respuesta
        prompt = f"""
        Actúa como mi Asistente.
        INTENCIÓN: Responder consulta del usuario.
        FUENTE DE DATOS: {fuente}
        
        [DATOS]
        {contexto}
        
        [PREGUNTA USUARIO]
        {mensaje}
        
        Responde directo y útil. Si es lista, usa viñetas.
        """
        model = genai.GenerativeModel(MODELO_IA)
        resp = model.generate_content(prompt)
        
        return {"respuesta": resp.text, "tipo": "consulta", "modo": "profundo" if modo_profundo else "rapido"}

    except Exception as e:
        return {"respuesta": f"Error al consultar: {str(e)}"}

async def procesar_conversacion(mensaje: str, clasificacion: Dict) -> Dict:
    """Analiza conversación, guarda en BD y genera alertas automáticas."""
    if not supabase: return {"respuesta": "Error BD"}

    prompt = f"""
    Analiza esta conversación/texto.
    CONTEXTO: {clasificacion.get('subtipo', 'general')}
    
    Devuelve JSON con:
    1. Resumen.
    2. 'acciones_pendientes': Lista de tareas que se derivan de esto (si las hay).
    
    JSON Schema:
    {{
        "resumen": "texto",
        "tipo": "venta | reclamo | personal | trabajo",
        "urgencia": "ALTA | MEDIA | BAJA",
        "acciones_pendientes": [
            {{
                "titulo": "Llamar a X",
                "descripcion": "Motivo...",
                "prioridad": "ALTA | MEDIA | BAJA",
                "tipo_alerta": "tarea | reunion | recordatorio"
            }}
        ]
    }}
    
    TEXTO: "{mensaje}"
    """
    try:
        model = genai.GenerativeModel(MODELO_IA)
        resp = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        analisis = json.loads(resp.text)
        
        # 1. Guardar Conversación
        datos_conv = {
            "usuario_id": USUARIO_ID_MVP,
            "resumen": analisis['resumen'],
            "tipo": analisis['tipo'],
            "urgencia": analisis['urgencia'],
            "metadata": analisis,
            "plataforma": "chat_manual"
        }
        res_conv = supabase.table('conversaciones').insert(datos_conv).execute()
        conv_id = res_conv.data[0]['id']
        
        # 2. Generar Alertas Automáticas
        alertas_creadas = 0
        if analisis.get('acciones_pendientes'):
            alertas = []
            for accion in analisis['acciones_pendientes']:
                alertas.append({
                    "usuario_id": USUARIO_ID_MVP,
                    "conversacion_id": conv_id,
                    "titulo": accion['titulo'],
                    "descripcion": accion['descripcion'],
                    "prioridad": accion['prioridad'],
                    "tipo": accion.get('tipo_alerta', 'tarea'),
                    "estado": "pendiente"
                })
            if alertas:
                supabase.table('alertas').insert(alertas).execute()
                alertas_creadas = len(alertas)
        
        msj_extra = f"\n🔔 {alertas_creadas} alertas creadas." if alertas_creadas > 0 else ""
        return {
            "respuesta": f"✅ Guardado. {analisis['resumen']}{msj_extra}",
            "tipo": "analisis_conversacion",
            "alertas_generadas": alertas_creadas
        }

    except Exception as e:
        return {"respuesta": f"Error procesando conversación: {str(e)}"}

async def crear_tarea_manual(mensaje: str, clasificacion: Dict) -> Dict:
    """Crea una alerta directa desde una orden del usuario."""
    prompt = f"""
    Extrae los datos de la tarea del mensaje: "{mensaje}"
    JSON Schema:
    {{
        "titulo": "Corto y claro",
        "descripcion": "Detalles",
        "prioridad": "ALTA | MEDIA | BAJA"
    }}
    """
    try:
        model = genai.GenerativeModel(MODELO_IA)
        resp = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        data = json.loads(resp.text)
        
        alerta = {
            "usuario_id": USUARIO_ID_MVP,
            "titulo": data['titulo'],
            "descripcion": data['descripcion'],
            "prioridad": data['prioridad'],
            "tipo": "manual",
            "estado": "pendiente"
        }
        supabase.table('alertas').insert(alerta).execute()
        return {"respuesta": f"✅ Tarea agendada: {data['titulo']}", "tipo": "tarea_manual"}
    except Exception as e:
        return {"respuesta": f"Error creando tarea: {str(e)}"}

# ======================================================================
# 🚀 ENDPOINTS DE LA API
# ======================================================================

@app.post("/chat", dependencies=[Depends(verificar_llave)])
async def chat_endpoint(entrada: MensajeEntrada):
    """
    Endpoint INTELIGENTE: Clasifica y distribuye la petición.
    """
    # 1. Clasificar Intención
    clasificacion = await clasificar_intencion(entrada.mensaje)
    tipo = clasificacion.get("tipo", "CONSULTA")
    
    print(f"🧠 Intención detectada: {tipo} | Profundo: {entrada.modo_profundo}")

    # 2. Enrutar
    if tipo == "CONSULTA":
        return await procesar_consulta(entrada.mensaje, entrada.modo_profundo, clasificacion)
    
    elif tipo == "CONVERSACION":
        # Si es conversación, se guarda y analiza
        return await procesar_conversacion(entrada.mensaje, clasificacion)
    
    elif tipo == "TAREA_MANUAL":
        return await crear_tarea_manual(entrada.mensaje, clasificacion)
    
    else:
        return {"respuesta": "No entendí tu intención."}

@app.post("/api/analizar", dependencies=[Depends(verificar_llave)])
async def analizar_archivos(files: List[UploadFile] = File(...)):
    """Analiza archivos y genera alertas (Usa la lógica de procesar_conversacion)."""
    texto_total = ""
    for archivo in files:
        content = await archivo.read()
        try:
            texto_total += f"\n--- {archivo.filename} ---\n{content.decode('utf-8')}\n"
        except:
            texto_total += f"\n[Binario {archivo.filename}]\n"
    
    # Reutilizamos la lógica de conversación para guardar y alertar
    clasificacion = {"subtipo": "analisis_archivo", "urgencia": "MEDIA"}
    resultado = await procesar_conversacion(texto_total[:30000], clasificacion)
    
    return {"status": "success", "data": resultado}

# --- NUEVOS ENDPOINTS PARA GESTIÓN DE ALERTAS (FRONTEND) ---

@app.get("/api/alertas", dependencies=[Depends(verificar_llave)])
async def obtener_alertas(estado: str = "pendiente"):
    """Devuelve las alertas para mostrar en la app."""
    if not supabase: return {"alertas": []}
    try:
        query = supabase.table('alertas').select('*').eq('usuario_id', USUARIO_ID_MVP).order('created_at', desc=True)
        if estado != "todas":
            query = query.eq('estado', estado)
        res = query.execute()
        return {"alertas": res.data}
    except Exception as e:
        return {"error": str(e)}

@app.patch("/api/alertas/{alerta_id}", dependencies=[Depends(verificar_llave)])
async def actualizar_alerta(alerta_id: str, body: ActualizarAlerta):
    """Permite marcar alertas como completadas o descartadas."""
    if not supabase: return {"status": "error"}
    try:
        res = supabase.table('alertas').update({'estado': body.estado}).eq('id', alerta_id).execute()
        return {"status": "success", "data": res.data}
    except Exception as e:
        return {"error": str(e)}

@app.post("/webhook")
async def webhook_whatsapp(payload: Dict):
    print("📩 Webhook:", payload)
    return {"status": "recibido"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)
