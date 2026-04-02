import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import io

st.set_page_config(page_title="Payroll Insights", page_icon="💼", layout="wide")

# ── SMART PARSER ─────────────────────────────────────────────────────────────
def detect_header_row(raw):
    for i in range(min(20, len(raw))):
        row = raw.iloc[i].astype(str).str.lower().tolist()
        if "employee" in " ".join(row):
            return i
    return 0

COLUMN_MAP = {
    "Employee Name": ["employee name","name"],
    "Employee Code": ["employee code","emp code","id"],
    "Total Earnings": ["total earnings","earnings","gross"],
    "Total Deductions": ["total deductions","deductions"],
    "Net Pay": ["net pay","net salary","take home"],
    "Basic Salary": ["basic salary","salary"],
    "Pay as you Earn": ["paye","tax"],
    "Unemployment Insurance Fund": ["uif","unemployment"],
    "Pension": ["pension"],
}

def auto_map_columns(df):
    df_copy = df.copy()
    for col in df.columns:
        col_clean = str(col).lower().strip()
        for target, options in COLUMN_MAP.items():
            if any(opt in col_clean for opt in options):
                if target not in df_copy.columns:
                    df_copy[target] = df[col]
    return df_copy

def parse_excel_all_sheets(file):
    xls = pd.ExcelFile(file)
    dfs = []
    for sheet in xls.sheet_names:
        raw = pd.read_excel(xls, sheet_name=sheet, header=None)
        header_row = detect_header_row(raw)
        headers = raw.iloc[header_row]
        df = raw.iloc[header_row+1:].copy()
        df.columns = headers
        df = df.dropna(how="all")

        # Deduplicate columns
        cols = pd.Series(df.columns)
        for dup in cols[cols.duplicated()].unique():
            idx = cols[cols == dup].index.tolist()
            for i, j in enumerate(idx):
                if i != 0:
                    cols[j] = f"{dup}_{i}"
        df.columns = cols
        df.columns = df.columns.astype(str).str.strip().str.replace(r"\.\d+$", "", regex=True)
        df = auto_map_columns(df)
        dfs.append(df)

    return pd.concat(dfs, ignore_index=True)

# ── FLAGS ENGINE ─────────────────────────────────────────────────────────────
def run_flags(df):
    flags = []
    for _, r in df.iterrows():
        name = r.get("Employee Name", "Unknown")
        code = r.get("Employee Code", "")

        def to_float(x): 
            try: return float(x)
            except: return 0

        earn = to_float(r.get("Total Earnings", 0))
        ded  = to_float(r.get("Total Deductions", 0))
        net  = to_float(r.get("Net Pay", 0))
        basic = to_float(r.get("Basic Salary", 0))
        paye = to_float(r.get("Pay as you Earn", 0))
        uif  = to_float(r.get("Unemployment Insurance Fund", 0))
        pension = to_float(r.get("Pension", 0))

        # ── CRITICAL
        if net < 0:
            flags.append(("Critical", name, code, "Negative net pay"))
        if earn > 0 and ded > earn * 0.8:
            flags.append(("Critical", name, code, "Deductions exceed 80% of earnings"))
        if basic > 0 and paye == 0:
            flags.append(("Critical", name, code, "No PAYE on salaried employee"))

        # ── WARNING
        if earn > 0 and uif == 0:
            flags.append(("Warning", name, code, "No UIF deduction"))
        if basic > 0 and pension == 0:
            flags.append(("Warning", name, code, "No pension contribution"))
        if basic > 0 and earn > basic * 2:
            flags.append(("Warning", name, code, "Earnings unusually high vs salary"))
        if ded > 0 and earn > 0 and (ded / earn) > 0.5:
            flags.append(("Warning", name, code, "Deductions exceed 50% of earnings"))

        # ── INFO
        if earn > 0 and basic > 0 and abs(earn - basic) < 1:
            flags.append(("Info", name, code, "No variable earnings detected"))
        if earn == 0 and net == 0:
            flags.append(("Info", name, code, "Zero earnings and net pay"))

    flag_df = pd.DataFrame(flags, columns=["Level","Employee","Code","Issue"])
    severity_map = {"Critical":3, "Warning":2, "Info":1}
    flag_df["Severity"] = flag_df["Level"].map(severity_map)
    return flag_df

# ── UI ───────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("Payroll Insights")
    uploaded = st.file_uploader("Upload payroll Excel", type=["xlsx","xls"])

if uploaded is None:
    st.title("Upload a payroll file")
    st.stop()

try:
    df = parse_excel_all_sheets(uploaded)
except Exception as e:
    st.error(f"Error reading file: {e}")
    st.stop()

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Overview",
    "🚩 Flags & Anomalies",
    "📈 Analysis",
    "👤 Employee Drilldown",
    "📋 Full Data",
    "📥 Exports"
])

# ── TAB 1: OVERVIEW
with tab1:
    st.title("Overview")
    if all(c in df.columns for c in ["Total Earnings","Total Deductions","Net Pay"]):
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Employees", len(df))
        c2.metric("Total Earnings", f"R {df['Total Earnings'].sum():,.0f}")
        c3.metric("Total Deductions", f"R {df['Total Deductions'].sum():,.0f}")
        c4.metric("Net Pay", f"R {df['Net Pay'].sum():,.0f}")

# ── TAB 2: FLAGS
with tab2:
    st.title("Flags & Anomalies")
    flag_df = run_flags(df)
    if flag_df.empty:
        st.success("No issues detected")
    else:
        st.dataframe(flag_df, use_container_width=True)

# ── TAB 3: ANALYSIS
with tab3:
    st.title("Analysis")

    # Earnings by category
    earnings_cols = [c for c in df.columns if "earn" in c.lower() and c not in ["Total Earnings"]]
    if earnings_cols:
        earnings_sum = df[earnings_cols].sum()
        fig_earn = go.Figure(data=[go.Pie(labels=earnings_sum.index, values=earnings_sum.values, hole=0.4)])
        fig_earn.update_layout(title="Earnings by Category")
        st.plotly_chart(fig_earn, use_container_width=True)

    # Deduction composition per employee
    ded_cols = ["Pay as you Earn","Unemployment Insurance Fund","Pension"]
    ded_present = [c for c in ded_cols if c in df.columns]
    search_emp = st.selectbox("Select Employee for Deduction Composition", [""] + df["Employee Name"].dropna().unique().tolist())
    if search_emp and ded_present:
        emp_row = df[df["Employee Name"]==search_emp].iloc[0]
        ded_values = [float(emp_row.get(c,0)) for c in ded_present]
        fig_ded = go.Figure(data=[go.Pie(labels=ded_present, values=ded_values, hole=0.4)])
        fig_ded.update_layout(title=f"Deductions for {search_emp}")
        st.plotly_chart(fig_ded, use_container_width=True)

    # Highlight negative net pay & unusually high deductions
    if "Net Pay" in df.columns:
        df["Alert"] = "Normal"
        df.loc[df["Net Pay"]<0, "Alert"] = "Negative Net Pay"
        df.loc[(df["Total Deductions"]/df["Total Earnings"]>0.5), "Alert"] = "High Deductions"
        fig_alert = px.scatter(df, x="Total Earnings", y="Net Pay", color="Alert",
                               hover_name="Employee Name", color_discrete_map={
                                   "Normal":"green","Negative Net Pay":"red","High Deductions":"orange"})
        fig_alert.update_layout(title="Net Pay vs Total Earnings (Alerts Highlighted)")
        st.plotly_chart(fig_alert, use_container_width=True)

# ── TAB 4: EMPLOYEE DRILLDOWN
with tab4:
    st.title("Employee Drilldown")
    search = st.text_input("Search by Employee Name or Code")
    filtered = df.copy()
    if search:
        filtered = df[df.astype(str).apply(lambda row: row.str.contains(search, case=False).any(), axis=1)]

    st.caption(f"{len(filtered)} results found")
    if len(filtered) == 1:
        r = filtered.iloc[0]
        st.subheader(f"👤 {r.get('Employee Name','')} ({r.get('Employee Code','')})")
        c1,c2,c3 = st.columns(3)
        with c1:
            st.metric("Total Earnings", f"R {r.get('Total Earnings',0):,.0f}")
            st.metric("Basic Salary", f"R {r.get('Basic Salary',0):,.0f}")
        with c2:
            st.metric("Total Deductions", f"R {r.get('Total Deductions',0):,.0f}")
            st.metric("PAYE", f"R {r.get('Pay as you Earn',0):,.0f}")
        with c3:
            st.metric("Net Pay", f"R {r.get('Net Pay',0):,.0f}")

        st.subheader("Flags for Employee")
        emp_flags = run_flags(df)
        emp_flags = emp_flags[emp_flags["Employee"] == r.get("Employee Name")]
        if emp_flags.empty:
            st.success("No flags for this employee")
        else:
            st.dataframe(emp_flags, use_container_width=True)

        st.subheader("Full Breakdown")
        breakdown = r.dropna()
        breakdown_df = pd.DataFrame(breakdown).reset_index()
        breakdown_df.columns = ["Item","Value"]
        st.dataframe(breakdown_df, use_container_width=True)
    else:
        st.dataframe(filtered, use_container_width=True)

# ── TAB 5: FULL DATA
with tab5:
    st.title("Full Dataset")
    st.dataframe(df, use_container_width=True)

# ── TAB 6: EXPORTS
with tab6:
    st.title("Download Reports")
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("Download Full Data CSV", csv, "payroll_data.csv")
    if not flag_df.empty:
        st.download_button("Download Flags Report", flag_df.to_csv(index=False).encode("utf-8"), "flags.csv")
