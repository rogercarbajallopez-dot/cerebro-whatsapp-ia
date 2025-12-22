# ====================================================
# WHATSAPP IA 14.0 - SEGURIDAD TOTAL (.ENV + API KEY)
# Base: v13.5 (Velocidad + Archivos) + Blindaje
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
import sqlite3
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

API_KEY_GOOGLE = os.getenv('GEMINI_API_KEY')
APP_PASSWORD = os.getenv('MI_APP_PASSWORD') # Tu contraseña maestra

# ✅ CORRECCIÓN CRÍTICA: Cambiado a 1.5-flash para evitar tu error de cuota
MODELO_IA = "gemini-2.5-flash" 
DB_NAME = "cerebro_whatsapp.db"

# Variables Globales
nlp = None
client = None
embedder = None   # Lazy Loading (Se mantiene para velocidad)
clf_urgencia = None
vectorizer = None

# --- SEGURIDAD: EL GUARDIA DE LA PUERTA ---
header_scheme = APIKeyHeader(name="x-api-key") 

async def verificar_llave(api_key: str = Depends(header_scheme)):
    """Si la llave no coincide con el .env, bloquea la entrada."""
    if api_key != APP_PASSWORD:
        print(f"⛔ ALERTA: Acceso denegado. Clave usada incorrecta.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso Denegado: Contraseña incorrecta."
        )
    return api_key

# --- FUNCIÓN DE CARGA DIFERIDA (VELOCIDAD) ---
def obtener_motor_vectorial():
    """Solo carga la IA pesada si realmente se necesita."""
    global embedder
    if embedder is None:
        print("🐢 Cargando motor de memoria profunda (SentenceTransformer)...")
        from sentence_transformers import SentenceTransformer
        embedder = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
    return embedder

# --- CARGA AL INICIO ---
print("🚀 Iniciando Sistema v14 (Blindado y Veloz)...")

# A. spaCy (Necesario para analizar archivos)
try:
    nlp = spacy.load("es_core_news_sm")
    print("✅ NLP Local: LISTO")
except:
    print("⚠️ NLP: Modelo no encontrado (funcionará básico).")

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

# C. Cliente Gemini
if API_KEY_GOOGLE:
    try:
        client = genai.Client(api_key=API_KEY_GOOGLE)
        print(f"✅ Motor IA ({MODELO_IA}): CONECTADO")
    except:
        print("❌ Error API Key Gemini")

# --- BASE DE DATOS ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS analisis (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, resumen TEXT, 
        personas TEXT, lugares TEXT, json_completo TEXT, embedding TEXT, urgencia TEXT)''')
    try: c.execute("ALTER TABLE analisis ADD COLUMN embedding TEXT"); 
    except: pass
    try: c.execute("ALTER TABLE analisis ADD COLUMN urgencia TEXT"); 
    except: pass
    conn.commit()
    conn.close()

def guardar_en_db(resumen, personas, lugares, json_data, urgencia, generar_vector):
    vector_json = "[]"
    if generar_vector:
        motor = obtener_motor_vectorial() # Lazy Load
        if motor:
            vector = motor.encode(resumen)
            vector_json = json.dumps(vector.tolist())

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''INSERT INTO analisis (fecha, resumen, personas, lugares, json_completo, embedding, urgencia)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''', 
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), resumen, 
               json.dumps(personas), json.dumps(lugares), json.dumps(json_data), 
               vector_json, urgencia))
    conn.commit()
    conn.close()

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

# --- BUSQUEDA ---
def busqueda_profunda_inteligente(pregunta: str):
    motor = obtener_motor_vectorial() # Lazy Load
    if not motor: return "Motor no disponible."
    
    from sklearn.metrics.pairwise import cosine_similarity
    vector_pregunta = motor.encode(pregunta).reshape(1, -1)
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT resumen, embedding, urgencia FROM analisis WHERE length(embedding) > 5')
    filas = c.fetchall()
    conn.close()
    
    if not filas: return "Sin memoria profunda."
    
    scores = []
    for r, emb, u in filas:
        try:
            emb_db = np.array(json.loads(emb)).reshape(1, -1)
            sim = cosine_similarity(vector_pregunta, emb_db)[0][0]
            scores.append((sim, r, u))
        except: pass
        
    scores.sort(key=lambda x: x[0], reverse=True)
    relevantes = [f"{'🚨' if u=='ALTA' else '📄'} {r}" for s, r, u in scores if s > 0.35]
    return "\n".join(relevantes[:3]) if relevantes else "Sin coincidencias relevantes."

# --- API (CON SEGURIDAD) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print("\n🔐 SERVIDOR V14 SEGURO - ESPERANDO CONEXIONES AUTORIZADAS")
    yield

# Docs ocultos para seguridad
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
    if entrada.modo_profundo:
        contexto = busqueda_profunda_inteligente(entrada.mensaje)
    else:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('SELECT resumen FROM analisis ORDER BY id DESC LIMIT 2')
        filas = c.fetchall()
        conn.close()
        if filas: contexto = "\n".join([f"- {f[0]}" for f in filas])

    prompt = f"""
    [SISTEMA] Urgencia: {urgencia}
    [MEMORIA] {contexto}
    [USUARIO] {entrada.mensaje}
    Responde directo, breve y útil.
    """
    
    try:
        response = client.models.generate_content(model=MODELO_IA, contents=prompt)
        return {"respuesta": f"{prefijo}{response.text}", "modo": "Profundo" if entrada.modo_profundo else "Rápido"}
    except Exception as e:
        return {"respuesta": f"Error: {str(e)}"}

# 🔒 ENDPOINT 2: ANÁLISIS DE ARCHIVOS BLINDADO (Tu código original + Seguridad)
@app.post("/api/analizar", dependencies=[Depends(verificar_llave)])
async def analizar_archivos_completo(files: List[UploadFile] = File(...), indexar_para_busqueda: bool = True):
    texto_acumulado = ""
    partes_para_gemini = []
    
    try:
        # Lectura de archivos (Tu lógica intacta)
        for archivo in files:
            contenido = await archivo.read()
            mime = detectar_mime_real(archivo.filename, archivo.content_type)
            if "text" in mime:
                txt = contenido.decode('utf-8', errors='ignore')
                texto_acumulado += txt + "\n"
                partes_para_gemini.append(f"\n--- DOC ({archivo.filename}) ---\n{txt}\n")
            else:
                partes_para_gemini.append(types.Part.from_bytes(data=contenido, mime_type=mime))

        # Motores locales
        datos_spacy = analizar_texto_con_spacy(texto_acumulado[:50000])
        urgencia_global = predecir_urgencia_ml(texto_acumulado[:1000])

        prompt_sistema = f"""
        Actúa como Analista. Entidades: {', '.join(datos_spacy['personas'])}
        Urgencia: {urgencia_global}
        Responde JSON exacto:
        {{
            "ASISTENTE_ACTIVO": {{
                "resumen_rapido": "Resumen ejecutivo.",
                "alertas_urgentes": ["Alertas"],
                "nivel_riesgo": "{urgencia_global}"
            }}
        }}
        """
        partes_para_gemini.insert(0, prompt_sistema)
        
        response = client.models.generate_content(
            model=MODELO_IA, 
            contents=partes_para_gemini, 
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        resultado = json.loads(response.text.replace("```json", "").replace("```", ""))

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

if __name__ == "__main__":
    import uvicorn
    # reload=True es útil mientras desarrollas para ver cambios en vivo

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

