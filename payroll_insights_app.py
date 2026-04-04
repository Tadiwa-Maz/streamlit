import io
import json
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Payroll Insights", page_icon="💼", layout="wide")

# ── CONSTANTS ────────────────────────────────────────────────────────────────
MAX_FILE_SIZE_MB = 20
SKIP_SHEET_KEYWORDS = [
    "summary",
    "cover",
    "contents",
    "index",
    "notes",
    "legend",
    "instructions",
]

DEFAULT_THRESHOLDS = {
    "critical_deduction_ratio": 0.80,
    "warning_deduction_ratio": 0.50,
    "warning_earnings_vs_basic_multiplier": 2.0,
}

COLUMN_MAP = {
    "Employee Name": ["employee name", "name", "full name", "staff name"],
    "Employee Code": ["employee code", "emp code", "emp id", "staff id", "id", "employee id"],
    "Total Earnings": ["total earnings", "total earn", "earnings", "gross pay", "gross earnings", "gross"],
    "Total Deductions": ["total deductions", "total ded", "deductions", "total deduction"],
    "Net Pay": ["net pay", "net salary", "take home", "nett pay", "net income"],
    "Basic Salary": ["basic salary", "basic pay", "basic", "salary"],
    "Pay as you Earn": ["paye", "pay as you earn", "income tax", "tax"],
    "Unemployment Insurance Fund": ["uif", "unemployment insurance", "unemployment"],
    "Pension": ["pension", "provident", "retirement"],
    "Employment Type": ["employment type", "emp type", "contract type", "type"],
}


# ── SMART PARSER ─────────────────────────────────────────────────────────────
def detect_header_row(raw: pd.DataFrame) -> int:
    """Find the row index that most likely contains column headers.
    Expects raw to already be a string-typed DataFrame."""
    best_row, best_score = 0, 0
    for i in range(min(20, len(raw))):
        try:
            row_text = " ".join(raw.iloc[i].str.lower().fillna("").tolist())
        except Exception:
            row_text = " ".join(str(v).lower() for v in raw.iloc[i])
        score = sum(1 for keywords in COLUMN_MAP.values() for kw in keywords if kw in row_text)
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
        if any(kw in sheet.lower() for kw in SKIP_SHEET_KEYWORDS):
            continue
        if selected_sheets and sheet not in selected_sheets:
            continue

        raw = pd.read_excel(xls, sheet_name=sheet, header=None)
        if raw.empty:
            continue

        raw = raw.fillna("").astype(str)

        header_row = detect_header_row(raw)
        headers = raw.iloc[header_row].str.strip().tolist()
        df = pd.read_excel(xls, sheet_name=sheet, header=None, skiprows=header_row + 1)
        if df.empty:
            continue
        df.columns = make_unique_columns(headers[: len(df.columns)])
        df = df.dropna(how="all")

        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].astype(str).str.strip().replace("nan", pd.NA)

        df = auto_map_columns(df)

        if not looks_like_payroll(df):
            continue

        df["_sheet"] = sheet
        dfs.append(df)
        used_sheets.append(sheet)

    if not dfs:
        raise ValueError(
            "No recognisable payroll data found in any sheet. "
            "Check that the file has columns like 'Employee Name', 'Total Earnings', or 'Net Pay'."
        )

    all_cols = list(dict.fromkeys(col for d in dfs for col in d.columns))
    dfs = [d.reindex(columns=all_cols) for d in dfs]

    return pd.concat(dfs, ignore_index=True), used_sheets


def coerce_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0)


def prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    numeric_targets = [
        "Total Earnings",
        "Total Deductions",
        "Net Pay",
        "Basic Salary",
        "Pay as you Earn",
        "Unemployment Insurance Fund",
        "Pension",
    ]
    df = df.copy()
    for col in numeric_targets:
        if col in df.columns:
            df[col] = coerce_numeric(df[col])
    return df


# ── FLAGS ENGINE ─────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def run_flags(df_json: str, thresholds_json: str) -> pd.DataFrame:
    df = pd.read_json(io.StringIO(df_json))
    thresholds = json.loads(thresholds_json)

    crit_ratio = thresholds["critical_deduction_ratio"]
    warn_ratio = thresholds["warning_deduction_ratio"]
    earn_mult = thresholds["warning_earnings_vs_basic_multiplier"]

    flags = []

    def add(level, name, code, issue):
        flags.append({"Level": level, "Employee": name, "Code": str(code), "Issue": issue})

    for _, r in df.iterrows():
        name = r.get("Employee Name", "Unknown")
        code = r.get("Employee Code", "")
        earn = float(r.get("Total Earnings", 0) or 0)
        ded = float(r.get("Total Deductions", 0) or 0)
        net = float(r.get("Net Pay", 0) or 0)
        basic = float(r.get("Basic Salary", 0) or 0)
        paye = float(r.get("Pay as you Earn", 0) or 0)
        uif = float(r.get("Unemployment Insurance Fund", 0) or 0)
        pension = float(r.get("Pension", 0) or 0)
        emp_type = str(r.get("Employment Type", "")).lower()

        is_salaried = basic > 0 and emp_type not in (
            "contract",
            "casual",
            "freelance",
            "independent",
        )

        seen = set()

        def flag(level, issue):
            key = (name, issue)
            if key not in seen:
                seen.add(key)
                add(level, name, code, issue)

        if net < 0:
            flag("Critical", "Negative net pay")
        if earn > 0 and ded >= earn * crit_ratio:
            flag("Critical", f"Deductions ≥ {int(crit_ratio * 100)}% of earnings")
        if is_salaried and paye == 0:
            flag("Critical", "No PAYE on salaried employee")

        if earn > 0 and uif == 0:
            flag("Warning", "No UIF deduction")
        if is_salaried and pension == 0:
            flag("Warning", "No pension contribution")
        if basic > 0 and earn > basic * earn_mult:
            flag("Warning", f"Earnings > {earn_mult}× basic salary")
        if earn > 0 and ded > 0 and (ded / earn) > warn_ratio:
            flag("Warning", f"Deductions > {int(warn_ratio * 100)}% of earnings")
        if earn > 0 and net > earn:
            flag("Warning", "Net pay exceeds total earnings")

        if basic > 0 and earn > 0 and abs(earn - basic) < 1:
            flag("Info", "No variable earnings detected")
        if earn == 0 and net == 0:
            flag("Info", "Zero earnings and net pay — possibly inactive")

    flag_df = pd.DataFrame(flags) if flags else pd.DataFrame(columns=["Level", "Employee", "Code", "Issue"])
    if not flag_df.empty:
        severity_map = {"Critical": 3, "Warning": 2, "Info": 1}
        flag_df["Severity"] = flag_df["Level"].map(severity_map)
        flag_df = flag_df.sort_values("Severity", ascending=False).drop(columns="Severity")
    return flag_df


# ── STYLING HELPERS ──────────────────────────────────────────────────────────
LEVEL_COLORS = {"Critical": "🔴", "Warning": "🟡", "Info": "🔵"}


def style_flags(flag_df: pd.DataFrame) -> pd.DataFrame:
    flag_df = flag_df.copy()
    flag_df["Level"] = flag_df["Level"].apply(lambda x: f"{LEVEL_COLORS.get(x, '')} {x}")
    return flag_df


# ── ANALYSIS HELPERS ─────────────────────────────────────────────────────────
def enrich_analysis_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "Total Earnings" not in df.columns:
        return df

    safe_earnings = df["Total Earnings"].replace(0, pd.NA)

    df["Deduction Rate %"] = (df.get("Total Deductions", 0) / safe_earnings * 100).round(2)
    df["Net-to-Gross %"] = (df.get("Net Pay", 0) / safe_earnings * 100).round(2)

    if "Pay as you Earn" in df:
        df["PAYE % of Gross"] = (df["Pay as you Earn"] / safe_earnings * 100).round(2)
    if "Pension" in df:
        df["Pension % of Gross"] = (df["Pension"] / safe_earnings * 100).round(2)
    if "Unemployment Insurance Fund" in df:
        df["UIF % of Gross"] = (df["Unemployment Insurance Fund"] / safe_earnings * 100).round(2)

    if "Basic Salary" in df:
        df["Variable Earnings"] = (df["Total Earnings"] - df["Basic Salary"]).round(2)
        df["Variable % of Gross"] = (df["Variable Earnings"] / safe_earnings * 100).round(2)

    return df


def compute_kpis(df: pd.DataFrame, thresholds: dict) -> dict:
    """Centralise KPI math so visuals and text stay consistent."""
    kpis: dict[str, float | int | None] = {
        "ded_median": None,
        "ded_p90": None,
        "ded_above_warn": None,
        "net_median": None,
        "net_p10": None,
        "net_p90": None,
        "paye_cov": None,
        "uif_cov": None,
    }

    if "Deduction Rate %" in df:
        series = df["Deduction Rate %"].dropna()
        if not series.empty:
            kpis["ded_median"] = float(series.median())
            kpis["ded_p90"] = float(series.quantile(0.9))
            kpis["ded_above_warn"] = float((series >= thresholds["warning_deduction_ratio"] * 100).mean() * 100)

    if "Net-to-Gross %" in df:
        series = df["Net-to-Gross %"].dropna()
        if not series.empty:
            kpis["net_median"] = float(series.median())
            kpis["net_p10"] = float(series.quantile(0.1))
            kpis["net_p90"] = float(series.quantile(0.9))

    def coverage(col):
        return float((df[col] > 0).mean() * 100) if col in df else None

    kpis["paye_cov"] = coverage("Pay as you Earn")
    kpis["uif_cov"] = coverage("Unemployment Insurance Fund")

    return kpis


def band_shares(series: pd.Series, bands: list[tuple[float | None, float | None, str]]) -> pd.DataFrame:
    """Return share of rows falling into each numeric band."""
    total = len(series)
    rows = []
    for low, high, label in bands:
        mask = pd.Series(True, index=series.index)
        if low is not None:
            mask &= series >= low
        if high is not None:
            mask &= series < high
        count = int(mask.sum())
        pct = (count / total * 100) if total else 0
        rows.append({"Band": label, "Employees": count, "Share %": round(pct, 1)})
    return pd.DataFrame(rows)


def _nice_step(value: float) -> float:
    """Pick a round step size (1, 2, 5 x 10^n) near the target value."""
    import math

    if value <= 0:
        return 1000.0
    exponent = 10 ** math.floor(math.log10(value))
    for m in (1, 2, 5, 10):
        step = m * exponent
        if value / step <= 5:
            return step
    return 10 * exponent


def make_value_bands(series: pd.Series, target_bins: int = 5) -> list[tuple[float | None, float | None, str]]:
    """Create simple Rand ranges based on the data spread."""
    if series.empty:
        return []
    max_val = float(series.max())
    step = _nice_step(max_val / max(1, target_bins))
    bounds = [0.0]
    while bounds[-1] < max_val:
        bounds.append(bounds[-1] + step)
    bands = []
    for i in range(len(bounds) - 1):
        low, high = bounds[i], bounds[i + 1]
        bands.append((low, high, f"R {low:,.0f} – R {high:,.0f}"))
    bands.append((bounds[-1], None, f"R {bounds[-1]:,.0f}+"))
    return bands


def summary_highlights(df: pd.DataFrame, kpis: dict, thresholds: dict) -> list[str]:
    highlights = []

    if kpis.get("ded_median") is not None:
        highlights.append(f"Typical deduction rate sits around {kpis['ded_median']:.1f}% of gross pay")
    if kpis.get("ded_above_warn") is not None:
        warn_pct = thresholds["warning_deduction_ratio"] * 100
        highlights.append(f"{kpis['ded_above_warn']:.0f}% of people have deductions above the {warn_pct:.0f}% warning level")

    if kpis.get("net_median") is not None:
        highlights.append(
            f"Most people take home about {kpis['net_median']:.1f}% of their gross pay"
        )

    if kpis.get("paye_cov") is not None:
        highlights.append(f"PAYE is being withheld for {kpis['paye_cov']:.0f}% of employees")
    if kpis.get("uif_cov") is not None:
        highlights.append(f"UIF is being withheld for {kpis['uif_cov']:.0f}% of employees")

    return highlights


# ── SIDEBAR ──────────────────────────────────────────────────────────────────
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

file_bytes = uploaded.read()
file_size_mb = len(file_bytes) / (1024 * 1024)
if file_size_mb > MAX_FILE_SIZE_MB:
    st.error(f"File too large ({file_size_mb:.1f} MB). Maximum allowed is {MAX_FILE_SIZE_MB} MB.")
    st.stop()

with st.spinner("Reading payroll file…"):
    try:
        df_raw, used_sheets = parse_excel_all_sheets(file_bytes)
    except Exception as e:
        st.error(f"Could not parse file: {e}")
        st.stop()

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

_df_prepared = prepare_df(df_raw)
df = enrich_analysis_columns(_df_prepared)

flag_df = run_flags(df.to_json(), json.dumps(thresholds))

n_critical = (flag_df["Level"] == "Critical").sum() if not flag_df.empty else 0
n_warning = (flag_df["Level"] == "Warning").sum() if not flag_df.empty else 0
n_info = (flag_df["Level"] == "Info").sum() if not flag_df.empty else 0

# ── TABS ─────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Overview",
    f"🚩 Flags ({n_critical}🔴 {n_warning}🟡)",
    "📈 Analysis",
    "👤 Employee Drilldown",
    "📋 Full Data",
    "📥 Exports",
])

# ── TAB 1: OVERVIEW ─────────────────────────────────────────────────────────
with tab1:
    st.title("Overview")

    has_core = all(c in df.columns for c in ["Total Earnings", "Total Deductions", "Net Pay"])

    if has_core:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Employees", f"{len(df):,}")
        c2.metric("Total Earnings", f"R {df['Total Earnings'].sum():,.0f}")
        c3.metric("Total Deductions", f"R {df['Total Deductions'].sum():,.0f}")
        c4.metric("Net Pay", f"R {df['Net Pay'].sum():,.0f}")
        avg_net = df["Net Pay"].mean()
        c5.metric("Avg Net Pay", f"R {avg_net:,.0f}")

        st.divider()
        col_l, col_r = st.columns(2)
        with col_l:
            st.subheader("Pay distribution")
            fig = px.box(df, y="Net Pay", points="outliers", labels={"Net Pay": "Net Pay (R)"})
            st.plotly_chart(fig, use_container_width=True)
        with col_r:
            st.subheader("Earnings vs Net Pay")
            if "Employee Name" in df.columns:
                fig2 = px.scatter(
                    df,
                    x="Total Earnings",
                    y="Net Pay",
                    hover_name="Employee Name",
                    labels={"Total Earnings": "Gross (R)", "Net Pay": "Net (R)"},
                )
                max_val = df[["Total Earnings", "Net Pay"]].max().max()
                fig2.add_shape(
                    type="line",
                    x0=0,
                    y0=0,
                    x1=max_val,
                    y1=max_val,
                    line=dict(dash="dash", color="grey"),
                )
                st.plotly_chart(fig2, use_container_width=True)

    if used_sheets:
        st.caption(f"Sheets loaded: {', '.join(used_sheets)}")

    flag_summary_cols = st.columns(3)
    flag_summary_cols[0].metric("🔴 Critical Flags", n_critical)
    flag_summary_cols[1].metric("🟡 Warning Flags", n_warning)
    flag_summary_cols[2].metric("🔵 Info Flags", n_info)


# ── TAB 2: FLAGS ─────────────────────────────────────────────────────────────
with tab2:
    st.title("Flags & Anomalies")

    if flag_df.empty:
        st.success("No issues detected in this payroll run.")
    else:
        level_filter = st.multiselect(
            "Filter by level", ["Critical", "Warning", "Info"], default=["Critical", "Warning", "Info"]
        )
        filtered_flags = flag_df[flag_df["Level"].isin(level_filter)]

        st.caption(f"Showing {len(filtered_flags)} of {len(flag_df)} flags")

        counts = flag_df["Level"].value_counts().reindex(["Critical", "Warning", "Info"], fill_value=0)
        fig_bar = px.bar(
            x=counts.index,
            y=counts.values,
            color=counts.index,
            color_discrete_map={"Critical": "#ef4444", "Warning": "#f59e0b", "Info": "#3b82f6"},
            labels={"x": "Level", "y": "Count"},
            title="Flag Summary",
        )
        fig_bar.update_layout(showlegend=False)
        st.plotly_chart(fig_bar, use_container_width=True)

        st.dataframe(style_flags(filtered_flags), use_container_width=True, hide_index=True)

        if len(filtered_flags) > 0:
            st.subheader("Most flagged employees")
            top_flagged = (
                filtered_flags.groupby("Employee")
                .size()
                .reset_index(name="Flag Count")
                .sort_values("Flag Count", ascending=False)
                .head(10)
            )
            fig_top = px.bar(top_flagged, x="Flag Count", y="Employee", orientation="h", title="Top Flagged Employees")
            fig_top.update_layout(yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig_top, use_container_width=True)


# ── TAB 3: ANALYSIS ─────────────────────────────────────────────────────────
with tab3:
    st.title("Analysis")

    kpis = compute_kpis(df, thresholds)
    highlights = summary_highlights(df, kpis, thresholds)
    if highlights:
        st.markdown("\n".join([f"- {text}" for text in highlights]))

    col_a, col_b = st.columns(2)

    with col_a:
        if "Total Earnings" in df.columns:
            st.subheader("How gross pay is spread")
            earnings = df["Total Earnings"].dropna()
            bands = make_value_bands(earnings)
            earnings_table = band_shares(earnings, bands)
            st.dataframe(earnings_table, hide_index=True, use_container_width=True)
            fig = px.bar(earnings_table, x="Band", y="Share %", text="Share %")
            fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
            fig.update_yaxes(range=[0, 100])
            st.plotly_chart(fig, use_container_width=True)

    with col_b:
        if "Deduction Rate %" in df.columns:
            st.subheader("Deduction bands (people)")
            rate_series = df["Deduction Rate %"].dropna()
            if not rate_series.empty:
                bands = [
                    (None, 30, "<30% of gross"),
                    (30, 50, "30–50% of gross"),
                    (50, 80, "50–80% of gross"),
                    (80, None, "≥80% of gross"),
                ]
                rate_table = band_shares(rate_series, bands)
                st.dataframe(rate_table, hide_index=True, use_container_width=True)
                fig2 = px.bar(rate_table, x="Band", y="Share %", text="Share %")
                fig2.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
                fig2.update_yaxes(range=[0, 100])
                st.plotly_chart(fig2, use_container_width=True)

    col_c, col_d = st.columns(2)
    with col_c:
        if "Net-to-Gross %" in df.columns:
            st.subheader("Take‑home bands (net / gross)")
            takehome_band = df["Net-to-Gross %"].dropna()
            if not takehome_band.empty:
                bands = [
                    (None, 50, "<50% of gross"),
                    (50, 70, "50–70% of gross"),
                    (70, 90, "70–90% of gross"),
                    (90, None, "≥90% of gross"),
                ]
                take_table = band_shares(takehome_band, bands)
                st.dataframe(take_table, hide_index=True, use_container_width=True)
                fig_ng = px.bar(take_table, x="Band", y="Share %", text="Share %")
                fig_ng.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
                fig_ng.update_yaxes(range=[0, 100])
                st.plotly_chart(fig_ng, use_container_width=True)

    with col_d:
        if "Deduction Rate %" in df.columns and "Employee Name" in df.columns:
            st.subheader("Top 10 highest deduction rates")
            top10 = df.sort_values("Deduction Rate %", ascending=False)[
                ["Employee Name", "Employee Code", "Deduction Rate %", "Total Earnings", "Total Deductions"]
            ].head(10)
            st.dataframe(top10.reset_index(drop=True), hide_index=True, use_container_width=True)

    if "_sheet" in df.columns and df["_sheet"].nunique() > 1:
        st.subheader("Sheet comparison (month‑over‑month)")
        sheet_summary = df.groupby("_sheet")[
            [col for col in ["Total Earnings", "Net Pay", "Total Deductions"] if col in df.columns]
        ].sum().reset_index()
        sheet_summary.columns = ["Sheet"] + sheet_summary.columns[1:].tolist()
        fig_sheet = px.bar(
            sheet_summary.melt(id_vars="Sheet"),
            x="Sheet",
            y="value",
            color="variable",
            barmode="group",
            labels={"value": "Amount (R)", "variable": "Metric"},
        )
        st.plotly_chart(fig_sheet, use_container_width=True)


# ── HELPER ───────────────────────────────────────────────────────────────────
def safe_metric(row, col, prefix="R "):
    val = row.get(col, 0)
    try:
        return f"{prefix}{float(val):,.2f}"
    except Exception:
        return str(val) if val else "—"


def render_employee_detail(r, flag_df):
    emp_name = r.get("Employee Name", "")
    emp_code = r.get("Employee Code", "")
    st.subheader(f"👤 {emp_name}  ({emp_code})")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Total Earnings", safe_metric(r, "Total Earnings"))
        st.metric("Basic Salary", safe_metric(r, "Basic Salary"))
    with c2:
        st.metric("Total Deductions", safe_metric(r, "Total Deductions"))
        st.metric("PAYE", safe_metric(r, "Pay as you Earn"))
    with c3:
        st.metric("Net Pay", safe_metric(r, "Net Pay"))
        st.metric("UIF", safe_metric(r, "Unemployment Insurance Fund"))

    ded_rate = r.get("Deduction Rate %")
    net_rate = r.get("Net-to-Gross %")
    if ded_rate is not None:
        if ded_rate < 50:
            badge = "🟢 <50%"
        elif ded_rate < 80:
            badge = "🟡 50–80%"
        else:
            badge = "🔴 ≥80%"
    else:
        badge = "—"

    cols_ratio = st.columns(3)
    cols_ratio[0].metric("Deduction rate", f"{ded_rate:.1f}%" if ded_rate is not None else "—", badge)
    cols_ratio[1].metric("Take‑home (net/gross)", f"{net_rate:.1f}%" if net_rate is not None else "—")
    cols_ratio[2].metric("Variable earnings", safe_metric(r, "Variable Earnings") if "Variable Earnings" in r else "—")

    earn_val = float(r.get("Total Earnings", 0) or 0)
    ded_val = float(r.get("Total Deductions", 0) or 0)
    net_val = float(r.get("Net Pay", 0) or 0)
    if earn_val > 0:
        fig_wf = go.Figure(
            go.Waterfall(
                orientation="v",
                measure=["absolute", "relative", "total"],
                x=["Gross Earnings", "Deductions", "Net Pay"],
                y=[earn_val, -ded_val, net_val],
                connector={"line": {"color": "rgb(63,63,63)"}},
            )
        )
        fig_wf.update_layout(title="Earnings Waterfall", showlegend=False)
        st.plotly_chart(fig_wf, use_container_width=True)

    st.subheader("Flags for this employee")
    emp_flags = flag_df[flag_df["Employee"] == emp_name]
    if emp_flags.empty:
        st.success("No flags for this employee.")
    else:
        st.dataframe(style_flags(emp_flags), use_container_width=True, hide_index=True)

    st.subheader("Compliance checks")
    checks = []
    checks.append(("PAYE present", r.get("Pay as you Earn", 0) > 0))
    checks.append(("UIF present", r.get("Unemployment Insurance Fund", 0) > 0))
    checks.append(("Pension present", r.get("Pension", 0) > 0))
    if ded_rate is not None:
        checks.append(("Deduction rate under 80%", ded_rate < 80))
    check_rows = [{"Check": c[0], "Status": "Yes" if c[1] else "Missing"} for c in checks]
    st.table(pd.DataFrame(check_rows))

    if "_sheet" in df.columns:
        st.subheader("Across periods (by sheet)")
        emp_all = df[df["Employee Name"] == emp_name]
        if not emp_all.empty:
            hist_cols = [c for c in ["_sheet", "Total Earnings", "Total Deductions", "Net Pay", "Deduction Rate %", "Net-to-Gross %"] if c in emp_all.columns]
            hist = emp_all[hist_cols].rename(columns={"_sheet": "Sheet"})
            st.dataframe(hist.reset_index(drop=True), hide_index=True, use_container_width=True)
            if "Sheet" in hist and "Net Pay" in hist:
                fig_hist = px.line(hist, x="Sheet", y="Net Pay", markers=True, title="Net Pay over sheets")
                st.plotly_chart(fig_hist, use_container_width=True)

    st.subheader("Payslip‑style breakdown")
    groups = []
    def add_row(label, val):
        groups.append({"Item": label, "Value": val})
    add_row("Gross pay", safe_metric(r, "Total Earnings"))
    add_row("Basic salary", safe_metric(r, "Basic Salary"))
    add_row("Variable earnings", safe_metric(r, "Variable Earnings") if "Variable Earnings" in r else "—")
    add_row("PAYE", safe_metric(r, "Pay as you Earn"))
    add_row("UIF", safe_metric(r, "Unemployment Insurance Fund"))
    add_row("Pension", safe_metric(r, "Pension"))
    add_row("Total deductions", safe_metric(r, "Total Deductions"))
    add_row("Net pay", safe_metric(r, "Net Pay"))
    st.table(pd.DataFrame(groups))

    st.subheader("Reviewer notes")
    note_key = f"note_{emp_name}_{emp_code}"
    default_note = st.session_state.get(note_key, "")
    note_val = st.text_area("Notes (kept only in this session)", value=default_note, key=note_key)
    st.session_state[note_key] = note_val


# ── TAB 4: EMPLOYEE DRILLDOWN ───────────────────────────────────────────────
with tab4:
    st.title("Employee Drilldown")

    search = st.text_input("Search by employee name or code", placeholder="e.g. Smith or EMP001")

    if not search:
        st.info("Enter a name or employee code to search.")
    else:
        mask = df.astype(str).apply(lambda row: row.str.contains(search, case=False, na=False).any(), axis=1)
        filtered = df[mask].copy()
        st.caption(f"{len(filtered)} result(s)")

        if filtered.empty:
            st.warning("No employees matched your search.")
        elif len(filtered) == 1:
            render_employee_detail(filtered.iloc[0], flag_df)
        else:
            display_cols = [
                c
                for c in ["Employee Name", "Employee Code", "Total Earnings", "Total Deductions", "Net Pay"]
                if c in filtered.columns
            ]
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
            else:
                render_employee_detail(filtered.iloc[selected_rows[0]], flag_df)


# ── TAB 5: FULL DATA ─────────────────────────────────────────────────────────
with tab5:
    st.title("Full Dataset")
    st.caption(f"{len(df):,} rows × {len(df.columns)} columns")

    all_cols = df.columns.tolist()
    default_show = [c for c in all_cols if not c.startswith("_")]
    cols_to_show = st.multiselect("Columns to display", all_cols, default=default_show)

    sort_col = st.selectbox("Sort by", [None] + cols_to_show)
    sort_asc = st.checkbox("Ascending", value=True)

    view_df = df[cols_to_show] if cols_to_show else df
    if sort_col:
        view_df = view_df.sort_values(sort_col, ascending=sort_asc)

    st.dataframe(view_df.reset_index(drop=True), use_container_width=True, hide_index=True)


# ── TAB 6: EXPORTS ───────────────────────────────────────────────────────────
with tab6:
    st.title("Download Reports")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("Full Payroll Data")
        csv_data = df.drop(columns=[c for c in ["_sheet"] if c in df.columns]).to_csv(index=False).encode("utf-8")
        st.download_button("Download CSV", csv_data, file_name="payroll_data.csv", mime="text/csv")

    with col2:
        st.subheader("Flags Report")
        if flag_df.empty:
            st.info("No flags to export.")
        else:
            flag_csv = flag_df.to_csv(index=False).encode("utf-8")
            st.download_button("Download Flags CSV", flag_csv, file_name="payroll_flags.csv", mime="text/csv")

    with col3:
        st.subheader("Summary Statistics")
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        if numeric_cols:
            summary_csv = df[numeric_cols].describe().to_csv().encode("utf-8")
            st.download_button(
                "Download Summary Stats",
                summary_csv,
                file_name="payroll_summary_stats.csv",
                mime="text/csv",
            )
