import io
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Payroll Variance Insights", page_icon="💼", layout="wide")

MAX_FILE_SIZE_MB = 20
HEADER_ROW_INDEX = 4  # based on the provided template

ID_COLUMNS = {
    "Employee Code",
    "Employee Name",
    "(HA) Area",
    "(HA) Department",
    "Employee Status Description",
    "Pay Run Short Description",
    "Job Title Type Short Description",
    "Date Joined Group",
    "Nature of Contract Short Description",
    "Date Engaged",
    "Termination Date",
    "Age",
    "Pension Fund Start Date",
}

TOTAL_COLUMNS = {
    "Total Earnings",
    "Total Deductions",
    "Total Company Contributions",
    "Salary Cost",
    "Net Pay",
    "Balance Of Remuneration",
}


def make_unique_columns(cols: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for c in cols:
        c = str(c).strip()
        if c not in seen:
            seen[c] = 0
            result.append(c)
        else:
            seen[c] += 1
            result.append(f"{c}_{seen[c]}")
    return result


def extract_period_label(raw: pd.DataFrame) -> str:
    """Try to pull month description from the template header rows."""
    candidates = []
    for i in range(1, 4):
        if i >= len(raw):
            break
        row = raw.iloc[i].dropna().astype(str).tolist()
        candidates.extend([r for r in row if r.strip()])
    for value in candidates:
        if "-" in value and any(m in value.lower() for m in ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]):
            return value.strip()
    return "Period"


@st.cache_data(show_spinner=False)
def read_template_excel(file_bytes: bytes) -> tuple[pd.DataFrame, str]:
    raw = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=None)
    if raw.empty:
        raise ValueError("The file is empty.")

    period_label = extract_period_label(raw)

    headers = raw.iloc[HEADER_ROW_INDEX].tolist()
    headers = make_unique_columns(headers)

    df = raw.iloc[HEADER_ROW_INDEX + 1 :].copy()
    df.columns = headers[: len(df.columns)]
    df = df.dropna(how="all")

    # Strip string columns
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip().replace("nan", pd.NA)

    # Coerce numeric for non-id columns
    for col in df.columns:
        if col in ID_COLUMNS:
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    return df, period_label


def variance_table(df_prev: pd.DataFrame, df_curr: pd.DataFrame, key_cols: list[str]) -> pd.DataFrame:
    rows = []
    for col in key_cols:
        if col not in df_prev.columns or col not in df_curr.columns:
            continue
        prev_val = df_prev[col].sum()
        curr_val = df_curr[col].sum()
        diff = curr_val - prev_val
        pct = (diff / prev_val * 100) if prev_val else 0
        rows.append(
            {
                "Item": col,
                "Previous": prev_val,
                "Current": curr_val,
                "Change": diff,
                "Change %": round(pct, 1),
            }
        )
    return pd.DataFrame(rows)


def top_change_drivers(df_prev: pd.DataFrame, df_curr: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    common_cols = [c for c in df_curr.columns if c in df_prev.columns]
    exclude_cols = ID_COLUMNS | TOTAL_COLUMNS
    numeric_cols = [c for c in common_cols if c not in exclude_cols]

    changes = []
    for col in numeric_cols:
        prev_val = df_prev[col].sum()
        curr_val = df_curr[col].sum()
        diff = curr_val - prev_val
        if diff == 0:
            continue
        changes.append({"Item": col, "Previous": prev_val, "Current": curr_val, "Change": diff})

    if not changes:
        return pd.DataFrame(columns=["Item", "Previous", "Current", "Change"])

    df_changes = pd.DataFrame(changes)
    df_changes["Abs Change"] = df_changes["Change"].abs()
    df_changes = df_changes.sort_values("Abs Change", ascending=False).head(top_n)
    return df_changes.drop(columns=["Abs Change"])


def per_employee_variance(df_prev: pd.DataFrame, df_curr: pd.DataFrame) -> pd.DataFrame:
    key = ["Employee Code", "Employee Name"]
    prev = df_prev[key + [c for c in TOTAL_COLUMNS if c in df_prev.columns]].copy()
    curr = df_curr[key + [c for c in TOTAL_COLUMNS if c in df_curr.columns]].copy()

    prev = prev.groupby(key, dropna=False).sum(numeric_only=True).reset_index()
    curr = curr.groupby(key, dropna=False).sum(numeric_only=True).reset_index()

    merged = prev.merge(curr, on=key, how="outer", suffixes=("_prev", "_curr")).fillna(0)

    for col in TOTAL_COLUMNS:
        if f"{col}_prev" in merged.columns and f"{col}_curr" in merged.columns:
            merged[f"{col} Change"] = merged[f"{col}_curr"] - merged[f"{col}_prev"]

    return merged


with st.sidebar:
    st.title("Payroll Variance Insights")
    st.divider()
    file_prev = st.file_uploader("Upload previous month", type=["xlsx", "xls"], key="prev")
    file_curr = st.file_uploader("Upload current month", type=["xlsx", "xls"], key="curr")

if not file_prev or not file_curr:
    st.title("Payroll Variance Insights")
    st.info("Upload two months to compare, for example May and June.")
    st.stop()

for f in (file_prev, file_curr):
    size_mb = len(f.getvalue()) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        st.error(f"File too large ({size_mb:.1f} MB). Maximum allowed is {MAX_FILE_SIZE_MB} MB.")
        st.stop()

with st.spinner("Reading files..."):
    df_prev, label_prev = read_template_excel(file_prev.getvalue())
    df_curr, label_curr = read_template_excel(file_curr.getvalue())

# Standardize column alignment
all_cols = list(dict.fromkeys(list(df_prev.columns) + list(df_curr.columns)))
df_prev = df_prev.reindex(columns=all_cols, fill_value=0)
df_curr = df_curr.reindex(columns=all_cols, fill_value=0)

st.title("Month-to-month comparison")
st.caption(f"Comparing {label_prev} to {label_curr}")

# Summary totals
summary = variance_table(df_prev, df_curr, [c for c in TOTAL_COLUMNS if c in all_cols])

col1, col2 = st.columns(2)
with col1:
    st.subheader("Total changes")
    st.dataframe(summary, hide_index=True, use_container_width=True)

with col2:
    if not summary.empty:
        fig = px.bar(summary, x="Item", y="Change", text="Change")
        fig.update_traces(texttemplate="%{text:.0f}", textposition="outside")
        st.plotly_chart(fig, use_container_width=True)

st.divider()

# Top drivers
st.subheader("Biggest drivers of change")
drivers = top_change_drivers(df_prev, df_curr, top_n=15)
st.dataframe(drivers, hide_index=True, use_container_width=True)

st.divider()

# Employee-level variance
st.subheader("Employee variance list")
emp_var = per_employee_variance(df_prev, df_curr)

# Allow user to sort by a key metric
sort_options = [c for c in emp_var.columns if c.endswith("Change")]
sort_by = st.selectbox("Sort by", sort_options)
sort_dir = st.checkbox("Largest first", value=True)
emp_var = emp_var.sort_values(sort_by, ascending=not sort_dir)

st.dataframe(emp_var, hide_index=True, use_container_width=True)

st.divider()

# Drilldown for one employee
st.subheader("Employee drilldown")
search = st.text_input("Search employee code or name")
if search:
    matches = emp_var[
        emp_var["Employee Code"].astype(str).str.contains(search, case=False, na=False)
        | emp_var["Employee Name"].astype(str).str.contains(search, case=False, na=False)
    ]
    st.caption(f"{len(matches)} match(es)")
    if len(matches) == 1:
        row = matches.iloc[0]
        st.write({
            "Employee Code": row.get("Employee Code"),
            "Employee Name": row.get("Employee Name"),
        })
        # Show previous vs current for that employee
        emp_code = row.get("Employee Code")
        prev_rows = df_prev[df_prev["Employee Code"] == emp_code]
        curr_rows = df_curr[df_curr["Employee Code"] == emp_code]
        if not prev_rows.empty and not curr_rows.empty:
            compare_cols = [c for c in TOTAL_COLUMNS if c in all_cols]
            prev_vals = prev_rows[compare_cols].sum().rename("Previous")
            curr_vals = curr_rows[compare_cols].sum().rename("Current")
            detail = pd.concat([prev_vals, curr_vals], axis=1)
            detail["Change"] = detail["Current"] - detail["Previous"]
            st.dataframe(detail.reset_index().rename(columns={"index": "Item"}), hide_index=True, use_container_width=True)

            # Show top component drivers for this employee
            emp_drivers = top_change_drivers(prev_rows, curr_rows, top_n=10)
            st.subheader("What drove the change for this employee")
            st.dataframe(emp_drivers, hide_index=True, use_container_width=True)
    else:
        st.dataframe(matches, hide_index=True, use_container_width=True)
