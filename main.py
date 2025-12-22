# ====================================================
# WHATSAPP IA 15.0 - CEREBRO EN LA NUBE (SUPABASE)
# Base: v14.0 + Persistencia Real + Eliminación de SQLite
# ====================================================

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
import google.generativeai as genai
from contextlib import asynccontextmanager
import os
import json
import mimetypes
import spacy
# import sqlite3  <-- ELIMINADO: Ya no usamos base de datos local
from supabase import create_client, Client # <-- NUEVO: Cliente de Supabase
import numpy as np 
from datetime import datetime
from dotenv import load_dotenv
from typing import List, Dict
from pydantic import BaseModel

# --- LIBRERÍAS LIGERAS (Carga rápida) ---
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.naive_bayes import MultinomialNB

# 1. CARGA DE SECRETOS (SEGURIDAD)
load_dotenv() # Lee el archivo .env

API_KEY_GOOGLE = os.getenv('GOOGLE_API_KEY')
APP_PASSWORD = os.getenv('MI_APP_PASSWORD') 

# --- NUEVAS VARIABLES PARA SUPABASE ---
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# ✅ CONFIGURACIÓN GEMINI
if API_KEY_GOOGLE:
    genai.configure(api_key=API_KEY_GOOGLE)
else:
    print("❌ ALERTA: No se encontró la API KEY de Google.")

# ✅ CONEXIÓN A SUPABASE (El Cerebro Eterno)
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase: CONECTADO")
    except Exception as e:
        print(f"❌ Error conectando a Supabase: {e}")
        supabase = None
else:
    print("⚠️ ALERTA: Faltan credenciales de SUPABASE en el archivo .env")
    supabase = None

# Usamos 1.5-flash (Versión estable actual más rápida)
MODELO_IA = "gemini-1.5-flash" 

# Variables Globales
nlp = None
clf_urgencia = None
vectorizer = None

# --- SEGURIDAD: EL GUARDIA DE LA PUERTA ---
header_scheme = APIKeyHeader(name="x-api-key") 

async def verificar_llave(api_key: str = Depends(header_scheme)):
    """Si la llave no coincide con el .env, bloquea la entrada."""
    if not APP_PASSWORD:
        return api_key
        
    if api_key != APP_PASSWORD:
        print(f"⛔ ALERTA: Acceso denegado. Clave usada incorrecta.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso Denegado: Contraseña incorrecta."
        )
    return api_key

# --- CARGA AL INICIO ---
print("🚀 Iniciando Sistema v15 (Nube + IA)...")

# A. spaCy
try:
    nlp = spacy.load("es_core_news_sm")
    print("✅ NLP Local: LISTO")
except:
    print("⚠️ NLP: Modelo no encontrado, intentando descargar...")
    import spacy.cli
    spacy.cli.download("es_core_news_sm")
    try:
        nlp = spacy.load("es_core_news_sm")
        print("✅ NLP Local: Descargado y LISTO")
    except:
        print("❌ ERROR CRÍTICO: No se pudo cargar Spacy.")

# B. Detector de Urgencia (ML Ligero)
try:
    print("⚙️ Calibrando seguridad...")
    datos_entrenamiento = [
        ("urgente", "ALTA"), ("ayuda", "ALTA"), ("emergencia", "ALTA"),
        ("error critico", "ALTA"), ("para ya", "ALTA"), ("plazo vence", "ALTA"),
        ("hola", "BAJA"), ("buenos dias", "BAJA"), ("informe", "BAJA"),
        ("gracias", "BAJA"), ("reunion", "BAJA"), ("todo bien", "BAJA")
    ]
    vectorizer = CountVectorizer()
    X_train = vectorizer.fit_transform([t[0] for t in datos_entrenamiento])
    y_train = [t[1] for t in datos_entrenamiento]
    clf_urgencia = MultinomialNB()
    clf_urgencia.fit(X_train, y_train)
    print("✅ Detector Urgencia: LISTO")
except Exception as e:
    print(f"⚠️ ML Urgencia omitido: {e}")


# --- BASE DE DATOS (REEMPLAZADA POR SUPABASE) ---
# La función init_db ya no es necesaria porque la tabla está en la nube.

def guardar_en_db(resumen, personas, lugares, json_data, urgencia, generar_vector):
    """Guarda análisis en Supabase (Persistencia Real)"""
    if not supabase:
        print("⚠️ No hay conexión a Supabase. Datos no guardados.")
        return

    try:
        # ID genérico para MVP. En el futuro usaremos el número de teléfono real.
        usuario_id = "00000000-0000-0000-0000-000000000000"
        
        # Preparar datos para insertar
        datos_conversacion = {
            'usuario_id': usuario_id,
            'resumen': resumen,
            'urgencia': urgencia,
            'metadata': json_data, # Guardamos todo el JSON del análisis
            'participantes': {'personas': personas, 'lugares': lugares},
            'plataforma': 'app_manual'
        }
        
        # Insertar en Supabase
        resultado = supabase.table('conversaciones').insert(datos_conversacion).execute()
        print(f"✅ Guardado en Supabase exitosamente.")
            
    except Exception as e:
        print(f"❌ Error guardando en Supabase: {e}")

# --- UTILIDADES ---
def predecir_urgencia_ml(texto: str) -> str:
    if not clf_urgencia or not texto or len(texto) < 3: return "BAJA"
    try: return clf_urgencia.predict(vectorizer.transform([texto.lower()]))[0]
    except: return "BAJA"

def detectar_mime_real(nombre: str, mime: str) -> str:
    nombre = nombre.lower()
    if nombre.endswith('.opus'): return 'audio/ogg'
    if nombre.endswith('.mp3'): return 'audio/mp3'
    if nombre.endswith('.txt'): return 'text/plain'
    return mimetypes.guess_type(nombre)[0] or mime

def analizar_texto_con_spacy(texto: str) -> Dict:
    if not nlp: return {"personas": [], "lugares": []}
    doc = nlp(texto)
    datos = {"personas": [], "lugares": []}
    for ent in doc.ents:
        if ent.label_ == "PER" and ent.text not in datos["personas"]:
            datos["personas"].append(ent.text)
        elif ent.label_ == "LOC" and ent.text not in datos["lugares"]:
            datos["lugares"].append(ent.text)
    return datos

# --- BUSQUEDA INTELIGENTE (AHORA EN SUPABASE) ---
def busqueda_profunda_inteligente(pregunta: str, usuario_id: str = None):
    """Búsqueda en memoria histórica usando Supabase"""
    if not supabase: return "Sin conexión a memoria."

    try:
        if not usuario_id:
            usuario_id = "00000000-0000-0000-0000-000000000000"
        
        # Búsqueda de los últimos 3 análisis relevantes (Por ahora cronológico)
        # En el futuro activaremos pgvector para búsqueda semántica real.
        resultado = supabase.table('conversaciones')\
            .select('resumen, urgencia')\
            .eq('usuario_id', usuario_id)\
            .order('created_at', desc=True)\
            .limit(3)\
            .execute()
        
        if not resultado.data:
            return "Sin memoria previa."
        
        relevantes = [f"{'🚨' if r['urgencia']=='ALTA' else '📄'} {r['resumen']}" 
                      for r in resultado.data]
        return "\n".join(relevantes)
            
    except Exception as e:
        print(f"Error en búsqueda: {e}")
        return "Error consultando memoria."

# --- API ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("\n🔐 SERVIDOR V15 (SUPABASE) - LISTO")
    yield

app = FastAPI(title="WhatsApp IA Secure", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class MensajeEntrada(BaseModel):
    mensaje: str
    modo_profundo: bool = False

# 🔒 ENDPOINT 1: CHAT BLINDADO
@app.post("/chat", dependencies=[Depends(verificar_llave)])
async def chat_endpoint(entrada: MensajeEntrada):
    urgencia = predecir_urgencia_ml(entrada.mensaje)
    prefijo = "🚨 [URGENTE] " if urgencia == "ALTA" else ""
    
    contexto = ""
    # Si activamos modo profundo o modo normal, siempre intentamos traer algo de memoria
    try:
        # Traer contexto de Supabase (Últimos 2 eventos)
        if supabase:
            resultado = supabase.table('conversaciones')\
                .select('resumen')\
                .order('created_at', desc=True)\
                .limit(2)\
                .execute()
            
            if resultado.data:
                contexto = "\n".join([f"- {r['resumen']}" for r in resultado.data])
    except Exception as e:
        print(f"Error leyendo contexto: {e}")

    prompt = f"""
    [SISTEMA] Urgencia detectada: {urgencia}
    [MEMORIA RECIENTE] 
    {contexto}
    
    [USUARIO DICE] 
    {entrada.mensaje}
    
    Responde de forma útil, profesional y directa.
    """
    
    try:
        model = genai.GenerativeModel(MODELO_IA)
        response = model.generate_content(prompt)
        return {"respuesta": f"{prefijo}{response.text}", "modo": "Nube/Supabase"}
    except Exception as e:
        return {"respuesta": f"Error IA: {str(e)}"}

# 🔒 ENDPOINT 2: ANÁLISIS DE ARCHIVOS BLINDADO
@app.post("/api/analizar", dependencies=[Depends(verificar_llave)])
async def analizar_archivos_completo(files: List[UploadFile] = File(...), indexar_para_busqueda: bool = True):
    texto_acumulado = ""
    partes_para_gemini = []
    
    try:
        # Lectura de archivos
        for archivo in files:
            contenido = await archivo.read()
            mime = detectar_mime_real(archivo.filename, archivo.content_type)
            
            if "text" in mime or "json" in mime:
                txt = contenido.decode('utf-8', errors='ignore')
                texto_acumulado += txt + "\n"
                partes_para_gemini.append(f"\n--- DOC ({archivo.filename}) ---\n{txt}\n")
            else:
                blob = {"mime_type": mime, "data": contenido}
                partes_para_gemini.append(blob)

        # Motores locales
        datos_spacy = analizar_texto_con_spacy(texto_acumulado[:50000])
        urgencia_global = predecir_urgencia_ml(texto_acumulado[:1000])

        prompt_sistema = f"""
        Actúa como Analista de Negocios y Asistente Personal.
        Entidades detectadas: {', '.join(datos_spacy['personas'])}
        Urgencia pre-calculada: {urgencia_global}
        
        Analiza el contenido y devuelve un JSON con esta estructura exacta:
        {{
            "ASISTENTE_ACTIVO": {{
                "resumen_rapido": "Un resumen breve de lo que trata el archivo.",
                "alertas_urgentes": ["Lista de cosas que requieren acción inmediata"],
                "nivel_riesgo": "{urgencia_global}",
                "tipo": "venta/reclamo/personal/otro"
            }}
        }}
        """
        partes_para_gemini.insert(0, prompt_sistema)
        
        model = genai.GenerativeModel(MODELO_IA)
        configuracion = genai.GenerationConfig(response_mime_type="application/json")
        
        response = model.generate_content(
            partes_para_gemini, 
            generation_config=configuracion
        )
        
        texto_limpio = response.text.replace("```json", "").replace("```", "")
        resultado = json.loads(texto_limpio)

        # GUARDAR EN SUPABASE
        guardar_en_db(
            resultado['ASISTENTE_ACTIVO']['resumen_rapido'], 
            datos_spacy['personas'], 
            datos_spacy['lugares'], 
            resultado, 
            urgencia_global, 
            indexar_para_busqueda
        )
        return {"status": "success", "data": resultado, "urgencia": urgencia_global}

    except Exception as e:
        print(f"❌ Error: {e}")
        return {"status": "error", "data": {"error": str(e)}}

# 🔒 ENDPOINT 3: WEBHOOK (PREPARADO PARA WHATSAPP AUTOMÁTICO)
@app.post("/webhook")
async def webhook_whatsapp(payload: Dict):
    """
    Aquí recibiremos los mensajes de Evolution API en el futuro.
    Por ahora solo confirma que recibió la señal.
    """
    print("📩 Webhook recibido:", payload)
    return {"status": "recibido"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)
