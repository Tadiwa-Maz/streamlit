import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from io import BytesIO

# ── Helper Functions ───────────────────────────────
def load_file(uploaded_file):
    try:
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file, dtype=object)
        else:
            df = pd.read_excel(uploaded_file, dtype=object)
        df = deduplicate_columns(df)
        df = convert_numeric_columns(df)
        return df
    except Exception as e:
        st.error(f"Error reading file: {e}")
        return None

def deduplicate_columns(df):
    cols = pd.Series(df.columns)
    for dup in df.columns[df.columns.duplicated(keep=False)]:
        dups = cols[cols == dup].index.tolist()
        for i, idx in enumerate(dups):
            if i > 0:
                cols[idx] = f"{dup}_{i}"
    df.columns = cols
    return df

def convert_numeric_columns(df):
    for col in df.columns:
        try:
            df[col] = pd.to_numeric(df[col], errors='ignore')
        except:
            pass
    return df

def flag_anomalies(df):
    flag_df = pd.DataFrame()
    numeric_cols = df.select_dtypes(include=np.number).columns.tolist()
    for col in numeric_cols:
        flag_df[col+"_flag"] = df[col].apply(lambda x: "Negative" if pd.to_numeric(x, errors='coerce') < 0 else "")
    return flag_df

def download_excel(df):
    buffer = BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer

# ── Streamlit App ───────────────────────────────
st.set_page_config(page_title="SA Payroll Insights", layout="wide")
st.title("Payroll Insights Dashboard – South Africa")

uploaded_file = st.file_uploader("Upload Payroll File", type=["xlsx","csv"])

if uploaded_file:
    df = load_file(uploaded_file)
    if df is not None:

        tab1, tab2, tab3, tab4 = st.tabs(["Overview", "Employee Search", "Analysis", "Flags/Download"])

        # ── TAB 1: OVERVIEW ─────────────
        with tab1:
            st.header("Dataset Overview")
            st.write(f"Rows: {df.shape[0]}, Columns: {df.shape[1]}")
            st.dataframe(df.head(10), use_container_width=True)

            st.subheader("Column Types")
            st.dataframe(pd.DataFrame(df.dtypes, columns=["Data Type"]), use_container_width=True)

            numeric_cols = df.select_dtypes(include=np.number).columns.tolist()
            if numeric_cols:
                st.subheader("Numeric Summary")
                st.dataframe(df[numeric_cols].describe().T, use_container_width=True)

                # KPIs
                total_pay = df[numeric_cols].sum(numeric_only=True).sum()
                negative_flags = (df[numeric_cols] < 0).sum().sum()
                st.metric("Total Payroll Amount", f"R {total_pay:,.2f}")
                st.metric("Negative Amount Flags", negative_flags)

        # ── TAB 2: EMPLOYEE SEARCH ─────────────
        with tab2:
            st.header("Search Employee")
            if "Employee Name" in df.columns:
                emp_name = st.text_input("Type Employee Name")
                if emp_name:
                    emp_df = df[df["Employee Name"].str.contains(emp_name, case=False, na=False)]
                    st.dataframe(emp_df, use_container_width=True)
            else:
                st.warning("No 'Employee Name' column found in dataset.")

        # ── TAB 3: ANALYSIS ─────────────
        with tab3:
            st.header("Payroll Analysis")
            numeric_cols = df.select_dtypes(include=np.number).columns.tolist()
            
            if numeric_cols:
                # Pie chart: earnings composition
                earnings_cols = [col for col in numeric_cols if "Pay" in col or "Allowance" in col or "Bonus" in col]
                if earnings_cols:
                    earnings_totals = df[earnings_cols].sum(numeric_only=True)
                    fig_pie = px.pie(
                        names=earnings_totals.index,
                        values=earnings_totals.values,
                        title="Composition of Total Earnings"
                    )
                    st.plotly_chart(fig_pie, use_container_width=True)

                # Pie chart: deductions composition
                deduction_cols = [col for col in numeric_cols if "Tax" in col or "UIF" in col or "Pension" in col or "Deduction" in col]
                if deduction_cols:
                    deduction_totals = df[deduction_cols].sum(numeric_only=True)
                    fig_pie_ded = px.pie(
                        names=deduction_totals.index,
                        values=deduction_totals.values,
                        title="Composition of Total Deductions"
                    )
                    st.plotly_chart(fig_pie_ded, use_container_width=True)

                # Histogram: gross pay distribution
                if "Gross Pay" in df.columns:
                    df["Gross Pay"] = pd.to_numeric(df["Gross Pay"], errors='coerce')
                    fig_hist = px.histogram(df, x="Gross Pay", nbins=50,
                                            title="Distribution of Gross Pay")
                    st.plotly_chart(fig_hist, use_container_width=True)

                # Histogram: net pay distribution
                if "Net Pay" in df.columns:
                    df["Net Pay"] = pd.to_numeric(df["Net Pay"], errors='coerce')
                    fig_hist_net = px.histogram(df, x="Net Pay", nbins=50,
                                                title="Distribution of Net Pay")
                    st.plotly_chart(fig_hist_net, use_container_width=True)

                # Pie chart: leave pay composition
                leave_cols = [col for col in numeric_cols if "Leave" in col or "BCEA" in col]
                if leave_cols:
                    leave_totals = df[leave_cols].sum(numeric_only=True)
                    fig_pie_leave = px.pie(
                        names=leave_totals.index,
                        values=leave_totals.values,
                        title="Composition of Leave Pay & Benefits"
                    )
                    st.plotly_chart(fig_pie_leave, use_container_width=True)

        # ── TAB 4: FLAGS & DOWNLOAD ─────────────
        with tab4:
            st.header("Anomalies / Flags")
            flag_df = flag_anomalies(df)
            st.dataframe(flag_df, use_container_width=True)

            st.subheader("Download Processed Data")
            st.download_button(
                label="Download Dataset with Flags",
                data=download_excel(pd.concat([df, flag_df], axis=1)),
                file_name="payroll_processed.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
