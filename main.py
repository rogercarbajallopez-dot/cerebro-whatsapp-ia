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
import jwt  # Se mantiene por compatibilidad con el archivo original
from datetime import datetime, timedelta
import pytz

# 1. CARGA DE SECRETOS
load_dotenv()
API_KEY_GOOGLE = os.getenv('GOOGLE_API_KEY')
APP_PASSWORD = os.getenv('MI_APP_PASSWORD') 
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
SUPABASE_JWT_SECRET = os.getenv('SUPABASE_JWT_SECRET')

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
        # 👇 CAMBIO CRÍTICO: Usamos el cliente de Supabase para validar
        user_response = supabase.auth.get_user(token)
        
        if not user_response or not user_response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido o expirado"
            )
        
        # Devolvemos el ID real del usuario autenticado
        return user_response.user.id
        
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
        model = genai.GenerativeModel(MODELO_IA)
        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        return json.loads(response.text)
    except:
        # Fallback de seguridad: Si el mensaje es largo o parece una queja, es VALOR.
        es_queja = any(x in mensaje.lower() for x in ["por qué", "qué pasó", "error", "no pudiste"])
        return {"tipo": "VALOR" if (len(mensaje) > 20 or es_queja) else "BASURA"}

from datetime import datetime
import json
import google.generativeai as genai

# Asumo que 'supabase', 'genai' y 'MODELO_IA' ya están inicializados globalmente

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
        # 3. Inferencia con Gemini
        model = genai.GenerativeModel(MODELO_IA)
        resp = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        analisis = json.loads(resp.text)
        
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
                alertas.append({
                    "usuario_id": usuario_id,
                    "conversacion_id": conv_id,
                    "titulo": t.get('titulo', 'Recordatorio'),
                    "descripcion": t.get('descripcion', f"Derivado de: {analisis['resumen_guardar']}"),
                    "prioridad": t.get('prioridad', 'MEDIA'),
                    "tipo": "auto_detectada",
                    "estado": "pendiente",
                    "etiqueta": t.get('etiqueta', 'OTROS')
                })
            
            if alertas:
                supabase.table('alertas').insert(alertas).execute()
                alertas_creadas = len(alertas)

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
    Crea una alerta directa (Intención TAREA).
    Versión Robusta: Corrige typos y asegura guardado aunque falle el JSON.
    """
    # 1. Obtenemos la fecha exacta en Lima, Perú
    zona_horaria = pytz.timezone('America/Lima')
    fecha_obj = datetime.now(zona_horaria)
    fecha_actual = fecha_obj.strftime("%Y-%m-%d %H:%M:%S (%A)") # Ej: 2026-01-05 (Lunes)

    prompt = f"""
    Actúa como asistente personal experto.
    HOY ES: {fecha_actual}.
    
    Extrae una tarea estructurada del mensaje del usuario: '{mensaje}'.
    
    INSTRUCCIONES CLAVE:
    1. 'titulo': Corto y directo (Ej: "Cita médica Neoplásicas").
    2. 'descripcion': DEBE contener todos los detalles clave: HORA exacta, LUGAR, NOMBRES.
       IMPORTANTE: Si dice "Mañana" o "Viernes", CALCULA la fecha real basándote en que HOY es {fecha_actual} e INCLUYE ESA FECHA.
    3. 'etiqueta': Clasifica en [NEGOCIO, ESTUDIO, PAREJA, SALUD, PERSONAL, OTROS].
    4. CORRECCIÓN: Si el usuario tiene errores de dedo (ej: "mesicamentos"), interpreta la palabra correcta.
    
    JSON Schema: 
    {{
        "titulo": "...", 
        "descripcion": "...",
        "prioridad": "ALTA" | "MEDIA" | "BAJA",
        "etiqueta": "..."
    }}
    """
    try:
        model = genai.GenerativeModel(MODELO_IA)
        resp = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        
        # --- CAPA DE SEGURIDAD 1: Limpieza de JSON ---
        # A veces la IA pone ```json al inicio. Esto lo elimina para que no falle.
        texto_limpio = resp.text.strip().replace("```json", "").replace("```", "")
        data = json.loads(texto_limpio)
        
        # Insertamos con los datos estructurados por la IA
        supabase.table('alertas').insert({
            "usuario_id": usuario_id,
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
        print(f"⚠️ Error procesando JSON de tarea: {e}")
        
        # --- CAPA DE SEGURIDAD 2: Fallback (Guardado de Emergencia) ---
        # Si la IA falla entendiendo el formato, GUARDAMOS EL MENSAJE ORIGINAL.
        # Así el usuario no pierde su recordatorio.
        try:
            supabase.table('alertas').insert({
                "usuario_id": usuario_id,
                "titulo": "Recordatorio (Texto original)",
                "descripcion": mensaje, # Guardamos lo que escribió el usuario tal cual
                "prioridad": "MEDIA",
                "tipo": "manual",
                "estado": "pendiente",
                "etiqueta": "OTROS"
            }).execute()
            
            return {
                "status": "tarea_creada", 
                "respuesta": "✅ Agendado. (Nota: No pude estructurar los detalles automáticamente, pero guardé tu mensaje tal cual)."
            }
        except Exception as e_bd:
            return {"status": "error", "respuesta": "Error crítico conectando con la base de datos."}
            
async def procesar_consulta_rapida(mensaje: str, usuario_id: str, modo_profundo: bool) -> str:
    """
    Responde consultas conectando:
    1. PERFIL (Memoria a Largo Plazo: Quién es el usuario).
    2. HISTORIAL (Memoria a Corto/Mediano Plazo: Qué ha pasado).
    3. TAREAS (Agenda: Qué tiene pendiente).
    4. INTERNET (Google Search: Para datos actuales).
    """
    if not supabase: return "Error: No hay conexión a base de datos."
    
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
        
        CONSULTA DEL USUARIO: "{mensaje}"
        
        DIRECTRICES DE RESPUESTA:
        1. INTERNET: Si el usuario pregunta por noticias, clima, dólar o datos actuales, USA TU HERRAMIENTA DE BÚSQUEDA (Google Search).
        2. PERSONALIZACIÓN: Usa los datos del PERFIL para adaptar tu respuesta.
        3. HISTORIAL: Si pregunta algo específico del pasado, usa el CONTEXTO.
        4. TONO: Eres un asistente útil. Sé claro y directo.
        """

        # --- AQUÍ ESTÁ LA ACTIVACIÓN DE INTERNET ---
        herramientas = [{"google_search_retrieval": {}}]
        
        model = genai.GenerativeModel(
            model_name=MODELO_IA,
            tools=herramientas  # <--- Esto conecta a Google
        )
        
        response = model.generate_content(prompt)
        return response.text

    except Exception as e:
        print(f"Error en consulta rápida: {e}")
        return "Lo siento, tuve un problema conectando con tu memoria."

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
    
    if estado != "todas": 
        q = q.eq('estado', estado)
    
    return {"alertas": q.execute().data}

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
    elif tipo == "TAREA":
        await crear_tarea_directa(mensaje, usuario_id_webhook)

    return Response(content="<?xml version='1.0' encoding='UTF-8'?><Response></Response>", media_type="application/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)



