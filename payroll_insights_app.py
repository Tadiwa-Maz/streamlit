import streamlit as st
import pandas as pd
import plotly.express as px

# ── UTILITY FUNCTIONS ──────────────────────────────────────────────

def make_unique(cols):
    """Ensure column names are unique"""
    seen = {}
    result = []
    for c in cols:
        if c not in seen:
            seen[c] = 0
            result.append(c)
        else:
            seen[c] += 1
            result.append(f"{c}_{seen[c]}")
    return result

def parse_excel_all_sheets(file):
    """Read all Excel sheets, deduplicate columns, preserve all data types"""
    xls = pd.ExcelFile(file)
    all_sheets = {}
    for sheet_name in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet_name)
        df.columns = df.columns.astype(str).str.strip()
        df.columns = make_unique(df.columns)

        # Convert complex objects to string but keep numbers/datetimes intact
        for col in df.columns:
            if df[col].dtype == 'object':
                df[col] = df[col].apply(
                    lambda x: x if pd.isna(x) else str(x) if isinstance(x, (list, dict, set)) else x
                )

        all_sheets[sheet_name] = df
    return all_sheets

def flag_anomalies(df):
    """Simple anomaly flags for demonstration"""
    flag_df = pd.DataFrame(index=df.index)
    for col in df.select_dtypes(include='number').columns:
        mean = df[col].mean()
        std = df[col].std()
        flag_df[col + "_flag"] = df[col].apply(lambda x: "⚠️" if abs(x - mean) > 3*std else "")
    return flag_df

# ── STREAMLIT APP ─────────────────────────────────────────────────

st.set_page_config(page_title="Payroll Insights", layout="wide")

st.title("Payroll Insights App")

uploaded_file = st.file_uploader("Upload your Excel file", type=["xlsx", "xls"])

if uploaded_file:
    # Parse Excel sheets
    all_sheets = parse_excel_all_sheets(uploaded_file)

    # TAB LAYOUT
    tab1, tab2, tab3 = st.tabs(["Data Preview", "Flags", "Analysis"])

    # ── TAB 1: DATA PREVIEW ─────────────────────────────────────────
    with tab1:
        st.header("Data Preview")
        sheet_names = list(all_sheets.keys())
        selected_sheet = st.selectbox("Select Sheet", sheet_names)
        df = all_sheets[selected_sheet]
        st.dataframe(df, width='stretch')

        # Download button for sheet
        st.download_button(
            label="Download this sheet as CSV",
            data=df.to_csv(index=False).encode('utf-8'),
            file_name=f"{selected_sheet}.csv",
            mime="text/csv"
        )

    # ── TAB 2: FLAGS ────────────────────────────────────────────────
    with tab2:
        st.header("Anomaly Flags")
        flag_df = flag_anomalies(df)
        st.dataframe(flag_df, width='stretch')

        st.download_button(
            label="Download Flags as CSV",
            data=flag_df.to_csv(index=False).encode('utf-8'),
            file_name=f"{selected_sheet}_flags.csv",
            mime="text/csv"
        )

    # ── TAB 3: ANALYSIS ─────────────────────────────────────────────
    with tab3:
        st.header("Analysis")
        numeric_cols = df.select_dtypes(include='number').columns.tolist()
        if numeric_cols:
            col_x = st.selectbox("X-axis column", numeric_cols, index=0)
            col_y = st.selectbox("Y-axis column", numeric_cols, index=min(1,len(numeric_cols)-1))

            fig = px.scatter(df, x=col_x, y=col_y, title=f"{col_y} vs {col_x}")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No numeric columns available for scatter plot.")
