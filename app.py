import streamlit as st
import pandas as pd
import pdfplumber
import plotly.express as px
import sqlite3
from pathlib import Path
import tempfile
import os

# Import local modules
# Import parsers explicitly
from parsers import (
    get_parser, 
    BBVADebitParser, 
    BBVACreditParser, 
    ScotiabankCreditParser, 
    ScotiabankDebitParser, 
    BanorteCreditParser,
    ScotiabankV2Parser  # Nuevo parser mejorado
)
from ai_parsers import OpenAIVisionParser, NemotronParser, LocalNemotronParser, GeminiVisionParser, AIBankParser


import database
from database import update_movement_classification


# Map names to classes
PARSERS_MAP = {
    "BBVA Débito": BBVADebitParser,
    "BBVA Crédito": BBVACreditParser,
    "Scotiabank Crédito": ScotiabankCreditParser,
    "Scotiabank Débito": ScotiabankDebitParser,
    "Scotiabank V2 (Mejorado)": ScotiabankV2Parser,  # Nuevo parser que detecta TDC/Checking automáticamente
    "Banorte Crédito": BanorteCreditParser,
    "Scotiabank - OpenAI Vision": OpenAIVisionParser,
    "Scotiabank - Gemini 1.5 Pro": GeminiVisionParser,
    "Scotiabank - Nvidia Nemotron (Cloud)": NemotronParser,
    "Scotiabank - Nvidia Nemotron (Local)": LocalNemotronParser
}


# Page Config
st.set_page_config(page_title="Gestor de Estados de Cuenta", layout="wide")

# Initialize DB
database.init_db()

def get_db_connection():
    conn = sqlite3.connect(database.DB_PATH)
    return conn

def load_data():
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT * FROM movements", conn)
    conn.close()
    
    # Enforce numeric types for meta columns to avoid PyArrow errors
    numeric_cols = ["meta_monto_original", "meta_saldo_pendiente", "monto", "saldo_calculado"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            
    return df
def process_pdf(uploaded_file, manual_parser_name=None):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_file.write(uploaded_file.getvalue())
        tmp_path = Path(tmp_file.name)
    
    try:
        # Extract text with x_tolerance=1 for better spacing
        texto = []
        with pdfplumber.open(tmp_path) as pdf:
            for p in pdf.pages:
                texto.append(p.extract_text(x_tolerance=1) or "")
        full_text = "\n".join(texto)
        
        # Detect parser or use manual
        parser_instance = None
        parser_name_detected = "Desconocido"
        
        if manual_parser_name and manual_parser_name != "Automático":
            parser_class = PARSERS_MAP.get(manual_parser_name)
            if parser_class:
                # Check for API keys if AI parser
                api_key = None
                if issubclass(parser_class, AIBankParser):
                    if parser_class == OpenAIVisionParser:
                        api_key = os.getenv("OPENAI_API_KEY")
                        if not api_key:
                            api_key = st.text_input("Ingresa tu OpenAI API Key", type="password")
                    elif parser_class == NemotronParser:
                        api_key = os.getenv("HUGGINGFACE_API_TOKEN")
                        if not api_key:
                            api_key = st.text_input("Ingresa tu Hugging Face API Token", type="password")

                    
                    if not api_key:
                        st.error("Se requiere API Key para usar este extractor.")
                        return False
                        
                    parser_instance = parser_class(full_text, pdf_path=tmp_path, api_key=api_key)
                else:
                    parser_instance = parser_class(full_text, pdf_path=tmp_path)
                
                parser_name_detected = manual_parser_name
        else:
            parser_instance = get_parser(full_text, pdf_path=tmp_path)
            if parser_instance:
                parser_name_detected = type(parser_instance).__name__

        if parser_instance:
            st.info(f"Usando parser: {parser_name_detected}")
            
            try:
                resultado = parser_instance.parse()
                account_number = resultado["account_number"]
                df_movements = resultado["movements"]
                
                # Validación para ScotiabankV2Parser, BanorteCreditParser y BBVA
                if isinstance(parser_instance, (ScotiabankV2Parser, BanorteCreditParser, BBVADebitParser, BBVACreditParser)):
                    meta = resultado.get("metadata", {})
                    if meta:
                        st.subheader(f"📊 Validación - {meta.get('account_type', 'Desconocido')}")
                        
                        # Mostrar header info
                        header = meta.get("header", {})
                        validation = meta.get("validation", {})
                        
                        if meta.get("account_type") == "CHECKING":
                            col1, col2, col3, col4 = st.columns(4)
                            col1.metric("Saldo Inicial", f"${header.get('saldo_inicial', 0):,.2f}")
                            col2.metric("Depósitos", f"${header.get('depositos', 0):,.2f}")
                            col3.metric("Retiros", f"${header.get('retiros', 0):,.2f}")
                            col4.metric("Saldo Final", f"${header.get('saldo_final', 0):,.2f}")
                        elif meta.get("account_type") == "TDC":
                            col1, col2, col3 = st.columns(3)
                            col1.metric("Período", header.get("periodo", "N/A"))
                            
                            # Unificar claves de diferentes parsers
                            cargos = header.get('resumen_cargos_total') or header.get('compras_cargos') or 0
                            abonos = header.get('resumen_pagos_abonos') or header.get('pagos_abonos') or 0
                            
                            col2.metric("Total Cargos", f"${cargos:,.2f}")
                            col3.metric("Pagos/Abonos", f"${abonos:,.2f}")
                        
                        # Mostrar validación
                        controles = validation.get("controles", {})
                        if controles:
                            all_ok = all(controles.values())
                            if all_ok:
                                st.success("✅ Validación Correcta: Todos los controles pasaron.")
                            else:
                                st.warning("⚠️ Validación con discrepancias:")
                                for ctrl, ok in controles.items():
                                    icon = "✅" if ok else "❌"
                                    st.write(f"  {icon} {ctrl}")
                            
                            with st.expander("Ver detalles de validación"):
                                st.json(validation)
                
                # Validación para AIBankParser (parsers con IA)
                elif isinstance(parser_instance, AIBankParser):
                    meta = resultado.get("metadata", {})
                    if meta:
                        st.subheader("Validación de Extracción (AI)")
                        col1, col2, col3, col4 = st.columns(4)
                        col1.metric("Saldo Anterior", f"${meta.get('saldo_anterior', 0):,.2f}")
                        col2.metric("Entradas", f"${meta.get('total_abonos', 0):,.2f}")
                        col3.metric("Salidas", f"${meta.get('total_cargos', 0):,.2f}")
                        col4.metric("Saldo Nuevo", f"${meta.get('saldo_nuevo', 0):,.2f}")
                        
                        # Run validation
                        try:
                            val_res = parser_instance.validate_balance(
                                df_movements.to_dict('records'),
                                float(meta.get('saldo_anterior') or 0),
                                float(meta.get('saldo_nuevo') or 0),
                                float(meta.get('total_abonos') or 0),
                                float(meta.get('total_cargos') or 0)
                            )

                            if val_res["valid"]:
                                st.success("✅ Validación Correcta: El balance coincide.")
                            else:
                                st.error("❌ Validación Fallida: Discrepancia en balance.")
                                st.write(f"Diferencia: ${val_res.get('diff', 0):,.2f}")
                                st.json(val_res)
                        except Exception as e:
                            st.warning(f"No se pudo validar automáticamente: {e}")
                            
                    # Display Informative Data
                    info_data = resultado.get("informative_data", [])
                    if info_data:
                        st.subheader("Información Adicional (Informativa)")
                        st.write("Se detectaron tablas informativas (ej. Saldo Pendiente):")
                        st.json(info_data)

                # Mostrar preview de movimientos
                st.subheader(f"📋 Preview: {len(df_movements)} movimientos encontrados")
                st.dataframe(df_movements.head(10))

                # Determine Bank Name
                bank_name = "Desconocido"
                p_name = type(parser_instance).__name__
                if "BBVA" in p_name:
                    bank_name = "BBVA"
                elif "Scotiabank" in p_name:
                    bank_name = "Scotiabank"
                elif "Banorte" in p_name:
                    bank_name = "Banorte"
                elif "OpenAI" in p_name or "Nemotron" in p_name:
                    bank_name = "Scotiabank"
                
                # Save to DB
                database.save_movements(df_movements, account_number, bank_name)
                st.success(f"✅ Procesado exitosamente! {len(df_movements)} movimientos guardados.")
                return True
                
            except Exception as e:
                st.error(f"Error parseando el archivo: {e}")
                import traceback
                with st.expander("Ver detalle del error"):
                    st.code(traceback.format_exc())
                return False
        else:

            st.warning("No se detectó un parser adecuado para este archivo.")
            with st.expander("Ver texto extraído (Debug)"):
                st.text(full_text[:1000])
            return False
            
    except Exception as e:
        st.error(f"Error leyendo el PDF: {e}")
        return False
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

# --- Sidebar ---
st.sidebar.title("Navegación")
page = st.sidebar.radio("Ir a", ["Cargar Archivos", "Dashboard", "Clasificación", "Proyección de Flujo"])

# --- Page: Cargar Archivos ---
if page == "Cargar Archivos":
    st.title("📂 Cargar Estados de Cuenta")
    
    uploaded_files = st.file_uploader("Sube tus PDFs aquí", type=["pdf"], accept_multiple_files=True)
    
    # Manual override option
    parser_options = ["Automático"] + list(PARSERS_MAP.keys())
    selected_parser = st.selectbox("Seleccionar Banco/Tipo (Opcional)", parser_options)
    
    if st.button("Procesar Archivos"):
        if uploaded_files:
            for uploaded_file in uploaded_files:
                st.write(f"--- Procesando: **{uploaded_file.name}** ---")
                if process_pdf(uploaded_file, manual_parser_name=selected_parser):
                    st.balloons()
        else:
            st.warning("Por favor sube al menos un archivo.")

# --- Page: Dashboard ---
elif page == "Dashboard":
    st.title("📊 Dashboard de Gastos")
    
    df = load_data()
    
    if df.empty:
        st.info("No hay datos cargados aún.")
    else:
        # Convert dates
        # Try multiple formats if needed, but parsers usually output DD/MMM or DD-MMM-YYYY
        # For now assume text, maybe clean up later.
        
        st.dataframe(df)
        
        # Simple Metrics
        total_records = len(df)
        unique_accounts = df["account_number"].nunique()
        st.metric("Total Movimientos", total_records)
        st.metric("Cuentas Únicas", unique_accounts)
        
        # Charts
        if "monto" in df.columns and "tipo" in df.columns:
            fig = px.histogram(df, x="tipo", y="monto", color="bank", title="Distribución por Tipo y Banco")
            st.plotly_chart(fig)

# --- Page: Clasificación ---
elif page == "Clasificación":
    st.title("🏷️ Clasificación de Movimientos")
    
    df = load_data()
    
    if df.empty:
        st.info("No hay datos para clasificar.")
    else:
        # Filters
        col1, col2 = st.columns(2)
        with col1:
            search_term = st.text_input("Buscar en descripción")
        with col2:
            filter_type = st.selectbox("Filtrar por Tipo", ["Todos", "Cargo", "Abono"])
            
        # Apply filters
        filtered_df = df.copy()
        if search_term:
            filtered_df = filtered_df[filtered_df["descripcion"].str.contains(search_term, case=False, na=False)]
        if filter_type != "Todos":
            filtered_df = filtered_df[filtered_df["tipo"] == filter_type]
            
        # Display editable table (if st.data_editor is available, otherwise use checkboxes)
        # We'll use a simple approach: Select rows -> Apply Classification
        
        st.subheader("Movimientos")
        
        # Add a selection column? Streamlit's data_editor is best for this.
        # Let's try to use data_editor if possible, or just a list with checkboxes.
        # For bulk actions, a multiselect of IDs might be hard.
        # Let's use data_editor to edit the 'user_classification' column directly.
        
        if "recurrence_period" not in filtered_df.columns:
            filtered_df["recurrence_period"] = None
            
        classification_options = [None, "Gasto Fijo", "Ingreso Fijo", "Gasto Variable", "Ingreso Variable", "Ignorar"]
        period_options = [None, "Mensual", "Bimestral", "Trimestral", "Semestral", "Anual"]
        
        edited_df = st.data_editor(
            filtered_df[["id", "fecha_oper", "descripcion", "monto", "tipo", "user_classification", "recurrence_period"]],
            column_config={
                "user_classification": st.column_config.SelectboxColumn(
                    "Clasificación",
                    help="Selecciona el tipo de movimiento",
                    width="medium",
                    options=classification_options,
                ),
                "recurrence_period": st.column_config.SelectboxColumn(
                    "Periodo",
                    help="Frecuencia del gasto/ingreso",
                    width="medium",
                    options=period_options,
                ),
                "id": st.column_config.NumberColumn("ID", disabled=True),
                "fecha_oper": st.column_config.TextColumn("Fecha", disabled=True),
                "descripcion": st.column_config.TextColumn("Descripción", disabled=True),
                "monto": st.column_config.NumberColumn("Monto", disabled=True),
                "tipo": st.column_config.TextColumn("Tipo", disabled=True),
            },
            hide_index=True,
            key="classification_editor"
        )
        
        if st.button("Guardar Cambios"):
            count = 0
            progress_bar = st.progress(0)
            total_rows = len(edited_df)
            
            for index, row in edited_df.iterrows():
                update_movement_classification(row["id"], row["user_classification"], row["recurrence_period"])
                count += 1
                if total_rows > 0:
                    progress_bar.progress(count / total_rows)
                
            st.success(f"Se actualizaron {count} movimientos.")
            st.rerun()

# --- Page: Proyección de Flujo ---
elif page == "Proyección de Flujo":
    st.title("🔮 Proyección de Flujo de Efectivo")
    
    df = load_data()
    
    if df.empty:
        st.info("No hay datos.")
    else:
        # 1. Calculate Fixed Expenses and Income
        # Logic: Group by description for items marked as Fixed, take the LATEST amount.
        
        if "user_classification" not in df.columns:
            df["user_classification"] = None
        if "recurrence_period" not in df.columns:
            df["recurrence_period"] = None
            
        fixed_expenses_df = df[df["user_classification"] == "Gasto Fijo"].copy()
        fixed_income_df = df[df["user_classification"] == "Ingreso Fijo"].copy()
        
        # Helper to get unique items with their period
        def get_projected_items(sub_df):
            if sub_df.empty:
                return []
            
            # Get latest occurrence for amount and period
            unique_items = sub_df.drop_duplicates(subset=["descripcion"], keep="last")
            
            items = []
            for _, row in unique_items.iterrows():
                items.append({
                    "descripcion": row["descripcion"],
                    "monto": row["monto"],
                    "periodo": row["recurrence_period"] if row["recurrence_period"] else "Mensual", # Default to Monthly
                    "last_date": row["fecha_oper"] # We need this for non-monthly
                })
            return items
            
        expense_items = get_projected_items(fixed_expenses_df)
        income_items = get_projected_items(fixed_income_df)
        
        # Calculate monthly average for summary (approximate)
        def calculate_monthly_avg(items):
            total = 0.0
            for item in items:
                monto = item["monto"]
                period = item["periodo"]
                if period == "Mensual":
                    total += monto
                elif period == "Bimestral":
                    total += monto / 2
                elif period == "Trimestral":
                    total += monto / 3
                elif period == "Semestral":
                    total += monto / 6
                elif period == "Anual":
                    total += monto / 12
            return total

        avg_monthly_expenses = calculate_monthly_avg(expense_items)
        avg_monthly_income = calculate_monthly_avg(income_items)
        
        st.subheader("Resumen Mensual Promedio (Estimado)")
        col1, col2, col3 = st.columns(3)
        col1.metric("Ingresos Fijos Prom.", f"${avg_monthly_income:,.2f}")
        col2.metric("Gastos Fijos Prom.", f"${avg_monthly_expenses:,.2f}")
        col3.metric("Neto Fijo Prom.", f"${avg_monthly_income - avg_monthly_expenses:,.2f}")
        
        with st.expander("Ver Detalles de Fijos"):
            c1, c2 = st.columns(2)
            with c1:
                st.write("**Gastos Fijos Detectados**")
                if expense_items:
                    st.dataframe(pd.DataFrame(expense_items)[["descripcion", "monto", "periodo"]])
                else:
                    st.info("No hay gastos fijos marcados.")
            with c2:
                st.write("**Ingresos Fijos Detectados**")
                if income_items:
                    st.dataframe(pd.DataFrame(income_items)[["descripcion", "monto", "periodo"]])
                else:
                    st.info("No hay ingresos fijos marcados.")

        # 2. Calculate MSI Projections
        msi_projections = {} # Month Offset -> Amount
        
        if "categoria" in df.columns:
            msi_df = df[df["categoria"] == "MSI"].copy()
            
            if not msi_df.empty:
                for _, row in msi_df.iterrows():
                    desc = row["descripcion"]
                    monto = row["monto"]
                    
                    # Parse "X de Y"
                    import re
                    m = re.search(r"(\d+)\s*(?:de|/)\s*(\d+)", desc)
                    
                    if m:
                        current_payment_num = int(m.group(1))
                        total_payments = int(m.group(2))
                        remaining_payments = total_payments - current_payment_num
                        
                        # Add to future months (1 to remaining)
                        for i in range(1, remaining_payments + 1):
                            msi_projections[i] = msi_projections.get(i, 0) + monto

        # 3. Combine for Future Projection (Next 12 Months)
        projection_data = []
        
        # Helper to check if item applies to month i (1-indexed from now)
        def get_period_amount(items, month_offset):
            total = 0.0
            for item in items:
                period = item["periodo"]
                monto = item["monto"]
                
                # Logic:
                # Mensual: Always applies.
                # Bimestral: Applies if month_offset % 2 == 0? 
                #   Ideally we check date. But for simplicity, let's assume it applies every N months starting from... now?
                #   Or starting from last date?
                #   Let's assume "Bimestral" means it happens in Month 2, 4, 6... relative to start of year?
                #   Or relative to the last payment?
                #   Let's use a simple modulo logic for now:
                #   If Bimestral, applies every 2 months. Let's assume it applies in month 1, 3, 5... or 2, 4, 6...
                #   Without a specific "Next Due Date", it's hard.
                #   Let's assume it applies in Month + period_months.
                #   Actually, let's just assume it applies if (month_offset % period_map[period]) == 0?
                #   That means if I pay now (Month 0), I pay again in Month 2.
                
                period_map = {
                    "Mensual": 1,
                    "Bimestral": 2,
                    "Trimestral": 3,
                    "Semestral": 6,
                    "Anual": 12
                }
                
                interval = period_map.get(period, 1)
                
                # We assume the expense happens every 'interval' months.
                # We assume the cycle starts at Month 1? Or Month 'interval'?
                # Let's assume it happens at Month 'interval', '2*interval', etc.
                # This is a simplification.
                
                if month_offset % interval == 0:
                    total += monto
                    
            return total

        for i in range(1, 13): # Next 12 months
            msi_amount = msi_projections.get(i, 0)
            
            fixed_income_this_month = get_period_amount(income_items, i)
            fixed_expenses_this_month = get_period_amount(expense_items, i)
            
            # Net Flow
            net_flow = fixed_income_this_month - fixed_expenses_this_month - msi_amount
            
            projection_data.append({
                "Mes Futuro": f"+{i}",
                "Ingresos Fijos": fixed_income_this_month,
                "Gastos Fijos": fixed_expenses_this_month,
                "Pagos MSI": msi_amount,
                "Flujo Neto": net_flow
            })
            
        proj_df = pd.DataFrame(projection_data)
        
        st.subheader("Proyección de Flujo a 12 Meses")
        st.dataframe(proj_df)
        
        # Chart
        # Stacked Bar for Expenses (Fixed + MSI) vs Income?
        # Or just Net Flow line?
        
        # Let's do a composed chart
        import plotly.graph_objects as go
        
        fig = go.Figure()
        
        # Income Bar
        fig.add_trace(go.Bar(
            x=proj_df["Mes Futuro"],
            y=proj_df["Ingresos Fijos"],
            name="Ingresos Fijos",
            marker_color="green"
        ))
        
        # Expenses Bar (Stacked?)
        # We can stack Fixed Expenses and MSI
        fig.add_trace(go.Bar(
            x=proj_df["Mes Futuro"],
            y=proj_df["Gastos Fijos"],
            name="Gastos Fijos",
            marker_color="red"
        ))
        
        fig.add_trace(go.Bar(
            x=proj_df["Mes Futuro"],
            y=proj_df["Pagos MSI"],
            name="Pagos MSI",
            marker_color="orange"
        ))
        
        # Net Flow Line
        fig.add_trace(go.Scatter(
            x=proj_df["Mes Futuro"],
            y=proj_df["Flujo Neto"],
            name="Flujo Neto",
            mode="lines+markers",
            line=dict(color="blue", width=3)
        ))
        
        fig.update_layout(
            title="Proyección de Ingresos vs Gastos",
            barmode="stack", # This stacks all bars. Wait, we want Income separate from Expenses.
            # Grouped stack is hard in simple plotly express.
            # Let's just stack Expenses (Fixed + MSI) and show Income as a separate bar or line?
            # Or just show Net Flow.
            
            # Alternative:
            # Positive bars: Income
            # Negative bars: Expenses
            # Line: Net
        )
        
        # Let's try "group" mode but that groups every trace.
        # We want (Income) vs (Fixed Exp + MSI).
        # That requires custom data prep.
        
        # Simplified Chart: Total Expenses vs Total Income
        proj_df["Total Gastos"] = proj_df["Gastos Fijos"] + proj_df["Pagos MSI"]
        
        fig2 = go.Figure()
        fig2.add_trace(go.Bar(x=proj_df["Mes Futuro"], y=proj_df["Ingresos Fijos"], name="Ingresos", marker_color="green"))
        fig2.add_trace(go.Bar(x=proj_df["Mes Futuro"], y=proj_df["Total Gastos"], name="Gastos Totales", marker_color="red"))
        fig2.add_trace(go.Scatter(x=proj_df["Mes Futuro"], y=proj_df["Flujo Neto"], name="Neto", line=dict(color="blue")))
        
        fig2.update_layout(title="Flujo de Efectivo Proyectado", barmode="group")
        
        st.plotly_chart(fig2)

