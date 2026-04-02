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

        tab1, tab2, tab3, tab4 = st.tabs(["Overview", "Employee Insights", "Analysis", "Flags/Download"])

        # ── TAB 1: OVERVIEW ─────────────
        with tab1:
            st.header("Overall Payroll Overview")
            st.write(f"Rows: {df.shape[0]}, Columns: {df.shape[1]}")

            numeric_cols = df.select_dtypes(include=np.number).columns.tolist()
            if numeric_cols:
                total_pay = df[numeric_cols].sum(numeric_only=True).sum()
                negative_flags = (df[numeric_cols] < 0).sum().sum()
                st.metric("Total Payroll Amount", f"R {total_pay:,.2f}")
                st.metric("Negative Amount Flags", negative_flags)

        # ── TAB 2: EMPLOYEE INSIGHTS ─────────────
        with tab2:
            st.header("Search Employee")
            if "Employee Name" in df.columns:
                emp_name = st.text_input("Type Employee Name")
                if emp_name:
                    emp_df = df[df["Employee Name"].str.contains(emp_name, case=False, na=False)]
                    if not emp_df.empty:
                        st.subheader(f"Insights for {emp_name}")
                        numeric_cols = emp_df.select_dtypes(include=np.number).columns.tolist()
                        
                        for col in numeric_cols:
                            value = emp_df[col].sum(numeric_only=True)
                            st.metric(col, f"R {value:,.2f}")

                        # Pie charts: earnings, deductions, leave
                        earnings_cols = [c for c in numeric_cols if "Pay" in c or "Bonus" in c or "Allowance" in c]
                        if earnings_cols:
                            earnings_totals = emp_df[earnings_cols].sum(numeric_only=True)
                            fig_pie = px.pie(
                                names=earnings_totals.index,
                                values=earnings_totals.values,
                                title="Employee Earnings Composition"
                            )
                            st.plotly_chart(fig_pie, use_container_width=True)

                        deduction_cols = [c for c in numeric_cols if "Tax" in c or "UIF" in c or "Pension" in c or "Deduction" in c]
                        if deduction_cols:
                            deduction_totals = emp_df[deduction_cols].sum(numeric_only=True)
                            fig_ded = px.pie(
                                names=deduction_totals.index,
                                values=deduction_totals.values,
                                title="Employee Deductions Composition"
                            )
                            st.plotly_chart(fig_ded, use_container_width=True)

                        leave_cols = [c for c in numeric_cols if "Leave" in c or "BCEA" in c]
                        if leave_cols:
                            leave_totals = emp_df[leave_cols].sum(numeric_only=True)
                            fig_leave = px.pie(
                                names=leave_totals.index,
                                values=leave_totals.values,
                                title="Employee Leave & Benefits"
                            )
                            st.plotly_chart(fig_leave, use_container_width=True)
                    else:
                        st.warning("No employee found with that name")
            else:
                st.warning("No 'Employee Name' column found in dataset.")

        # ── TAB 3: ANALYSIS ─────────────
        with tab3:
            st.header("Payroll Analysis")
            numeric_cols = df.select_dtypes(include=np.number).columns.tolist()
            
            if numeric_cols:
                # Earnings
                earnings_cols = [c for c in numeric_cols if "Pay" in c or "Bonus" in c or "Allowance" in c]
                if earnings_cols:
                    earnings_totals = df[earnings_cols].sum(numeric_only=True)
                    fig_pie = px.pie(
                        names=earnings_totals.index,
                        values=earnings_totals.values,
                        title="Earnings Composition"
                    )
                    st.plotly_chart(fig_pie, use_container_width=True)

                # Deductions
                deduction_cols = [c for c in numeric_cols if "Tax" in c or "UIF" in c or "Pension" in c or "Deduction" in c]
                if deduction_cols:
                    deduction_totals = df[deduction_cols].sum(numeric_only=True)
                    fig_pie_ded = px.pie(
                        names=deduction_totals.index,
                        values=deduction_totals.values,
                        title="Deductions Composition"
                    )
                    st.plotly_chart(fig_pie_ded, use_container_width=True)

                # Gross Pay distribution
                if "Gross Pay" in df.columns:
                    df["Gross Pay"] = pd.to_numeric(df["Gross Pay"], errors='coerce')
                    fig_hist = px.histogram(df, x="Gross Pay", nbins=50, title="Gross Pay Distribution")
                    st.plotly_chart(fig_hist, use_container_width=True)

                # Net Pay distribution
                if "Net Pay" in df.columns:
                    df["Net Pay"] = pd.to_numeric(df["Net Pay"], errors='coerce')
                    fig_hist_net = px.histogram(df, x="Net Pay", nbins=50, title="Net Pay Distribution")
                    st.plotly_chart(fig_hist_net, use_container_width=True)

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
