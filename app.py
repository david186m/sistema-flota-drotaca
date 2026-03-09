import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import plotly.express as px
import os
import io
import openpyxl
from fpdf import FPDF
import tempfile

# --- 1. CONFIGURACIÓN DE PÁGINA (Debe ser la primera línea) ---
st.set_page_config(page_title="Control de Flota Drotaca", page_icon="🚛", layout="wide")

# --- 2. CONFIGURACIÓN DE LA MEMORIA (SESSION STATE) ---
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False
if "usuario_actual" not in st.session_state:
    st.session_state.usuario_actual = ""

# --- 3. FUNCIONES GENERALES ---
def obtener_hora_venezuela():
    return datetime.utcnow() - timedelta(hours=4)

def limpiar_numero_logistica(valor):
    if valor is None or valor == "" or str(valor).lower() == "none":
        return 0.0
    s = str(valor).upper().replace('KMS', '').strip()
    if '.' in s and ',' in s:
        if s.rfind(',') > s.rfind('.'):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    elif '.' in s:
        if len(s.split('.')[-1]) == 3:
            s = s.replace('.', '')
    elif ',' in s:
        if len(s.split(',')[-1]) == 3:
            s = s.replace(',', '')
        else:
            s = s.replace(',', '.')
    try:
        return float(s)
    except:
        return 0.0

def color_gps(val):
    color = '#198754' if val == 'GPS Operativo' else '#DC3545'
    return f'color: {color}; font-weight: bold;'

def color_estatus(val):
    val_str = str(val).strip().upper()
    if val_str == 'OPERATIVO': return 'color: #198754; font-weight: bold;'
    elif val_str == 'NO OPERATIVO': return 'color: #DC3545; font-weight: bold;'
    return ''

def color_taller(val):
    val_str = str(val).strip().upper()
    if val_str == 'TERMINADO': return 'color: #198754; font-weight: bold;'
    if val_str == 'EN PROCESO': return 'color: #DC3545; font-weight: bold;'
    if '⚠️' in val_str: return 'color: #DC3545; font-weight: bold;'
    return ''

# DICCIONARIO PARA MESES EN ESPAÑOL
MESES_ESPANOL = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
}

# --- MOTOR GENERADOR DE PDF ---
def crear_pdf_operativo(nombre, rol, df_datos, dias_tot, dias_trab, dias_inac, mes_filtro, extra_filtros=""):
    class PDF(FPDF):
        def header(self):
            self.set_font('Arial', 'B', 14)
            self.set_fill_color(26, 59, 92)
            self.set_text_color(255, 255, 255)
            self.cell(0, 10, f' DROTACA - REPORTE DE ROTACION: {rol.upper()}', 0, 1, 'C', 1)
            self.ln(3)

    pdf = PDF('L', 'mm', 'A4') 
    pdf.add_page()
    
    pdf.set_font('Arial', 'B', 12)
    pdf.set_text_color(0, 0, 0)
    nombre_safe = nombre.replace("Ñ", "N").replace("ñ", "n") 
    pdf.cell(0, 6, f"Perfil Operativo: {nombre_safe}", 0, 1)
    
    pdf.set_font('Arial', 'B', 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 5, f"Periodo: {mes_filtro} {extra_filtros}", 0, 1)

    pdf.set_font('Arial', '', 10)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 6, f"Dias Registrados: {dias_tot}  |  Trabajados: {dias_trab}  |  Inactivos: {dias_inac}", 0, 1)
    pdf.ln(4)

    columnas = df_datos.columns.tolist()
    
    # REINGENIERÍA DE ANCHOS DE COLUMNA (Se le dio más espacio a UNIDAD)
    if rol == 'Chofer':
        # Columnas: FECHA, DIA, UNIDAD, RUTA, ZONA, OBSERVACIÓN
        anchos = [18, 18, 33, 119, 22, 65] # Total 275mm 
    else:
        # Columnas: FECHA, DIA, CHOFER, UNIDAD, RUTA, ZONA, OBSERVACIÓN
        anchos = [18, 18, 38, 30, 90, 20, 61] # Total 275mm

    pdf.set_font('Arial', 'B', 9)
    pdf.set_fill_color(230, 230, 230)
    for i, col in enumerate(columnas):
        pdf.cell(anchos[i], 8, str(col), 1, 0, 'C', 1)
    pdf.ln()

    pdf.set_font('Arial', '', 8)
    for index, row in df_datos.iterrows():
        for i, col in enumerate(columnas):
            texto = str(row[col])
            texto = texto.replace("Ñ","N").replace("ñ","n").replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")
            texto = texto.replace("Á","A").replace("É","E").replace("Í","I").replace("Ó","O").replace("Ú","U")
            
            # Recortador ajustado para que permita más letras si la columna es más ancha
            max_chars = int(anchos[i] * 0.70) 
            texto_limpio = texto[:max_chars]
            
            pdf.cell(anchos[i], 7, texto_limpio, 1, 0, 'L')
        pdf.ln()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        pdf.output(tmp.name)
        tmp.seek(0)
        data = tmp.read()
    os.remove(tmp.name)
    return data

# --- 4. PROCESAMIENTO DE DATOS (MÓDULO DE FLOTA) ---
@st.cache_data(ttl=60)
def cargar_y_procesar_datos():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        credenciales_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(credenciales_dict, scope)
    except:
        creds = ServiceAccountCredentials.from_json_keyfile_name("credenciales.json", scope)
        
    cliente = gspread.authorize(creds)
    libro_flota = cliente.open("Sistema_Flota_2026")
    ws_km = libro_flota.worksheet("Kilometraje")
    ws_control = libro_flota.worksheet("Control_Diario")
    ws_maestro = libro_flota.worksheet("Maestro_Flota")
    ws_taller = libro_flota.worksheet("Historial_Taller")
    
    try:
        hora_sincronizacion = ws_control.acell('Z2').value
        if not hora_sincronizacion:
            hora_sincronizacion = "Esperando primera edición..."
    except:
        hora_sincronizacion = "Sin registro en celda Z2"

    df_km = pd.DataFrame(ws_km.get_all_values()[1:], columns=ws_km.get_all_values()[0])
    df_control = pd.DataFrame(ws_control.get_all_values()[1:], columns=ws_control.get_all_values()[0])
    df_maestro = pd.DataFrame(ws_maestro.get_all_values()[1:], columns=ws_maestro.get_all_values()[0])
    
    datos_taller = ws_taller.get_all_values()
    columnas_taller = ['Placa', 'Ruta', 'Zona', 'Fecha_Entrada', 'Motivo_Falla', 'Estatus_Reparacion', 'Fecha_Salida', 'Taller / Mecánico']
    df_taller = pd.DataFrame(datos_taller[1:], columns=datos_taller[0]) if len(datos_taller) > 1 else pd.DataFrame(columns=columnas_taller)
    
    if not df_taller.empty and 'Fecha_Entrada' in df_taller.columns:
        df_taller['Fecha_Entrada_DT'] = pd.to_datetime(df_taller['Fecha_Entrada'], dayfirst=True, errors='coerce')
        df_taller['Fecha_Salida_DT'] = pd.to_datetime(df_taller['Fecha_Salida'], dayfirst=True, errors='coerce')
        hoy = pd.Timestamp.today().normalize()
        def calcular_duracion(row):
            if pd.isna(row['Fecha_Entrada_DT']): return ""
            fecha_fin = row['Fecha_Salida_DT'] if pd.notna(row['Fecha_Salida_DT']) else hoy
            dias = max(0, (fecha_fin - row['Fecha_Entrada_DT']).days)
            return f"⚠️ {dias} días" if dias > 10 else f"{dias} días"
        df_taller['Duración'] = df_taller.apply(calcular_duracion, axis=1)
        df_taller = df_taller.drop(columns=['Fecha_Entrada_DT', 'Fecha_Salida_DT'])
        if 'Duración' in df_taller.columns and 'Fecha_Salida' in df_taller.columns:
            cols = df_taller.columns.tolist()
            cols.insert(cols.index('Fecha_Salida') + 1, cols.pop(cols.index('Duración')))
            df_taller = df_taller[cols]

    df_km['FECHA_DT'] = pd.to_datetime(df_km['FECHA'], format='%d/%m/%Y', errors='coerce')
    df_km['KM_LIMPIO'] = df_km['KILOMETRAJE'].apply(limpiar_numero_logistica)
    df_validos = df_km[df_km['KM_LIMPIO'] > 0].copy()
    df_ultimo = df_validos.sort_values('FECHA_DT', ascending=False).drop_duplicates('UNIDAD')
    inicio_mes = pd.Timestamp(2026, 3, 1)
    
    km_historico = df_validos.groupby(['UNIDAD', 'FECHA_DT'])['KM_LIMPIO'].max().reset_index()
    km_historico = km_historico.sort_values(['UNIDAD', 'FECHA_DT'])
    km_historico['Recorrido_Dia'] = km_historico.groupby('UNIDAD')['KM_LIMPIO'].diff().fillna(0)
    km_mes = km_historico[km_historico['FECHA_DT'] >= inicio_mes]
    dias_activos_df = km_mes[km_mes['Recorrido_Dia'] >= 70].groupby('UNIDAD').size().reset_index(name='dias_activos')
    
    df_mes_actual = df_validos[df_validos['FECHA_DT'] >= inicio_mes]
    df_mes_anterior = df_validos[df_validos['FECHA_DT'] < inicio_mes]
    recorrido_mes = df_mes_actual.groupby('UNIDAD').agg(km_max_mes=('KM_LIMPIO', 'max'), km_min_mes_actual=('KM_LIMPIO', 'min')).reset_index()
    cierre_mes_anterior = df_mes_anterior.groupby('UNIDAD').agg(km_cierre_anterior=('KM_LIMPIO', 'max')).reset_index()
    recorrido_mes = pd.merge(recorrido_mes, cierre_mes_anterior, on='UNIDAD', how='left')
    recorrido_mes['km_arranque'] = recorrido_mes['km_cierre_anterior'].fillna(recorrido_mes['km_min_mes_actual'])
    recorrido_mes['Km Mensual Actual'] = recorrido_mes['km_max_mes'] - recorrido_mes['km_arranque']
    recorrido_mes = pd.merge(recorrido_mes, dias_activos_df, on='UNIDAD', how='left')
    recorrido_mes['dias_activos'] = recorrido_mes['dias_activos'].fillna(0).apply(lambda x: x if x > 0 else 1)

    df_merge = pd.merge(df_control[['Placa', 'Grupo', 'RUTA']], df_ultimo[['UNIDAD', 'KM_LIMPIO', 'FECHA']], left_on="Placa", right_on="UNIDAD", how="left")
    df_merge2 = pd.merge(df_merge, recorrido_mes[['UNIDAD', 'Km Mensual Actual', 'dias_activos']], on="UNIDAD", how="left")
    
    if 'Placa' in df_maestro.columns:
        cols_maestro = ['Placa']
        if 'Fecha_GPS' in df_maestro.columns: cols_maestro.append('Fecha_GPS')
        if 'Modelo' in df_maestro.columns: cols_maestro.append('Modelo')
        df_final = pd.merge(df_merge2, df_maestro[cols_maestro], on="Placa", how="left")
        if 'Fecha_GPS' in df_final.columns:
            df_final['Estatus GPS'] = df_final['Fecha_GPS'].apply(lambda x: 'Sin GPS' if pd.isna(x) or str(x).strip().upper() == 'SIN GPS' or str(x).strip() == '' else 'GPS Operativo')
            df_final = df_final.drop(columns=['Fecha_GPS'])
        if 'Modelo' in df_final.columns:
            df_final['Modelo'] = df_final['Modelo'].replace('', 'N/A').fillna('N/A')
    else:
        df_final = df_merge2
        df_final['Estatus GPS'] = "Error"
        df_final['Modelo'] = "Error"
    
    if not df_taller.empty and 'Placa' in df_taller.columns and 'Estatus_Reparacion' in df_taller.columns:
        df_taller_ultimo = df_taller.drop_duplicates(subset=['Placa'], keep='last').copy()
        en_taller = df_taller_ultimo['Estatus_Reparacion'].str.strip().str.upper() != 'TERMINADO'
        df_activos = df_taller_ultimo[en_taller][['Placa', 'Fecha_Entrada', 'Motivo_Falla', 'Taller / Mecánico']]
        df_final = pd.merge(df_final, df_activos, on="Placa", how="left")
        df_final['Estatus_Unidad'] = df_final['Fecha_Entrada'].apply(lambda x: 'No Operativo' if pd.notna(x) else 'Operativo')
        def armar_observacion(row):
            if pd.notna(row['Motivo_Falla']):
                mecanico = row['Taller / Mecánico'] if pd.notna(row['Taller / Mecánico']) and str(row['Taller / Mecánico']).strip() != '' else 'Sin asignar'
                return f"{row['Motivo_Falla']} ({mecanico})"
            return ""
        df_final['Observacion'] = df_final.apply(armar_observacion, axis=1)
        df_final['Fecha_Inoperativo'] = df_final['Fecha_Entrada'].fillna('')
        df_final = df_final.drop(columns=['Fecha_Entrada', 'Motivo_Falla', 'Taller / Mecánico'])
    else:
        df_final['Estatus_Unidad'] = 'Operativo'
        df_final['Observacion'] = ''
        df_final['Fecha_Inoperativo'] = ''

    df_final = df_final.drop(columns=['UNIDAD'])
    df_final = df_final.rename(columns={'KM_LIMPIO': 'Km Actual', 'FECHA': 'Última Actualización'})
    df_final['Km Actual'] = df_final['Km Actual'].fillna(0)
    df_final['Km Mensual Actual'] = df_final['Km Mensual Actual'].fillna(0)
    df_final['dias_activos'] = df_final['dias_activos'].fillna(1)
    df_final['Última Actualización'] = df_final['Última Actualización'].fillna("Sin Registro")
    
    columnas_orden = ['Placa', 'Modelo', 'Grupo', 'RUTA', 'Estatus_Unidad', 'Estatus GPS', 'Km Actual', 'Km Mensual Actual', 'dias_activos', 'Última Actualización', 'Observacion', 'Fecha_Inoperativo']
    return df_final[columnas_orden], df_taller, hora_sincronizacion

# --- 4.1 PROCESAMIENTO DE DATOS (MÓDULO DE PERSONAL) ---
@st.cache_data(ttl=120)
def cargar_datos_personal():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        credenciales_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(credenciales_dict, scope)
    except:
        creds = ServiceAccountCredentials.from_json_keyfile_name("credenciales.json", scope)
        
    cliente = gspread.authorize(creds)
    libro = cliente.open("Sistema_Flota_2026")
    
    try:
        ws_choferes = libro.worksheet("Rotacion_Choferes")
        df_choferes = pd.DataFrame(ws_choferes.get_all_values()[1:], columns=ws_choferes.get_all_values()[0])
        df_choferes['FECHA_DT'] = pd.to_datetime(df_choferes['FECHA'], format='%d/%m/%Y', errors='coerce')
    except:
        df_choferes = pd.DataFrame()

    try:
        ws_ayudantes = libro.worksheet("Rotacion_Ayudantes")
        df_ayudantes = pd.DataFrame(ws_ayudantes.get_all_values()[1:], columns=ws_ayudantes.get_all_values()[0])
        df_ayudantes['FECHA_DT'] = pd.to_datetime(df_ayudantes['FECHA'], format='%d/%m/%Y', errors='coerce')
    except:
        df_ayudantes = pd.DataFrame()
        
    return df_choferes, df_ayudantes

# --- 5. PANTALLA DE LOGIN ---
def pantalla_login():
    st.markdown("""
    <style>
    [data-testid="stApp"] { background: radial-gradient(circle at center, #151b26 0%, #080a0e 100%) !important; color: #ffffff; }
    [data-testid="stHeader"] { background-color: transparent !important; }
    [data-testid="stForm"] { background-color: rgba(28, 34, 45, 0.8) !important; border-radius: 20px !important; border: 1px solid rgba(0, 212, 255, 0.2) !important; box-shadow: 0 15px 35px rgba(0, 0, 0, 0.5) !important; backdrop-filter: blur(10px) !important; padding: 40px 30px !important; }
    [data-testid="stForm"] div[data-baseweb="input"] { background-color: rgba(0, 0, 0, 0.3) !important; border: 1px solid rgba(255, 255, 255, 0.1) !important; border-radius: 8px !important; transition: all 0.3s ease !important; }
    [data-testid="stForm"] div[data-baseweb="input"]:focus-within { border-color: #00d4ff !important; box-shadow: 0 0 8px rgba(0, 212, 255, 0.3) !important; }
    [data-testid="stForm"] input { color: white !important; }
    [data-testid="stForm"] label p { color: #a0a0a0 !important; font-size: 0.9rem !important; }
    [data-testid="stFormSubmitButton"] button { background: linear-gradient(45deg, #0056b3, #00d4ff) !important; border: none !important; border-radius: 8px !important; color: white !important; font-weight: bold !important; letter-spacing: 1px !important; transition: transform 0.2s, box-shadow 0.2s !important; width: 100% !important; margin-top: 10px !important; }
    [data-testid="stFormSubmitButton"] button:hover { transform: translateY(-2px) !important; box-shadow: 0 5px 15px rgba(0, 212, 255, 0.4) !important; }
    .login-title { color: #ffffff; font-size: 2.2rem; letter-spacing: 2px; text-align: center; margin-bottom: 5px; font-weight: 600; }
    .login-title span { color: #00d4ff; font-weight: 300; }
    .login-subtitle { color: #a0a0a0; font-size: 0.95rem; text-align: center; margin-bottom: 30px; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("<br><br>", unsafe_allow_html=True) 
    
    usuarios_permitidos = {
        "David_Admin": "Drotaca2026",
        "Supervisor_Oriente": "Oriente26",
        "Supervisor_Occidente": "Occidente26",
        "Jsuarez": "295377886"
    }

    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        nombre_imagen = "logo.png" 
        if os.path.exists(nombre_imagen):
            st.image(nombre_imagen, width=300)
            st.markdown("<br>", unsafe_allow_html=True)
        else:
            st.warning(f"⚠️ No se encontró el archivo '{nombre_imagen}' en la carpeta.")

        st.markdown("<div class='login-title'>🚛 Monitoreo Integral <span>2026</span></div>", unsafe_allow_html=True)
        st.markdown("<div class='login-subtitle'>Acceso Restringido al Sistema</div><br>", unsafe_allow_html=True)

        with st.form("formulario_login"):
            usuario_input = st.text_input("👤 Usuario:")
            password_input = st.text_input("🔑 Contraseña:", type="password")
            boton_ingresar = st.form_submit_button("Ingresar al Sistema", use_container_width=True)
            
            if boton_ingresar:
                if usuario_input in usuarios_permitidos and usuarios_permitidos[usuario_input] == password_input:
                    st.session_state.autenticado = True
                    st.session_state.usuario_actual = usuario_input
                    st.rerun() 
                else:
                    st.error("Usuario o contraseña incorrectos.")

# --- 6. MÓDULO 1: CONTROL DE FLOTA ---
def modulo_flota():
    col_titulo, col_boton = st.columns([0.8, 0.2])
    with col_titulo:
        st.title("🚛 Panel de Control de Flota - Drotaca")
    with col_boton:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 Actualizar Datos", key="btn_flota", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    st.divider()

    try:
        df_resultados, df_historial_taller, ultima_sync = cargar_y_procesar_datos()
        ahora = obtener_hora_venezuela()
        dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        meses_año = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        fecha_reloj = f"{dias_semana[ahora.weekday()]}, {ahora.strftime('%d-%m-%Y - %I:%M %p')}"
        mes_actual_texto = f"{meses_año[ahora.month - 1]} {ahora.year}"
        
        st.markdown(f"""
        <div style="display: flex; justify-content: space-between; flex-wrap: wrap; background-color: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 5px solid #003366; margin-bottom: 20px;">
            <div style="font-size: 15px; margin-bottom: 5px;">
                🕒 <b>Hora del Sistema:</b> <span style="color: #003366;">{fecha_reloj}</span>
            </div>
            <div style="font-size: 15px;">
                📡 <b>Última edición real del documento:</b> <span style="color: #198754; font-weight: bold;">{ultima_sync}</span> <span style="font-size: 13px; color: gray;">(Por el equipo)</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        tab1, tab2 = st.tabs(["📊 Monitoreo en Tiempo Real", "🛠️ Gestión de Taller"])
        
        with tab1:
            opciones = ["Todos los vehículos"] + sorted(df_resultados["Grupo"].unique().tolist())
            seleccion = st.selectbox("📌 Filtrar por Región/Grupo:", opciones)
            
            df_mostrar = df_resultados if seleccion == "Todos los vehículos" else df_resultados[df_resultados["Grupo"] == seleccion]
            
            total_unidades = len(df_mostrar)
            total_km_mensual = df_mostrar['Km Mensual Actual'].sum()
            sin_gps_count = len(df_mostrar[df_mostrar['Estatus GPS'] == 'Sin GPS'])
            no_operativas_count = len(df_mostrar[df_mostrar['Estatus_Unidad'] == 'No Operativo'])
            operativas_count = total_unidades - no_operativas_count

            st.markdown("<br>", unsafe_allow_html=True)
            col1, col2, col3, col4, col5 = st.columns(5)
            with col1: st.metric(label="Total Flota", value=f"{total_unidades} 🚚")
            with col2: st.metric(label="✅ Operativas", value=f"{operativas_count}")
            with col3: st.metric(label="⚠️ En Taller", value=f"{no_operativas_count}")
            with col4: st.metric(label="❌ Sin GPS", value=f"{sin_gps_count}")
            with col5: st.metric(label="Recorrido Mensual", value=f"{total_km_mensual:,.2f} km")
            st.divider()

            st.subheader(f"📈 Análisis de Recorrido: {mes_actual_texto}")
            if seleccion == "Todos los vehículos":
                km_data = df_mostrar.groupby("Grupo")["Km Mensual Actual"].sum().reset_index()
                eje_x = "Grupo"
            else:
                km_data = df_mostrar[["Placa", "Km Mensual Actual"]].copy()
                eje_x = "Placa"
                
            km_data = km_data.sort_values(by="Km Mensual Actual", ascending=False)
            km_data['Etiqueta'] = km_data['Km Mensual Actual'].apply(lambda x: f"{x:,.0f} Kms".replace(",", "X").replace(".", ",").replace("X", "."))
            fig = px.bar(km_data, x=eje_x, y="Km Mensual Actual", text="Etiqueta")
            fig.update_traces(textposition='outside', marker_color='#1A3B5C', cliponaxis=False)
            fig.update_layout(xaxis_title="", yaxis_title="Kilómetros Recorridos", dragmode=False, margin=dict(t=30, b=0, l=0, r=0), height=400)
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False, 'scrollZoom': False})
            st.divider()
            
            df_promedios = df_mostrar.copy()
            df_promedios['Km_Num'] = pd.to_numeric(df_promedios['Km Mensual Actual'], errors='coerce').fillna(0)
            df_promedios['Odometer_Num'] = pd.to_numeric(df_promedios['Km Actual'], errors='coerce').fillna(0)
            df_promedios['Dias_Num'] = pd.to_numeric(df_promedios['dias_activos'], errors='coerce').fillna(1)
            dias_calendario = ahora.day if ahora.day > 0 else 1

            df_promedios['Promedio_Diario_Num'] = df_promedios['Km_Num'] / df_promedios['Dias_Num']
            ritmo_diario_real = df_promedios['Km_Num'] / dias_calendario
            df_promedios['Promedio_Semanal_Num'] = ritmo_diario_real * 7
            df_promedios['Promedio_Mensual_Num'] = df_promedios['Km_Num'] 

            def format_kms(val):
                try:
                    return f"{int(val):,} Kms".replace(',', '.')
                except:
                    return "0 Kms"

            df_promedios['RECORRIDO PROMEDIO DIARIO'] = df_promedios['Promedio_Diario_Num'].apply(format_kms)
            df_promedios['RECORRIDO PROMEDIO SEMANAL'] = df_promedios['Promedio_Semanal_Num'].apply(format_kms)
            df_promedios['RECORRIDO PROMEDIO MENSUAL'] = df_promedios['Promedio_Mensual_Num'].apply(format_kms)
            df_promedios['Km Actual Formato'] = df_promedios['Odometer_Num'].apply(format_kms)

            columnas_finales = [
                'Placa', 'Modelo', 'Grupo', 'RUTA', 'Estatus_Unidad', 'Estatus GPS', 
                'Km Actual Formato', 'RECORRIDO PROMEDIO DIARIO', 'RECORRIDO PROMEDIO SEMANAL', 
                'RECORRIDO PROMEDIO MENSUAL', 'Última Actualización', 'Observacion', 'Fecha_Inoperativo'
            ]
            
            columnas_existentes = [col for col in columnas_finales if col in df_promedios.columns]
            df_display = df_promedios[columnas_existentes].rename(columns={'Km Actual Formato': 'Km Actual'}).copy()

            col_tabla1, col_tabla2 = st.columns([0.7, 0.3])
            with col_tabla1:
                st.subheader(f"📑 Reporte Detallado: {seleccion}")
            with col_tabla2:
                try:
                    df_export = df_promedios.copy()
                    wb = openpyxl.load_workbook("INFORME GERENCIAL.xlsx")
                    ws = wb.active 
                    titulo_zona = f"INFORME MENSUAL - RUTA {seleccion.upper()} 2026" if seleccion != "Todos los vehículos" else "INFORME MENSUAL - TODA LA FLOTA 2026"
                    ws['E1'] = titulo_zona
                    ws['J1'] = meses_año[ahora.month - 1].upper()

                    fila_inicio = 3
                    for index, row in enumerate(df_export.to_dict('records')):
                        fila_actual = fila_inicio + index
                        ws.cell(row=fila_actual, column=1, value=index + 1)
                        ws.cell(row=fila_actual, column=2, value=row.get('Placa', ''))
                        ws.cell(row=fila_actual, column=3, value=row.get('Modelo', ''))
                        ws.cell(row=fila_actual, column=4, value=row.get('Grupo', ''))
                        ws.cell(row=fila_actual, column=5, value=row.get('RUTA', ''))
                        ws.cell(row=fila_actual, column=6, value=row.get('Estatus_Unidad', ''))
                        ws.cell(row=fila_actual, column=7, value=row.get('Estatus GPS', ''))
                        ws.cell(row=fila_actual, column=8, value=row.get('Odometer_Num', 0))
                        ws.cell(row=fila_actual, column=9, value=row.get('Promedio_Diario_Num', 0))
                        ws.cell(row=fila_actual, column=10, value=row.get('Promedio_Semanal_Num', 0))
                        ws.cell(row=fila_actual, column=11, value=row.get('Promedio_Mensual_Num', 0)) 

                    for r in range(fila_inicio + len(df_export), 72):
                        ws.row_dimensions[r].hidden = True

                    output = io.BytesIO()
                    wb.save(output)
                    output.seek(0)

                    st.download_button(
                        label="📥 Descargar Informe Gerencial",
                        data=output,
                        file_name=f"INFORME_GERENCIAL_{seleccion.replace(' ', '_')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
                except FileNotFoundError:
                    st.warning("⚠️ Sube 'INFORME GERENCIAL.xlsx' a tu carpeta.")
            
            total_diario = df_promedios['Promedio_Diario_Num'].sum()
            total_semanal = df_promedios['Promedio_Semanal_Num'].sum()
            total_mensual = df_promedios['Promedio_Mensual_Num'].sum()

            totals_row = {col: "" for col in df_display.columns}
            totals_row['Placa'] = "TOTALES"
            if 'RECORRIDO PROMEDIO DIARIO' in totals_row: totals_row['RECORRIDO PROMEDIO DIARIO'] = format_kms(total_diario)
            if 'RECORRIDO PROMEDIO SEMANAL' in totals_row: totals_row['RECORRIDO PROMEDIO SEMANAL'] = format_kms(total_semanal)
            if 'RECORRIDO PROMEDIO MENSUAL' in totals_row: totals_row['RECORRIDO PROMEDIO MENSUAL'] = format_kms(total_mensual)

            df_display = pd.concat([df_display, pd.DataFrame([totals_row])], ignore_index=True)

            def aplicar_estilos_dinamicos(row):
                styles = [''] * len(row)
                if row['Placa'] == 'TOTALES':
                    return ['background-color: #ffff00; color: black; font-weight: bold; text-align: center;'] * len(row)
                for i, col in enumerate(row.index):
                    if col == 'Estatus GPS': styles[i] = color_gps(row[col]) + ' text-align: center;'
                    elif col == 'Estatus_Unidad': styles[i] = color_estatus(row[col]) + ' text-align: center;'
                    elif col in ['RECORRIDO PROMEDIO DIARIO', 'RECORRIDO PROMEDIO SEMANAL', 'RECORRIDO PROMEDIO MENSUAL']:
                        color = '#1f497d' if 'DIARIO' in col else ('#4f81bd' if 'SEMANAL' in col else '#e46c0a')
                        styles[i] = f'background-color: {color}; color: white; font-weight: bold; text-align: center;'
                    else: styles[i] = 'text-align: center;'
                return styles

            estilos_tabla = [dict(selector="th", props=[("background-color", "#1A3B5C"), ("color", "white"), ("text-align", "center")])]
            st.dataframe(df_display.style.apply(aplicar_estilos_dinamicos, axis=1).set_table_styles(estilos_tabla), use_container_width=True, hide_index=True)

        with tab2:
            st.subheader("🛠️ Registro Histórico de Mantenimiento")
            busqueda = st.text_input("🔍 Buscar por Placa (Ej. A0378AK):").upper()
            if not df_historial_taller.empty:
                df_mostrar_taller = df_historial_taller[df_historial_taller['Placa'].str.contains(busqueda, na=False)] if busqueda else df_historial_taller
                st.dataframe(df_mostrar_taller.style.set_table_styles(estilos_tabla).map(color_taller, subset=['Estatus_Reparacion']), use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"Error cargando los datos de Flota: {e}")

# --- 7. MÓDULO 2: ROTACIÓN DE PERSONAL (CON FILTROS Y PDF AJUSTADO) ---
def modulo_personal():
    col_titulo, col_boton = st.columns([0.8, 0.2])
    with col_titulo:
        st.title("👥 Control y Rotación de Personal")
    with col_boton:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 Actualizar Datos", key="btn_personal", use_container_width=True):
            cargar_datos_personal.clear()
            st.rerun()
    
    try:
        df_choferes, df_ayudantes = cargar_datos_personal()
        
        if df_choferes.empty and df_ayudantes.empty:
            st.warning("⚠️ No se encontraron las hojas 'Rotacion_Choferes' o 'Rotacion_Ayudantes' en Google Sheets.")
            return

        # FILTRO GLOBAL POR MES
        if not df_choferes.empty:
            df_choferes['MES_NUM'] = df_choferes['FECHA_DT'].dt.month
            df_choferes['MES_NOMBRE'] = df_choferes['MES_NUM'].map(MESES_ESPANOL)
        if not df_ayudantes.empty:
            df_ayudantes['MES_NUM'] = df_ayudantes['FECHA_DT'].dt.month
            df_ayudantes['MES_NOMBRE'] = df_ayudantes['MES_NUM'].map(MESES_ESPANOL)

        meses_disponibles = set()
        if not df_choferes.empty: meses_disponibles.update(df_choferes['MES_NOMBRE'].dropna().unique())
        if not df_ayudantes.empty: meses_disponibles.update(df_ayudantes['MES_NOMBRE'].dropna().unique())
        
        meses_ordenados = sorted(list(meses_disponibles), key=lambda m: list(MESES_ESPANOL.values()).index(m))

        st.markdown("""<div style="background-color: #f8f9fa; padding: 10px 20px; border-radius: 8px; border-left: 5px solid #1A3B5C; margin-bottom: 20px;"></div>""", unsafe_allow_html=True)
        col_mes1, col_mes2 = st.columns([0.3, 0.7])
        with col_mes1:
            mes_seleccionado = st.selectbox("📅 Filtrar por Mes:", ["Todo el año"] + meses_ordenados)

        if mes_seleccionado != "Todo el año":
            if not df_choferes.empty: df_choferes = df_choferes[df_choferes['MES_NOMBRE'] == mes_seleccionado]
            if not df_ayudantes.empty: df_ayudantes = df_ayudantes[df_ayudantes['MES_NOMBRE'] == mes_seleccionado]

        st.divider()

        tab_choferes, tab_ayudantes = st.tabs(["🚛 Gestión de Choferes", "👷 Gestión de Ayudantes"])
        estilos_tabla_personal = [dict(selector="th", props=[("background-color", "#1A3B5C"), ("color", "white"), ("text-align", "center")])]

        # --- LÓGICA PARA CHOFERES ---
        with tab_choferes:
            if not df_choferes.empty:
                lista_choferes = sorted([str(x) for x in df_choferes['CHOFER'].unique() if str(x).strip() != ""])
                chofer_sel = st.selectbox("🔍 Buscar Perfil Operativo (Chofer):", ["Resumen General"] + lista_choferes, key="sb_chofer")
                
                if chofer_sel == "Resumen General":
                    st.subheader(f"📊 Métricas Globales de Choferes ({mes_seleccionado})")
                    total_choferes = len(lista_choferes)
                    
                    hoy_str = datetime.now().strftime('%d/%m/%Y')
                    inactivos_hoy = 0
                    if 'FECHA' in df_choferes.columns:
                        df_hoy = df_choferes[df_choferes['FECHA'] == hoy_str]
                        if not df_hoy.empty:
                            condicion_inactivo = df_hoy['OBSERVACIÓN'].astype(str).str.contains('VACACIONES|REPOSO|FALTA', case=False, na=False) | df_hoy['RUTA'].astype(str).str.contains('VACACIONES|REPOSO|FALTA', case=False, na=False)
                            inactivos_hoy = condicion_inactivo.sum()
                    
                    c1, c2, c3 = st.columns(3)
                    with c1: st.metric("Total Choferes Registrados", total_choferes)
                    with c2: st.metric("Choferes Inactivos (Hoy)", inactivos_hoy)
                    with c3: st.metric("Registros en el Periodo", len(df_choferes))
                else:
                    df_ind = df_choferes[df_choferes['CHOFER'] == chofer_sel].copy()
                    
                    # SUBFILTROS INTELIGENTES PARA CHOFER
                    unidades_disp = ["Todas"] + sorted([str(x) for x in df_ind['UNIDAD'].unique() if str(x).strip() != ""])
                    zonas_disp = ["Todas"] + sorted([str(x) for x in df_ind['ZONA'].unique() if str(x).strip() != ""])
                    
                    c_f1, c_f2 = st.columns(2)
                    with c_f1:
                        unidad_sel = st.selectbox("🚛 Filtrar por Unidad (Opcional):", unidades_disp, key="u_ch")
                    with c_f2:
                        zona_sel = st.selectbox("📍 Filtrar por Zona (Opcional):", zonas_disp, key="z_ch")
                        
                    # Aplicar subfiltros
                    if unidad_sel != "Todas": df_ind = df_ind[df_ind['UNIDAD'] == unidad_sel]
                    if zona_sel != "Todas": df_ind = df_ind[df_ind['ZONA'] == zona_sel]
                    
                    texto_filtros = ""
                    if unidad_sel != "Todas": texto_filtros += f" | Und: {unidad_sel}"
                    if zona_sel != "Todas": texto_filtros += f" | Zona: {zona_sel}"

                    df_ind = df_ind.sort_values(by='FECHA_DT', ascending=True)
                    total_dias = len(df_ind)
                    condicion = df_ind['OBSERVACIÓN'].astype(str).str.contains('VACACIONES|REPOSO|FALTA', case=False, na=False) | df_ind['RUTA'].astype(str).str.contains('VACACIONES|REPOSO|FALTA', case=False, na=False)
                    dias_inactivos = condicion.sum()
                    dias_activos = total_dias - dias_inactivos

                    st.markdown(f"### 👤 Perfil Operativo: {chofer_sel}")
                    c1, c2, c3 = st.columns(3)
                    with c1: st.metric("Días Registrados (Total)", total_dias)
                    with c2: st.metric("✅ Días Trabajados", dias_activos)
                    with c3: st.metric("⚠️ Días Inactivos (Vac/Reposo)", dias_inactivos)

                    st.divider()
                    col_graf, col_datos = st.columns([0.4, 0.6])
                    
                    with col_graf:
                        st.write("**Distribución por Zona Trabajada**")
                        df_activos = df_ind[~condicion]
                        if not df_activos.empty and 'ZONA' in df_activos.columns:
                            zonas_count = df_activos['ZONA'].value_counts().reset_index()
                            zonas_count.columns = ['ZONA', 'Días']
                            fig = px.pie(zonas_count, values='Días', names='ZONA', hole=0.4, color_discrete_sequence=px.colors.sequential.Blues_r)
                            fig.update_traces(textposition='inside', textinfo='percent+label')
                            fig.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0), height=300)
                            st.plotly_chart(fig, use_container_width=True)
                        else:
                            st.info("Sin registros de zona para esta selección.")

                    with col_datos:
                        st.write("**Historial Detallado**")
                        columnas_mostrar = ['FECHA', 'DIA', 'UNIDAD', 'RUTA', 'ZONA', 'OBSERVACIÓN']
                        cols_existentes = [c for c in columnas_mostrar if c in df_ind.columns]
                        df_view = df_ind[cols_existentes]
                        st.dataframe(df_view.style.set_table_styles(estilos_tabla_personal), use_container_width=True, hide_index=True)
                        
                        pdf_bytes = crear_pdf_operativo(chofer_sel, "Chofer", df_view, total_dias, dias_activos, dias_inactivos, mes_seleccionado, texto_filtros)
                        st.download_button(label=f"📥 Descargar PDF Gerencial", data=pdf_bytes, file_name=f"Perfil_{chofer_sel.replace(' ', '_')}.pdf", mime="application/pdf", use_container_width=True)

        # --- LÓGICA PARA AYUDANTES ---
        with tab_ayudantes:
            if not df_ayudantes.empty:
                lista_ayudantes = sorted([str(x) for x in df_ayudantes['AYUDANTE'].unique() if str(x).strip() != ""])
                ayu_sel = st.selectbox("🔍 Buscar Perfil Operativo (Ayudante):", ["Resumen General"] + lista_ayudantes, key="sb_ayu")
                
                if ayu_sel == "Resumen General":
                    st.subheader(f"📊 Métricas Globales de Ayudantes ({mes_seleccionado})")
                    total_ayudantes = len(lista_ayudantes)
                    c1, c2, c3 = st.columns(3)
                    with c1: st.metric("Total Ayudantes Registrados", total_ayudantes)
                    with c2: st.metric("Registros en el Periodo", len(df_ayudantes))
                else:
                    df_ind = df_ayudantes[df_ayudantes['AYUDANTE'] == ayu_sel].copy()
                    
                    # SUBFILTROS INTELIGENTES PARA AYUDANTE
                    unidades_disp_a = ["Todas"] + sorted([str(x) for x in df_ind['UNIDAD'].unique() if str(x).strip() != ""])
                    zonas_disp_a = ["Todas"] + sorted([str(x) for x in df_ind['ZONA'].unique() if str(x).strip() != ""])
                    
                    c_f1, c_f2 = st.columns(2)
                    with c_f1:
                        unidad_sel_a = st.selectbox("🚛 Filtrar por Unidad (Opcional):", unidades_disp_a, key="u_ayu")
                    with c_f2:
                        zona_sel_a = st.selectbox("📍 Filtrar por Zona (Opcional):", zonas_disp_a, key="z_ayu")
                        
                    if unidad_sel_a != "Todas": df_ind = df_ind[df_ind['UNIDAD'] == unidad_sel_a]
                    if zona_sel_a != "Todas": df_ind = df_ind[df_ind['ZONA'] == zona_sel_a]
                    
                    texto_filtros_a = ""
                    if unidad_sel_a != "Todas": texto_filtros_a += f" | Und: {unidad_sel_a}"
                    if zona_sel_a != "Todas": texto_filtros_a += f" | Zona: {zona_sel_a}"

                    df_ind = df_ind.sort_values(by='FECHA_DT', ascending=True)
                    total_dias = len(df_ind)
                    condicion = df_ind['OBSERVACIÓN'].astype(str).str.contains('VACACIONES|REPOSO|FALTA', case=False, na=False) | df_ind['RUTA'].astype(str).str.contains('VACACIONES|REPOSO|FALTA', case=False, na=False)
                    dias_inactivos = condicion.sum()
                    dias_activos = total_dias - dias_inactivos

                    st.markdown(f"### 👷 Perfil Operativo: {ayu_sel}")
                    c1, c2, c3 = st.columns(3)
                    with c1: st.metric("Días Registrados (Total)", total_dias)
                    with c2: st.metric("✅ Días Trabajados", dias_activos)
                    with c3: st.metric("⚠️ Días Inactivos (Vac/Reposo)", dias_inactivos)

                    st.divider()
                    col_graf, col_datos = st.columns([0.4, 0.6])
                    
                    with col_graf:
                        st.write("**Distribución por Zona Trabajada**")
                        df_activos = df_ind[~condicion]
                        if not df_activos.empty and 'ZONA' in df_activos.columns:
                            zonas_count = df_activos['ZONA'].value_counts().reset_index()
                            zonas_count.columns = ['ZONA', 'Días']
                            fig = px.pie(zonas_count, values='Días', names='ZONA', hole=0.4, color_discrete_sequence=px.colors.sequential.Oranges_r)
                            fig.update_traces(textposition='inside', textinfo='percent+label')
                            fig.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0), height=300)
                            st.plotly_chart(fig, use_container_width=True)
                        else:
                            st.info("Sin registros de zona para esta selección.")

                    with col_datos:
                        st.write("**Historial Detallado**")
                        columnas_mostrar = ['FECHA', 'DIA', 'CHOFER', 'UNIDAD', 'RUTA', 'ZONA', 'OBSERVACIÓN']
                        cols_existentes = [c for c in columnas_mostrar if c in df_ind.columns]
                        df_view = df_ind[cols_existentes]
                        st.dataframe(df_view.style.set_table_styles(estilos_tabla_personal), use_container_width=True, hide_index=True)
                        
                        pdf_bytes = crear_pdf_operativo(ayu_sel, "Ayudante", df_view, total_dias, dias_activos, dias_inactivos, mes_seleccionado, texto_filtros_a)
                        st.download_button(label=f"📥 Descargar PDF Gerencial", data=pdf_bytes, file_name=f"Perfil_{ayu_sel.replace(' ', '_')}.pdf", mime="application/pdf", use_container_width=True)

    except Exception as e:
        st.error(f"Error cargando los datos de Personal: {e}")

# --- 8. CONTROL DE FLUJO Y NAVEGACIÓN ---
if not st.session_state.autenticado:
    pantalla_login()
else:
    st.markdown("""
    <style>
    [data-testid="stApp"] { background: #ffffff !important; color: #31333F !important; }
    [data-testid="stHeader"] { background-color: transparent !important; }
    </style>
    """, unsafe_allow_html=True)

    with st.sidebar:
        st.markdown(f"### 👤 Usuario:\n**{st.session_state.usuario_actual}**")
        st.divider()
        st.markdown("### 🗂️ Módulos del Sistema")
        menu_seleccionado = st.radio("", ["🚛 Control de Flota", "👥 Rotación de Personal"])
        st.divider()
        if st.button("🚪 Cerrar Sesión", use_container_width=True, type="primary"):
            st.session_state.autenticado = False
            st.session_state.usuario_actual = ""
            st.rerun()

    if menu_seleccionado == "🚛 Control de Flota":
        modulo_flota()
    elif menu_seleccionado == "👥 Rotación de Personal":
        modulo_personal()
