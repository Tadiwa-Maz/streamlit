import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from io import BytesIO

# ── Helper Functions ─────────────────────────────────────────────
def load_file(uploaded_file):
    """Load Excel or CSV file safely with all data types."""
    try:
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file, dtype=object)  # Read everything as object
        else:
            df = pd.read_excel(uploaded_file, dtype=object)
        df = deduplicate_columns(df)
        return df
    except Exception as e:
        st.error(f"Error reading file: {e}")
        return None

def deduplicate_columns(df):
    """Ensure all column names are unique by appending _1, _2, etc."""
    cols = pd.Series(df.columns)
    for dup in df.columns[df.columns.duplicated(keep=False)]:
        dups = cols[cols == dup].index.tolist()
        for i, idx in enumerate(dups):
            if i > 0:
                cols[idx] = f"{dup}_{i}"
    df.columns = cols
    return df

def convert_all_to_string(df):
    """Convert all columns to string for safe display/export."""
    return df.astype(str)

def download_excel(df):
    """Return an Excel file buffer for download."""
    buffer = BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer

# ── Streamlit App ─────────────────────────────────────────────
st.title("Payroll Insights Dashboard")

uploaded_file = st.file_uploader("Upload your Excel or CSV file", type=["xlsx", "csv"])

if uploaded_file:
    df = load_file(uploaded_file)
    if df is not None:
        st.success("File loaded successfully!")

        # ── TAB 1: Raw Data ─────────────────────────────
        tab1, tab2, tab3 = st.tabs(["Raw Data", "Flags", "Analysis"])
        
        with tab1:
            st.subheader("Raw Data")
            st.dataframe(df, width='stretch')

        # ── TAB 2: Flags / Anomalies ────────────────────
        with tab2:
            st.subheader("Flags / Anomalies")
            # Example: flag numeric columns that are empty or negative
            numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            flag_df = df.copy()
            for col in numeric_cols:
                flag_df[f"{col}_flag"] = df[col].apply(lambda x: "Check" if pd.isna(x) or x < 0 else "")
            st.dataframe(flag_df, width='stretch')

            # Download flagged data
            st.download_button(
                label="Download Flags as Excel",
                data=download_excel(flag_df),
                file_name="flags.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        # ── TAB 3: Analysis ─────────────────────────────
        with tab3:
            st.subheader("Analysis")
            # Example: simple scatter plot for first two numeric columns
            if len(numeric_cols) >= 2:
                x_col, y_col = numeric_cols[:2]
                fig = px.scatter(df, x=x_col, y=y_col, title=f"{y_col} vs {x_col}")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Not enough numeric columns for scatter plot.")

    else:
        st.error("Failed to load file.")
else:
    st.info("Please upload a file to get started.")
