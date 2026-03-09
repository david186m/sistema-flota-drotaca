import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import plotly.express as px
import os

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

# Colores originales (Modo claro/corporativo para adentro)
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

# --- 4. PROCESAMIENTO DE DATOS (LÓGICA ORIGINAL + SEGURIDAD NUBE) ---
@st.cache_data(ttl=60)
def cargar_y_procesar_datos():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # 🔴 NUEVO BLOQUE DE SEGURIDAD PARA INTERNET / LOCAL 🔴
    try:
        # Intentamos leer desde la caja fuerte de Streamlit (Internet)
        credenciales_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(credenciales_dict, scope)
    except:
        # Si da error, usamos el archivo físico (Tu computadora local)
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
    df_marzo = df_validos[df_validos['FECHA_DT'] >= inicio_mes]
    recorrido_mes = df_marzo.groupby('UNIDAD').agg(km_min_mes=('KM_LIMPIO', 'min'), km_max_mes=('KM_LIMPIO', 'max')).reset_index()
    recorrido_mes['Km Mensual Actual'] = recorrido_mes['km_max_mes'] - recorrido_mes['km_min_mes']
    
    df_merge = pd.merge(df_control[['Placa', 'Grupo', 'RUTA']], df_ultimo[['UNIDAD', 'KM_LIMPIO', 'FECHA']], left_on="Placa", right_on="UNIDAD", how="left")
    df_merge2 = pd.merge(df_merge, recorrido_mes[['UNIDAD', 'Km Mensual Actual']], on="UNIDAD", how="left")
    
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
    df_final['Última Actualización'] = df_final['Última Actualización'].fillna("Sin Registro")
    
    columnas_orden = ['Placa', 'Modelo', 'Grupo', 'RUTA', 'Estatus_Unidad', 'Estatus GPS', 'Km Actual', 'Km Mensual Actual', 'Última Actualización', 'Observacion', 'Fecha_Inoperativo']
    
    return df_final[columnas_orden], df_taller, hora_sincronizacion

# --- 5. PANTALLA DE LOGIN (CON IMAGEN Y DISEÑO OSCURO) ---
def pantalla_login():
    st.markdown("""
    <style>
    [data-testid="stApp"] {
        background: radial-gradient(circle at center, #151b26 0%, #080a0e 100%) !important;
        color: #ffffff;
    }
    [data-testid="stHeader"] { background-color: transparent !important; }
    [data-testid="stForm"] {
        background-color: rgba(28, 34, 45, 0.8) !important;
        border-radius: 20px !important;
        border: 1px solid rgba(0, 212, 255, 0.2) !important;
        box-shadow: 0 15px 35px rgba(0, 0, 0, 0.5) !important;
        backdrop-filter: blur(10px) !important;
        padding: 40px 30px !important;
    }
    [data-testid="stForm"] div[data-baseweb="input"] {
        background-color: rgba(0, 0, 0, 0.3) !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        border-radius: 8px !important;
        transition: all 0.3s ease !important;
    }
    [data-testid="stForm"] div[data-baseweb="input"]:focus-within {
        border-color: #00d4ff !important;
        box-shadow: 0 0 8px rgba(0, 212, 255, 0.3) !important;
    }
    [data-testid="stForm"] input { color: white !important; }
    [data-testid="stForm"] label p { color: #a0a0a0 !important; font-size: 0.9rem !important; }
    [data-testid="stFormSubmitButton"] button {
        background: linear-gradient(45deg, #0056b3, #00d4ff) !important;
        border: none !important; border-radius: 8px !important; color: white !important;
        font-weight: bold !important; letter-spacing: 1px !important;
        transition: transform 0.2s, box-shadow 0.2s !important; width: 100% !important; margin-top: 10px !important;
    }
    [data-testid="stFormSubmitButton"] button:hover {
        transform: translateY(-2px) !important; box-shadow: 0 5px 15px rgba(0, 212, 255, 0.4) !important;
    }
    .login-title { color: #ffffff; font-size: 2.2rem; letter-spacing: 2px; text-align: center; margin-bottom: 5px; font-weight: 600; }
    .login-title span { color: #00d4ff; font-weight: 300; }
    .login-subtitle { color: #a0a0a0; font-size: 0.95rem; text-align: center; margin-bottom: 30px; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("<br><br>", unsafe_allow_html=True) 
    
    usuarios_permitidos = {
        "David_Admin": "Drotaca2026",
        "Supervisor_Oriente": "Oriente26",
        "Supervisor_Occidente": "Occidente26"
    }

    col1, col2, col3 = st.columns([1, 1.5, 1])
    
    with col2:
        nombre_imagen = "logo.png" 
        
        if os.path.exists(nombre_imagen):
            st.image(nombre_imagen, width=300)
            st.markdown("<br>", unsafe_allow_html=True)
        else:
            st.warning(f"⚠️ No se encontró el archivo '{nombre_imagen}' en la carpeta.")

        st.markdown("<div class='login-title'>🚛 Monitoreo de Flota <span>2026</span></div>", unsafe_allow_html=True)
        st.markdown("<div class='login-subtitle'>Acceso Restringido al Sistema</div><br>", unsafe_allow_html=True)

        with st.form("formulario_login"):
            usuario_input = st.text_input("👤 Usuario:")
            password_input = st.text_input("🔑 Contraseña:", type="password")
            boton_ingresar = st.form_submit_button("Ingresar al Sistema", use_container_width=True)
            
            if boton_ingresar:
                if usuario_input in usuarios_permitidos and usuarios_permitidos[usuario_input] == password_input:
                    print(f"[LOG OCULTO] ✅ {usuario_input} ha iniciado sesión. Hora: {obtener_hora_venezuela().strftime('%d/%m/%Y %I:%M:%S %p')}")
                    st.session_state.autenticado = True
                    st.session_state.usuario_actual = usuario_input
                    st.rerun() 
                else:
                    st.error("Usuario o contraseña incorrectos.")
                    print(f"[LOG OCULTO] ⚠️ Intento fallido de acceso. Usuario: '{usuario_input}'")


# --- 6. PANEL PRINCIPAL (BLANCO CORPORATIVO / LÓGICA ORIGINAL) ---
def pantalla_principal():
    st.markdown("""
    <style>
    [data-testid="stApp"] { background: #ffffff !important; color: #31333F !important; }
    [data-testid="stHeader"] { background-color: transparent !important; }
    </style>
    """, unsafe_allow_html=True)

    with st.sidebar:
        st.markdown(f"### 👤 Bienvenido(a):\n**{st.session_state.usuario_actual}**")
        st.divider()
        if st.button("🚪 Cerrar Sesión", use_container_width=True, type="primary"):
            st.session_state.autenticado = False
            st.session_state.usuario_actual = ""
            st.rerun()

    col_titulo, col_boton = st.columns([0.8, 0.2])
    with col_titulo:
        st.title("🚛 Panel de Control de Flota - Drotaca")
    with col_boton:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 Actualizar Datos", use_container_width=True):
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
            
            col_tabla1, col_tabla2 = st.columns([0.7, 0.3])
            with col_tabla1:
                st.subheader(f"📑 Reporte Detallado: {seleccion}")
            with col_tabla2:
                df_descarga = df_mostrar.copy()
                df_descarga['Km Actual'] = df_descarga['Km Actual'].round(2)
                df_descarga['Km Mensual Actual'] = df_descarga['Km Mensual Actual'].round(2)
                csv_data = df_descarga.to_csv(index=False, sep=';', decimal=',').encode('utf-8-sig')
                st.download_button(label="📥 Exportar a Excel", data=csv_data, file_name=f"Reporte_Flota_{seleccion}.csv", mime="text/csv", use_container_width=True)
            
            estilos_tabla = [
                dict(selector="th", props=[("background-color", "#1A3B5C"), ("color", "white"), ("text-align", "center"), ("font-weight", "bold"), ("font-size", "14px"), ("border", "1px solid white")]),
                dict(selector="td", props=[("text-align", "center"), ("border", "1px solid #e0e0e0"), ("font-size", "14px")]),
                dict(selector="tr:hover", props=[("background-color", "#f2f8ff")])
            ]

            tabla_formateada = df_mostrar.style.format({
                "Km Actual": "{:,.0f} Kms",       
                "Km Mensual Actual": "{:,.0f} Kms" 
            }).set_table_styles(estilos_tabla).map(color_gps, subset=['Estatus GPS']).map(color_estatus, subset=['Estatus_Unidad'])

            st.dataframe(tabla_formateada, use_container_width=True, hide_index=True)

        with tab2:
            st.subheader("🛠️ Registro Histórico de Mantenimiento")
            st.write("Esta tabla refleja todos los movimientos de entrada y salida del taller.")
            busqueda = st.text_input("🔍 Buscar por Placa (Ej. A0378AK):").upper()
            
            if not df_historial_taller.empty:
                df_mostrar_taller = df_historial_taller
                if busqueda:
                    df_mostrar_taller = df_mostrar_taller[df_mostrar_taller['Placa'].str.contains(busqueda, na=False)]
                columnas_estilo = ['Estatus_Reparacion', 'Duración'] if 'Duración' in df_mostrar_taller.columns else ['Estatus_Reparacion']
                st.dataframe(df_mostrar_taller.style.set_table_styles(estilos_tabla).map(color_taller, subset=columnas_estilo), use_container_width=True, hide_index=True)
            else:
                st.info("Aún no hay registros en la hoja de Historial de Taller.")

    except Exception as e:
        st.error(f"Error cargando los datos: {e}")

# --- 7. CONTROL DE FLUJO ---
if not st.session_state.autenticado:
    pantalla_login()
else:
    pantalla_principal()