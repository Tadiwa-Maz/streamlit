import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import io

st.set_page_config(page_title="Payroll Insights", page_icon="💼", layout="wide")

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
MAX_FILE_SIZE_MB = 20
SKIP_SHEET_KEYWORDS = ["summary", "cover", "contents", "index", "notes", "legend", "instructions"]

# Configurable thresholds (can be overridden via sidebar)
DEFAULT_THRESHOLDS = {
    "critical_deduction_ratio": 0.80,
    "warning_deduction_ratio": 0.50,
    "warning_earnings_vs_basic_multiplier": 2.0,
}

COLUMN_MAP = {
    "Employee Name":                ["employee name", "name", "full name", "staff name"],
    "Employee Code":                ["employee code", "emp code", "emp id", "staff id", "id", "employee id"],
    "Total Earnings":               ["total earnings", "total earn", "earnings", "gross pay", "gross earnings", "gross"],
    "Total Deductions":             ["total deductions", "total ded", "deductions", "total deduction"],
    "Net Pay":                      ["net pay", "net salary", "take home", "nett pay", "net income"],
    "Basic Salary":                 ["basic salary", "basic pay", "basic", "salary"],
    "Pay as you Earn":              ["paye", "pay as you earn", "income tax", "tax"],
    "Unemployment Insurance Fund":  ["uif", "unemployment insurance", "unemployment"],
    "Pension":                      ["pension", "provident", "retirement"],
    "Employment Type":              ["employment type", "emp type", "contract type", "type"],
}


# ── SMART PARSER ──────────────────────────────────────────────────────────────
def detect_header_row(raw: pd.DataFrame) -> int:
    """Find the row index that most likely contains column headers.
    Expects raw to already be a string-typed DataFrame."""
    best_row, best_score = 0, 0
    for i in range(min(20, len(raw))):
        try:
            row_text = " ".join(raw.iloc[i].str.lower().fillna("").tolist())
        except Exception:
            row_text = " ".join(str(v).lower() for v in raw.iloc[i])
        score = sum(
            1 for keywords in COLUMN_MAP.values()
            for kw in keywords if kw in row_text
        )
        if score > best_score:
            best_score, best_row = score, i
    return best_row


def auto_map_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map raw column names to standardised names using fuzzy keyword matching."""
    df_copy = df.copy()
    used_targets = set()
    for col in df.columns:
        col_clean = str(col).lower().strip()
        for target, options in COLUMN_MAP.items():
            if target in used_targets:
                continue
            if any(opt in col_clean for opt in options):
                df_copy[target] = df[col]
                used_targets.add(target)
                break
    return df_copy


def make_unique_columns(cols: pd.Index) -> list:
    seen = {}
    result = []
    for c in cols:
        c = str(c)
        if c not in seen:
            seen[c] = 0
            result.append(c)
        else:
            seen[c] += 1
            result.append(f"{c}_{seen[c]}")
    return result


def looks_like_payroll(df: pd.DataFrame) -> bool:
    """Heuristic: at least 2 mapped key columns must be present."""
    key_cols = {"Employee Name", "Total Earnings", "Net Pay", "Basic Salary"}
    return len(key_cols & set(df.columns)) >= 2


@st.cache_data(show_spinner=False)
def parse_excel_all_sheets(file_bytes: bytes, selected_sheets: list | None = None) -> tuple[pd.DataFrame, list[str]]:
    """
    Parse an Excel workbook, auto-detecting headers on each sheet.
    Returns (combined_df, list_of_sheet_names_used).
    """
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    all_sheet_names = xls.sheet_names

    dfs, used_sheets = [], []
    for sheet in all_sheet_names:
        # Skip obviously non-payroll sheets
        if any(kw in sheet.lower() for kw in SKIP_SHEET_KEYWORDS):
            continue
        if selected_sheets and sheet not in selected_sheets:
            continue

        raw = pd.read_excel(xls, sheet_name=sheet, header=None)
        if raw.empty:
            continue

        # Force every cell to string so header detection never sees floats
        raw = raw.fillna("").astype(str)

        header_row = detect_header_row(raw)
        headers = raw.iloc[header_row].str.strip().tolist()
        df = pd.read_excel(xls, sheet_name=sheet, header=None,
                           skiprows=header_row + 1)
        if df.empty:
            continue
        df.columns = make_unique_columns(headers[:len(df.columns)])
        df = df.dropna(how="all")

        # Strip whitespace from string columns
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].astype(str).str.strip().replace("nan", pd.NA)

        df = auto_map_columns(df)

        if not looks_like_payroll(df):
            continue

        df["_sheet"] = sheet
        dfs.append(df)
        used_sheets.append(sheet)

    if not dfs:
        raise ValueError("No recognisable payroll data found in any sheet. "
                         "Check that the file has columns like 'Employee Name', "
                         "'Total Earnings', or 'Net Pay'.")

    # Align columns before concat to avoid mixed-type issues
    all_cols = list(dict.fromkeys(col for d in dfs for col in d.columns))
    dfs = [d.reindex(columns=all_cols) for d in dfs]

    return pd.concat(dfs, ignore_index=True), used_sheets


def coerce_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0)


def prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure all standard numeric columns are properly typed."""
    numeric_targets = [
        "Total Earnings", "Total Deductions", "Net Pay",
        "Basic Salary", "Pay as you Earn",
        "Unemployment Insurance Fund", "Pension",
    ]
    for col in numeric_targets:
        if col in df.columns:
            df[col] = coerce_numeric(df[col])
    return df


# ── FLAGS ENGINE ──────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def run_flags(df_json: str, thresholds_json: str) -> pd.DataFrame:
    """
    Cached flag engine. Accepts JSON strings so Streamlit can hash them.
    Returns a DataFrame of flags.
    """
    import json
    df = pd.read_json(io.StringIO(df_json))
    thresholds = json.loads(thresholds_json)

    crit_ratio  = thresholds["critical_deduction_ratio"]
    warn_ratio  = thresholds["warning_deduction_ratio"]
    earn_mult   = thresholds["warning_earnings_vs_basic_multiplier"]

    flags = []

    def add(level, name, code, issue):
        flags.append({"Level": level, "Employee": name, "Code": str(code), "Issue": issue})

    for _, r in df.iterrows():
        name  = r.get("Employee Name", "Unknown")
        code  = r.get("Employee Code", "")
        earn  = float(r.get("Total Earnings", 0) or 0)
        ded   = float(r.get("Total Deductions", 0) or 0)
        net   = float(r.get("Net Pay", 0) or 0)
        basic = float(r.get("Basic Salary", 0) or 0)
        paye  = float(r.get("Pay as you Earn", 0) or 0)
        uif   = float(r.get("Unemployment Insurance Fund", 0) or 0)
        pension = float(r.get("Pension", 0) or 0)
        emp_type = str(r.get("Employment Type", "")).lower()

        is_salaried = basic > 0 and emp_type not in ("contract", "casual", "freelance", "independent")

        seen = set()  # deduplicate flags per employee

        def flag(level, issue):
            key = (name, issue)
            if key not in seen:
                seen.add(key)
                add(level, name, code, issue)

        # ── CRITICAL
        if net < 0:
            flag("Critical", "Negative net pay")
        if earn > 0 and ded >= earn * crit_ratio:
            flag("Critical", f"Deductions ≥ {int(crit_ratio*100)}% of earnings")
        if is_salaried and paye == 0:
            flag("Critical", "No PAYE on salaried employee")

        # ── WARNING
        if earn > 0 and uif == 0:
            flag("Warning", "No UIF deduction")
        if is_salaried and pension == 0:
            flag("Warning", "No pension contribution")
        if basic > 0 and earn > basic * earn_mult:
            flag("Warning", f"Earnings > {earn_mult}× basic salary")
        if earn > 0 and ded > 0 and (ded / earn) > warn_ratio:
            flag("Warning", f"Deductions > {int(warn_ratio*100)}% of earnings")
        if earn > 0 and net > earn:
            flag("Warning", "Net pay exceeds total earnings")

        # ── INFO
        if basic > 0 and earn > 0 and abs(earn - basic) < 1:
            flag("Info", "No variable earnings detected")
        if earn == 0 and net == 0:
            flag("Info", "Zero earnings and net pay — possibly inactive")

    flag_df = pd.DataFrame(flags) if flags else pd.DataFrame(
        columns=["Level", "Employee", "Code", "Issue"]
    )
    if not flag_df.empty:
        severity_map = {"Critical": 3, "Warning": 2, "Info": 1}
        flag_df["Severity"] = flag_df["Level"].map(severity_map)
        flag_df = flag_df.sort_values("Severity", ascending=False).drop(columns="Severity")
    return flag_df


# ── STYLING HELPERS ───────────────────────────────────────────────────────────
LEVEL_COLORS = {"Critical": "🔴", "Warning": "🟡", "Info": "🔵"}

def style_flags(flag_df: pd.DataFrame) -> pd.DataFrame:
    flag_df = flag_df.copy()
    flag_df["Level"] = flag_df["Level"].apply(lambda x: f"{LEVEL_COLORS.get(x,'')} {x}")
    return flag_df


# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("💼 Payroll Insights")
    st.divider()

    uploaded = st.file_uploader("Upload payroll Excel", type=["xlsx", "xls"])

    st.divider()
    st.subheader("⚙️ Flag Thresholds")
    crit_pct = st.slider("Critical deduction ratio (%)", 50, 100, 80, 5)
    warn_pct = st.slider("Warning deduction ratio (%)", 20, 80, 50, 5)
    earn_mult = st.slider("Earnings vs basic multiplier", 1.5, 5.0, 2.0, 0.5)

    thresholds = {
        "critical_deduction_ratio": crit_pct / 100,
        "warning_deduction_ratio": warn_pct / 100,
        "warning_earnings_vs_basic_multiplier": earn_mult,
    }

if uploaded is None:
    st.title("💼 Payroll Insights")
    st.info("Upload an Excel payroll file using the sidebar to get started.")
    st.stop()

# ── FILE SIZE CHECK ───────────────────────────────────────────────────────────
file_bytes = uploaded.read()
file_size_mb = len(file_bytes) / (1024 * 1024)
if file_size_mb > MAX_FILE_SIZE_MB:
    st.error(f"File too large ({file_size_mb:.1f} MB). Maximum allowed is {MAX_FILE_SIZE_MB} MB.")
    st.stop()

# ── PARSE ─────────────────────────────────────────────────────────────────────
with st.spinner("Reading payroll file…"):
    try:
        df_raw, used_sheets = parse_excel_all_sheets(file_bytes)
    except Exception as e:
        st.error(f"Could not parse file: {e}")
        st.stop()

# Sheet selector (shown after first parse)
with st.sidebar:
    if len(used_sheets) > 1:
        st.divider()
        st.subheader("📄 Sheets")
        chosen_sheets = st.multiselect("Select sheets to include", used_sheets, default=used_sheets)
        if set(chosen_sheets) != set(used_sheets):
            try:
                df_raw, used_sheets = parse_excel_all_sheets(file_bytes, chosen_sheets)
            except Exception as e:
                st.error(str(e))
                st.stop()

df = prepare_df(df_raw)

# ── RUN FLAGS (once, cached) ──────────────────────────────────────────────────
import json
flag_df = run_flags(df.to_json(), json.dumps(thresholds))

n_critical = (flag_df["Level"] == "Critical").sum() if not flag_df.empty else 0
n_warning  = (flag_df["Level"] == "Warning").sum()  if not flag_df.empty else 0
n_info     = (flag_df["Level"] == "Info").sum()      if not flag_df.empty else 0

# ── TABS ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Overview",
    f"🚩 Flags ({n_critical}🔴 {n_warning}🟡)",
    "📈 Analysis",
    "👤 Employee Drilldown",
    "📋 Full Data",
    "📥 Exports",
])

# ── TAB 1: OVERVIEW ───────────────────────────────────────────────────────────
with tab1:
    st.title("Overview")

    has_core = all(c in df.columns for c in ["Total Earnings", "Total Deductions", "Net Pay"])

    if has_core:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Employees", f"{len(df):,}")
        c2.metric("Total Earnings",    f"R {df['Total Earnings'].sum():,.0f}")
        c3.metric("Total Deductions",  f"R {df['Total Deductions'].sum():,.0f}")
        c4.metric("Net Pay",           f"R {df['Net Pay'].sum():,.0f}")
        avg_net = df["Net Pay"].mean()
        c5.metric("Avg Net Pay",       f"R {avg_net:,.0f}")

        st.divider()
        col_l, col_r = st.columns(2)
        with col_l:
            st.subheader("Pay distribution")
            fig = px.box(
                df, y="Net Pay",
                points="outliers",
                labels={"Net Pay": "Net Pay (R)"},
            )
            st.plotly_chart(fig, use_container_width=True)
        with col_r:
            st.subheader("Earnings vs Net Pay")
            if "Employee Name" in df.columns:
                fig2 = px.scatter(
                    df,
                    x="Total Earnings", y="Net Pay",
                    hover_name="Employee Name",
                    labels={"Total Earnings": "Gross (R)", "Net Pay": "Net (R)"},
                )
                # Ideal line
                max_val = df[["Total Earnings", "Net Pay"]].max().max()
                fig2.add_shape(type="line", x0=0, y0=0, x1=max_val, y1=max_val,
                               line=dict(dash="dash", color="grey"))
                st.plotly_chart(fig2, use_container_width=True)

    if used_sheets:
        st.caption(f"Sheets loaded: {', '.join(used_sheets)}")

    flag_summary_cols = st.columns(3)
    flag_summary_cols[0].metric("🔴 Critical Flags", n_critical)
    flag_summary_cols[1].metric("🟡 Warning Flags",  n_warning)
    flag_summary_cols[2].metric("🔵 Info Flags",     n_info)


# ── TAB 2: FLAGS ──────────────────────────────────────────────────────────────
with tab2:
    st.title("Flags & Anomalies")

    if flag_df.empty:
        st.success("✅ No issues detected in this payroll run.")
    else:
        level_filter = st.multiselect(
            "Filter by level", ["Critical", "Warning", "Info"],
            default=["Critical", "Warning", "Info"]
        )
        filtered_flags = flag_df[flag_df["Level"].isin(level_filter)]

        st.caption(f"Showing {len(filtered_flags)} of {len(flag_df)} flags")

        # Summary bar chart
        counts = flag_df["Level"].value_counts().reindex(["Critical", "Warning", "Info"], fill_value=0)
        fig_bar = px.bar(
            x=counts.index, y=counts.values,
            color=counts.index,
            color_discrete_map={"Critical": "#ef4444", "Warning": "#f59e0b", "Info": "#3b82f6"},
            labels={"x": "Level", "y": "Count"},
            title="Flag Summary"
        )
        fig_bar.update_layout(showlegend=False)
        st.plotly_chart(fig_bar, use_container_width=True)

        st.dataframe(
            style_flags(filtered_flags),
            use_container_width=True,
            hide_index=True,
        )

        # Most flagged employees
        if len(filtered_flags) > 0:
            st.subheader("Most flagged employees")
            top_flagged = (
                filtered_flags.groupby("Employee")
                .size().reset_index(name="Flag Count")
                .sort_values("Flag Count", ascending=False)
                .head(10)
            )
            fig_top = px.bar(
                top_flagged, x="Flag Count", y="Employee",
                orientation="h", title="Top Flagged Employees"
            )
            fig_top.update_layout(yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig_top, use_container_width=True)


# ── TAB 3: ANALYSIS ───────────────────────────────────────────────────────────
with tab3:
    st.title("Analysis")

    col_a, col_b = st.columns(2)

    with col_a:
        if "Total Earnings" in df.columns:
            st.subheader("Earnings Distribution")
            fig = px.histogram(df, x="Total Earnings", nbins=30,
                               labels={"Total Earnings": "Gross Earnings (R)"})
            # Add percentile lines
            p25, p50, p75 = df["Total Earnings"].quantile([0.25, 0.5, 0.75])
            for pct, val, label in [(p25, p25, "P25"), (p50, p50, "Median"), (p75, p75, "P75")]:
                fig.add_vline(x=val, line_dash="dash", annotation_text=label)
            st.plotly_chart(fig, use_container_width=True)

            stats = df["Total Earnings"].describe()
            sc1, sc2, sc3 = st.columns(3)
            sc1.metric("Median", f"R {stats['50%']:,.0f}")
            sc2.metric("P25",    f"R {stats['25%']:,.0f}")
            sc3.metric("P75",    f"R {stats['75%']:,.0f}")

    with col_b:
        if "Net Pay" in df.columns and "Total Earnings" in df.columns:
            st.subheader("Effective Deduction Rate")
            df_copy = df.copy()
            df_copy["Deduction Rate"] = (
                df_copy["Total Deductions"] / df_copy["Total Earnings"].replace(0, pd.NA)
            ).dropna() * 100
            fig2 = px.histogram(df_copy, x="Deduction Rate", nbins=20,
                                labels={"Deduction Rate": "Deduction Rate (%)"})
            fig2.add_vline(x=50, line_dash="dash", line_color="orange",
                           annotation_text="50% warning")
            fig2.add_vline(x=80, line_dash="dash", line_color="red",
                           annotation_text="80% critical")
            st.plotly_chart(fig2, use_container_width=True)

    # Deduction composition — paginated
    ded_cols = ["Pay as you Earn", "Unemployment Insurance Fund", "Pension"]
    present_ded = [c for c in ded_cols if c in df.columns]
    if present_ded and "Employee Name" in df.columns:
        st.subheader("Deduction Composition per Employee")

        PAGE_SIZE = 30
        total_pages = max(1, -(-len(df) // PAGE_SIZE))  # ceiling division
        page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1) - 1
        df_page = df.iloc[page * PAGE_SIZE: (page + 1) * PAGE_SIZE]

        fig3 = go.Figure()
        for col in present_ded:
            fig3.add_trace(go.Bar(
                y=df_page["Employee Name"].astype(str),
                x=df_page[col].fillna(0),
                name=col,
                orientation="h",
            ))
        fig3.update_layout(
            barmode="stack",
            height=max(400, len(df_page) * 22),
            margin=dict(l=10, r=10),
        )
        st.plotly_chart(fig3, use_container_width=True)
        st.caption(f"Page {page+1} of {total_pages} ({PAGE_SIZE} employees per page)")

    # Multi-sheet comparison
    if "_sheet" in df.columns and df["_sheet"].nunique() > 1:
        st.subheader("Sheet Comparison (Month-over-Month)")
        sheet_summary = df.groupby("_sheet")[["Total Earnings", "Net Pay", "Total Deductions"]].sum().reset_index()
        sheet_summary.columns = ["Sheet", "Total Earnings", "Net Pay", "Total Deductions"]
        fig_sheet = px.bar(
            sheet_summary.melt(id_vars="Sheet"),
            x="Sheet", y="value", color="variable", barmode="group",
            labels={"value": "Amount (R)", "variable": "Metric"},
        )
        st.plotly_chart(fig_sheet, use_container_width=True)


# ── TAB 4: EMPLOYEE DRILLDOWN ─────────────────────────────────────────────────
with tab4:
    st.title("Employee Drilldown")

    search = st.text_input("Search by employee name or code", placeholder="e.g. Smith or EMP001")

    if not search:
        st.info("Enter a name or employee code to search.")
    else:
        mask = df.astype(str).apply(
            lambda row: row.str.contains(search, case=False, na=False).any(), axis=1
        )
        filtered = df[mask].copy()
        st.caption(f"{len(filtered)} result(s)")

        if filtered.empty:
            st.warning("No employees matched your search.")
        elif len(filtered) > 1:
            # Clickable table — user selects a row
            display_cols = [c for c in ["Employee Name", "Employee Code", "Total Earnings", "Total Deductions", "Net Pay"] if c in filtered.columns]
            selected = st.dataframe(
                filtered[display_cols].reset_index(drop=True),
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
            )
            selected_rows = selected.selection.rows if selected.selection else []
            if not selected_rows:
                st.info("Click a row above to view the full employee breakdown.")
                st.stop()
            r = filtered.iloc[selected_rows[0]]
        else:
            r = filtered.iloc[0]

        # ── Single employee detail view
        emp_name = r.get("Employee Name", "")
        emp_code = r.get("Employee Code", "")
        st.subheader(f"👤 {emp_name}  ({emp_code})")

        c1, c2, c3 = st.columns(3)
        def safe_metric(col, label, prefix="R "):
            val = r.get(col, 0)
            try:
                return f"{prefix}{float(val):,.2f}"
            except Exception:
                return str(val)

        with c1:
            st.metric("Total Earnings",   safe_metric("Total Earnings"))
            st.metric("Basic Salary",     safe_metric("Basic Salary"))
        with c2:
            st.metric("Total Deductions", safe_metric("Total Deductions"))
            st.metric("PAYE",             safe_metric("Pay as you Earn"))
        with c3:
            st.metric("Net Pay",          safe_metric("Net Pay"))
            st.metric("UIF",              safe_metric("Unemployment Insurance Fund"))

        # Waterfall chart: Earnings → Deductions → Net Pay
        earn_val = float(r.get("Total Earnings", 0) or 0)
        ded_val  = float(r.get("Total Deductions", 0) or 0)
        net_val  = float(r.get("Net Pay", 0) or 0)
        if earn_val > 0:
            fig_wf = go.Figure(go.Waterfall(
                orientation="v",
                measure=["absolute", "relative", "total"],
                x=["Gross Earnings", "Deductions", "Net Pay"],
                y=[earn_val, -ded_val, net_val],
                connector={"line": {"color": "rgb(63,63,63)"}},
            ))
            fig_wf.update_layout(title="Earnings Waterfall", showlegend=False)
            st.plotly_chart(fig_wf, use_container_width=True)

        st.subheader("Flags for this employee")
        emp_flags = flag_df[flag_df["Employee"] == emp_name]
        if emp_flags.empty:
            st.success("No flags for this employee.")
        else:
            st.dataframe(style_flags(emp_flags), use_container_width=True, hide_index=True)

        st.subheader("Full breakdown")
        breakdown = r.dropna()
        breakdown_df = pd.DataFrame({"Item": breakdown.index, "Value": breakdown.values})
        st.dataframe(breakdown_df, use_container_width=True, hide_index=True)


# ── TAB 5: FULL DATA ──────────────────────────────────────────────────────────
with tab5:
    st.title("Full Dataset")
    st.caption(f"{len(df):,} rows × {len(df.columns)} columns")

    # Column selector
    all_cols = df.columns.tolist()
    default_show = [c for c in all_cols if not c.startswith("_")]
    cols_to_show = st.multiselect("Columns to display", all_cols, default=default_show)

    sort_col = st.selectbox("Sort by", [None] + cols_to_show)
    sort_asc = st.checkbox("Ascending", value=True)

    view_df = df[cols_to_show] if cols_to_show else df
    if sort_col:
        view_df = view_df.sort_values(sort_col, ascending=sort_asc)

    st.dataframe(view_df.reset_index(drop=True), use_container_width=True, hide_index=True)


# ── TAB 6: EXPORTS ────────────────────────────────────────────────────────────
with tab6:
    st.title("Download Reports")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("Full Payroll Data")
        csv_data = df.drop(columns=[c for c in ["_sheet"] if c in df.columns]).to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇ Download CSV", csv_data,
            file_name="payroll_data.csv", mime="text/csv",
        )

    with col2:
        st.subheader("Flags Report")
        if flag_df.empty:
            st.info("No flags to export.")
        else:
            flag_csv = flag_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇ Download Flags CSV", flag_csv,
                file_name="payroll_flags.csv", mime="text/csv",
            )

    with col3:
        st.subheader("Summary Statistics")
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        if numeric_cols:
            summary_csv = df[numeric_cols].describe().to_csv().encode("utf-8")
            st.download_button(
                "⬇ Download Summary Stats", summary_csv,
                file_name="payroll_summary_stats.csv", mime="text/csv",
            )
