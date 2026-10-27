import streamlit as st
import pandas as pd
from pathlib import Path
from io import BytesIO
import re
import html

st.set_page_config(
    page_title="Project Intelligence | The Learning Forge",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DATA_DIR = Path(__file__).parent / "data"


def build_agent_pdf(goal: str, report: str, tool_log: list) -> bytes:
    """Create a polished PDF executive report in memory using ReportLab."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether
    )

    buffer = BytesIO()
    navy = colors.HexColor("#0B1020")
    blue = colors.HexColor("#315FEA")
    muted = colors.HexColor("#526079")
    pale = colors.HexColor("#EEF3FF")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=23, leading=28, textColor=navy, alignment=TA_LEFT,
        spaceAfter=5 * mm
    ))
    styles.add(ParagraphStyle(
        name="ReportSubtitle", parent=styles["Normal"], fontSize=9,
        leading=13, textColor=muted, spaceAfter=2 * mm
    ))
    styles.add(ParagraphStyle(
        name="SectionHeading", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=13, leading=16, textColor=blue, spaceBefore=5 * mm, spaceAfter=2 * mm
    ))
    styles.add(ParagraphStyle(
        name="ReportBody", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=9, leading=13, textColor=colors.HexColor("#202B40"),
        spaceAfter=2.2 * mm, wordWrap="CJK"
    ))
    styles.add(ParagraphStyle(
        name="SmallMeta", parent=styles["Normal"], fontSize=8,
        leading=11, textColor=muted
    ))

    def clean_markup(text: str) -> str:
        text = html.escape(text, quote=False)
        text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"`([^`]+)`", r"<font name='Courier'>\1</font>", text)
        return text.replace("  ", " ")

    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=19*mm, leftMargin=19*mm,
        topMargin=18*mm, bottomMargin=18*mm,
        title="Project Intelligence — Executive Report",
        author="The Learning Forge | Project Intelligence Platform"
    )
    story = [
        Paragraph("PROJECT INTELLIGENCE", styles["ReportSubtitle"]),
        Paragraph("Executive Investigation Report", styles["ReportTitle"]),
        HRFlowable(width="100%", thickness=1.5, color=blue, spaceAfter=5*mm),
        Paragraph("<b>Business goal</b>", styles["ReportBody"]),
        Paragraph(clean_markup(goal), styles["ReportBody"]),
        Spacer(1, 2*mm),
        Paragraph("Prepared by the AI Agent · Evidence-led analysis · For human review", styles["ReportSubtitle"]),
        HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#D7DEEC"), spaceBefore=2*mm, spaceAfter=3*mm),
    ]

    # Format headings, bullets, and ordinary paragraphs from the Markdown report.
    in_table = False
    for raw_line in (report or "No report content was returned.").splitlines():
        line = raw_line.strip()
        if not line:
            story.append(Spacer(1, 1.5*mm))
            continue
        if line.startswith("|"):
            # Keep Markdown table rows readable as compact text rather than overflowing a PDF table.
            if set(line.replace("|", "").replace("-", "").replace(":", "").strip()) == set():
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            cell_text = "  •  ".join(c for c in cells if c)
            story.append(Paragraph(clean_markup(cell_text), styles["ReportBody"]))
            continue
        heading_match = re.match(r"^#{1,4}\s+(.*)", line)
        if heading_match:
            story.append(Paragraph(clean_markup(heading_match.group(1)), styles["SectionHeading"]))
        elif line.startswith(("- ", "* ", "• ")):
            bullet_text = line[2:].strip() if line[:2] in ("- ", "* ") else line[2:].strip()
            story.append(Paragraph("&#8226;&nbsp;&nbsp;" + clean_markup(bullet_text), styles["ReportBody"]))
        else:
            story.append(Paragraph(clean_markup(line), styles["ReportBody"]))

    if tool_log:
        story.extend([Spacer(1, 4*mm), Paragraph("Investigation Activity", styles["SectionHeading"])])
        rows = [["Tool", "Search / input", "Result"]]
        for item in tool_log:
            rows.append([
                str(item.get("tool", "")),
                str(item.get("query", "") or "—"),
                str(item.get("result_status", "")),
            ])
        safe_rows = [[Paragraph(html.escape(str(cell)), styles["SmallMeta"]) for cell in row] for row in rows]
        table = Table(safe_rows, colWidths=[43*mm, 91*mm, 27*mm], repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), navy),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("BACKGROUND", (0,1), (-1,-1), colors.white),
            ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#D7DEEC")),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("LEFTPADDING", (0,0), (-1,-1), 6),
            ("RIGHTPADDING", (0,0), (-1,-1), 6),
            ("TOPPADDING", (0,0), (-1,-1), 6),
            ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ]))
        story.append(table)

    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D7DEEC"))
        canvas.line(19*mm, 13*mm, A4[0]-19*mm, 13*mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(19*mm, 8*mm, "THE LEARNING FORGE · PROJECT INTELLIGENCE")
        canvas.drawRightString(A4[0]-19*mm, 8*mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
    --navy: #0B1020;
    --panel: #131B30;
    --blue: #528BFF;
    --violet: #9B7BFF;
    --text: #E9EEFA;
    --muted: #9AA8C2;
}

header[data-testid="stHeader"] {
    background: transparent;
}

[data-testid="stToolbar"] {
    right: 1rem;
}

.stAppViewBlockContainer {
    padding-top: 1rem;
}

.stApp {
    background:
        radial-gradient(ellipse at 8% 0%, #19284B 0%, transparent 38%),
        linear-gradient(145deg, #0B1020 0%, #10172A 55%, #111329 100%);
    color: var(--text);
    font-family: 'Inter', sans-serif;
}

.block-container {
    max-width: 1400px;
    padding-top: 2rem;
    padding-bottom: 3rem;
}

h1, h2, h3, p, label, .stMarkdown {
    font-family: 'Inter', sans-serif;
}

h1 {
    color: #F4F7FF !important;
    font-weight: 800 !important;
    letter-spacing: -1.3px;
}

h2, h3 {
    color: #E9EEFA !important;
    letter-spacing: -0.4px;
}

p, label {
    color: #C1CBE0;
}

.hero {
    background: linear-gradient(120deg, #182B50 0%, #24214B 55%, #302454 100%);
    border: 1px solid #34456B;
    border-radius: 24px;
    padding: 30px 32px;
    margin: 8px 0 26px 0;
    box-shadow: 0 18px 50px rgba(0,0,0,.22);
}

.eyebrow {
    color: #9FC0FF;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 2px;
    text-transform: uppercase;
}

.hero-title {
    color: #FFFFFF;
    font-size: clamp(28px, 4vw, 43px);
    font-weight: 800;
    line-height: 1.15;
    margin: 12px 0;
    letter-spacing: -1.4px;
}

.hero-copy {
    color: #CBD8F5;
    font-size: 15px;
    line-height: 1.7;
    max-width: 760px;
}

.pill {
    display: inline-block;
    color: #D7E5FF;
    border: 1px solid #526B9B;
    background: rgba(255,255,255,.06);
    padding: 6px 11px;
    border-radius: 30px;
    font-size: 11px;
    margin: 14px 5px 0 0;
}

div[data-testid="stMetric"] {
    background: linear-gradient(145deg, #18233D, #131B30);
    border: 1px solid #2B3A5B;
    border-radius: 16px;
    padding: 18px;
}

div[data-testid="stMetricLabel"] {
    color: #AFC0E1;
}

div[data-testid="stMetricValue"] {
    color: #FFFFFF;
    font-weight: 800;
}

div[data-testid="stTabs"] button {
    color: #B9C6E0;
    font-weight: 700;
    padding: 14px 18px;
}

div[data-testid="stTabs"] button[aria-selected="true"] {
    color: #91B7FF !important;
}

div[data-testid="stTabs"] [data-baseweb="tab-highlight"] {
    background-color: #528BFF;
}

div[data-testid="stTabs"] [data-baseweb="tab-panel"] {
    background: rgba(19,27,48,.76);
    border: 1px solid #2B3A5B;
    border-radius: 0 0 18px 18px;
    padding: 25px 24px;
    margin-top: 2px;
}

.stButton > button {
    background: linear-gradient(100deg, #477FFF, #8165F5);
    color: white;
    border: 0;
    border-radius: 11px;
    min-height: 46px;
    padding: 0 22px;
    font-weight: 700;
    box-shadow: 0 6px 20px rgba(82,139,255,.2);
    transition: all .2s ease;
}

.stButton > button:hover {
    border: 1px solid #B9CFFF;
    color: white;
    filter: brightness(1.12);
    transform: translateY(-1px);
}

.stTextInput input, .stTextArea textarea {
    background: #0E1629;
    color: #F1F5FF;
    border: 1px solid #344363;
    border-radius: 11px;
}

div[data-baseweb="select"] > div {
    background: #0E1629;
    border-color: #344363;
    border-radius: 11px;
    color: #F1F5FF;
}

div[data-testid="stDataFrame"] {
    border: 1px solid #2B3A5B;
    border-radius: 12px;
    overflow: hidden;
}

hr {
    border-color: #2A3651;
}

.agent-result-banner {
    background: linear-gradient(120deg, rgba(34,197,94,.13), rgba(82,139,255,.12));
    border: 1px solid #36516D; border-radius: 16px; padding: 18px 20px;
    margin: 12px 0 18px 0;
}
.agent-result-eyebrow { color: #9FC0FF; font-size: 10px; font-weight: 800; letter-spacing: 1.8px; }
.agent-result-title { color: #FFFFFF; font-size: 23px; font-weight: 800; margin-top: 5px; }
.agent-result-copy { color: #C1CBE0; font-size: 13px; margin-top: 5px; }

.footer {
    color: #7E8EAE;
    font-size: 12px;
    text-align: center;
    padding-top: 25px;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
    <div class="eyebrow">✦ THE LEARNING FORGE &nbsp; / &nbsp; AI SOLUTION LAB</div>
    <div class="hero-title">Project Intelligence Platform</div>
    <div class="hero-copy">
        One business problem. Three solution patterns.
        Explore deterministic workflows, conversational AI,
        and tool-using agents through an enterprise project
        reporting use case.
    </div>
    <span class="pill">WORKFLOW AUTOMATION</span>
    <span class="pill">GENERATIVE AI</span>
    <span class="pill">SOLUTION ARCHITECTURE</span>
</div>
""", unsafe_allow_html=True)

try:
    projects = pd.read_csv(DATA_DIR / "projects.csv")
except Exception as e:
    st.error(f"Could not load projects.csv: {e}")
    st.stop()

st.markdown("### Portfolio at a glance")

status_col = "overall_status"
if status_col in projects.columns:
    at_risk = projects[status_col].astype(str).str.upper().isin(
        ["AMBER", "RED"]
    ).sum()
else:
    at_risk = 0

m1, m2, m3 = st.columns(3)
m1.metric("Total Projects", len(projects))
m2.metric("Projects Requiring Attention", int(at_risk))
m3.metric("Data Fields", len(projects.columns))

st.write("")

workflow_tab, chatbot_tab, agent_tab = st.tabs([
    "⚙️  Workflow Automation",
    "✧  AI Chatbot",
    "◈  AI Agent",
])

with workflow_tab:
    st.markdown("### Workflow Studio")
    st.write(
        "Select a predefined business process. "
        "The workflow will apply explicit rules to your project data."
    )

    workflow_name = st.selectbox(
        "Choose a workflow",
        [
            "Weekly Portfolio Status Report",
            "At-Risk Project Report",
            "Milestone Slippage Report",
            "Risk & Issue Report",
            "Budget Variance Report",
            "Steering Committee Report",
        ],
    )

    st.caption("Execution mode: Deterministic · No LLM required")

    if st.button("▶  Run Workflow", type="primary",
             key="run_workflow"):

        st.success(f"Workflow completed: {workflow_name}")

        if workflow_name == "Weekly Portfolio Status Report":
            st.subheader("Weekly Portfolio Status Report")
            st.write(f"Reporting date: {projects['status_report_date'].iloc[0]}")

            st.dataframe(
                projects[
                    ["project_id", "project_name", "overall_status",
                    "schedule_status", "budget_status", "risk_status"]
                ],
                use_container_width=True,
                hide_index=True
            )

            st.download_button(
                "⬇ Download Portfolio Report (CSV)",
                data=projects.to_csv(index=False).encode("utf-8"),
                file_name="weekly_portfolio_report.csv",
                mime="text/csv"
            )

        elif workflow_name == "At-Risk Project Report":
            report = projects[
                projects["overall_status"].astype(str).str.upper()
                .isin(["AMBER", "RED"])
            ]
            st.subheader("At-Risk Projects")
            st.dataframe(report, use_container_width=True, hide_index=True)

            st.download_button(
                "⬇ Download At-Risk Report",
                data=report.to_csv(index=False).encode("utf-8"),
                file_name="at_risk_projects.csv",
                mime="text/csv"
            )

        elif workflow_name == "Milestone Slippage Report":
            milestones_file = DATA_DIR / "milestones.csv"

            if milestones_file.exists():
                milestones = pd.read_csv(milestones_file)

                milestones["baseline_date"] = pd.to_datetime(
                    milestones["baseline_date"], errors="coerce"
                )
                milestones["forecast_date"] = pd.to_datetime(
                    milestones["forecast_date"], errors="coerce"
                )

                milestones["delay_days"] = (
                    milestones["forecast_date"]
                    - milestones["baseline_date"]
                ).dt.days

                delayed = milestones[
                    milestones["delay_days"] > 0
                ].copy()

                st.subheader("Milestone Slippage Report")
                st.metric("Delayed Milestones", len(delayed))

                st.dataframe(
                    delayed,
                    use_container_width=True,
                    hide_index=True
                )

                st.download_button(
                    "⬇ Download Milestone Slippage Report",
                    data=delayed.to_csv(index=False).encode("utf-8"),
                    file_name="milestone_slippage_report.csv",
                    mime="text/csv"
                )
            else:
                st.error("milestones.csv was not found in the data folder.")
        elif workflow_name == "Risk & Issue Report":
            risks_file = DATA_DIR / "risks.csv"

            if risks_file.exists():
                risks = pd.read_csv(risks_file)

                st.subheader("Risk & Issue Report")

                # Show high-severity risks where severity is available
                if "severity" in risks.columns:
                    high_risks = risks[
                        risks["severity"]
                        .astype(str)
                        .str.upper()
                        .isin(["HIGH", "CRITICAL", "SEVERE"])
                    ]
                elif "risk_score" in risks.columns:
                    high_risks = risks[
                        pd.to_numeric(
                            risks["risk_score"], errors="coerce"
                        ) >= 15
                    ]
                else:
                    high_risks = risks.copy()

                st.metric("Total Risks", len(risks))
                st.metric("High-Priority Risks", len(high_risks))

                st.dataframe(
                    high_risks,
                    use_container_width=True,
                    hide_index=True
                )

                st.download_button(
                    "⬇ Download Risk & Issue Report",
                    data=high_risks.to_csv(index=False).encode("utf-8"),
                    file_name="risk_issue_report.csv",
                    mime="text/csv"
                )
            else:
                st.error("risks.csv was not found in the data folder.")

        elif workflow_name == "Budget Variance Report":
            st.subheader("Budget Variance Report")

            required_columns = [
                "project_id",
                "project_name",
                "budget_eur",
                "actual_cost_eur",
                "forecast_cost_eur",
            ]

            if all(col in projects.columns for col in required_columns):
                budget_report = projects[required_columns].copy()

                budget_report["budget_used_pct"] = (
                    pd.to_numeric(budget_report["actual_cost_eur"], errors="coerce")
                    / pd.to_numeric(budget_report["budget_eur"], errors="coerce")
                    * 100
                ).round(1)

                budget_report["forecast_variance"] = (
                    pd.to_numeric(budget_report["forecast_cost_eur"], errors="coerce")
                    - pd.to_numeric(budget_report["budget_eur"], errors="coerce")
                )

                budget_report["budget_outlook"] = budget_report[
                    "forecast_variance"
                ].apply(
                    lambda x: "Over Budget" if x > 0
                    else "Within Budget"
                )

                st.metric(
                    "Projects Forecast Over Budget",
                    int((budget_report["forecast_variance"] > 0).sum())
                )

                st.dataframe(
                    budget_report,
                    use_container_width=True,
                    hide_index=True
                )

                st.download_button(
                    "⬇ Download Budget Variance Report",
                    data=budget_report.to_csv(index=False).encode("utf-8"),
                    file_name="budget_variance_report.csv",
                    mime="text/csv"
                )
            else:
                st.error(
                    "Required budget columns were not found. "
                    "Check the column names in projects.csv."
                )

        elif workflow_name == "Steering Committee Report":
            st.subheader("Steering Committee Report")

            status_counts = projects["overall_status"].value_counts()

            red_count = int(
                projects["overall_status"].astype(str).str.upper().eq("RED").sum()
            )
            amber_count = int(
                projects["overall_status"].astype(str).str.upper().eq("AMBER").sum()
            )

            st.markdown("### Executive Summary")

            c1, c2, c3 = st.columns(3)
            c1.metric("Total Projects", len(projects))
            c2.metric("RED Projects", red_count)
            c3.metric("AMBER Projects", amber_count)

            st.markdown("### Portfolio Status Distribution")

            chart_data = (
                projects["overall_status"]
                .astype(str)
                .str.upper()
                .value_counts()
                .reindex(["GREEN", "AMBER", "RED"], fill_value=0)
                .rename_axis("Status")
                .reset_index(name="Projects")
            )

            import altair as alt

            status_chart = (
                alt.Chart(chart_data)
                .mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6)
                .encode(
                    x=alt.X(
                        "Status:N",
                        sort=["GREEN", "AMBER", "RED"],
                        title="Project Status",
                        axis=alt.Axis(labelColor="#000000", titleColor="#000000")
                    ),
                    y=alt.Y(
                        "Projects:Q",
                        title="Number of Projects",
                        axis=alt.Axis(
                            labelColor="#000000",
                            titleColor="#000000",
                            gridColor="#283650",
                            tickMinStep=1
                        )
                    ),
                    color=alt.Color(
                        "Status:N",
                        scale=alt.Scale(
                            domain=["GREEN", "AMBER", "RED"],
                            range=["#22C55E", "#F59E0B", "#EF4444"]
                        ),
                        legend=None
                    ),
                    tooltip=["Status:N", "Projects:Q"]
                )
                .properties(height=260, width=200)
                .configure_view(strokeOpacity=0, continuousWidth=20, discreteWidth=35)
                .configure(background="#FFFFFF")
                .configure_axis(
                    labelColor='#334155',
                    titleColor='#334155',
                    gridColor='#E2E8F0'
                )
            )

            st.altair_chart(status_chart, use_container_width=True)


            st.markdown("### Projects Requiring Decisions")

            decision_projects = projects[
                projects["overall_status"].astype(str).str.upper().isin(
                    ["RED", "AMBER"]
                )
            ]

            display_columns = [
                col for col in [
                    "project_id",
                    "project_name",
                    "overall_status",
                    "schedule_status",
                    "budget_status",
                    "risk_status",
                ]
                if col in decision_projects.columns
            ]

            st.dataframe(
                decision_projects[display_columns],
                use_container_width=True,
                hide_index=True
            )

            st.download_button(
                "⬇ Download Steering Committee Report",
                data=decision_projects[display_columns].to_csv(
                    index=False
                ).encode("utf-8"),
                file_name="steering_committee_summary.csv",
                mime="text/csv"
            )

        else:
            st.info(
                f"{workflow_name} is selected. "
                "Its dedicated report logic will be added next."
            )

with chatbot_tab:
    st.markdown("### Ask your Project Copilot")
    st.write(
        "Ask a question about the portfolio in everyday language."
    )

    with st.container(border=True):
        st.markdown("**Try asking**")
        st.caption(
            "Which projects are at risk? · "
            "What decisions need management attention?"
        )

        st.text_input(
            "Your question",
            placeholder="Ask about status, risks, milestones or budget...",
            key="chat_question",
        )

        if st.button("➤  Send Question", key="send_question", type="primary"):
            question = st.session_state.get("chat_question", "").strip()

            if not question:
                st.warning("Please enter a question first.")
            else:
                try:
                    with st.spinner("Project Copilot is analysing your question..."):
                        from modules.chatbot import ask_chatbot

                        answer = ask_chatbot(question)

                    if answer:
                        st.markdown("#### Answer")
                        st.markdown(answer)
                    else:
                        st.warning(
                            "The chatbot returned an empty response. "
                            "Please try asking in a different way."
                        )
                except Exception as e:
                    st.error(
                        "I couldn't generate an answer. Check that the "
                        "chatbot configuration and API keys are set up, "
                        "then try again."
                    )
                    with st.expander("Technical details"):
                        st.code(str(e))

with agent_tab:
    st.markdown("### AI Agent Workspace")
    st.write(
        "Set a business goal. The agent can inspect structured project data, "
        "search indexed documents, cross-check evidence, and prepare an executive report."
    )

    with st.container(border=True):
        st.text_area(
            "Business goal",
            placeholder=(
                "Investigate migration delays, cross-check risks against "
                "milestone dates and architecture documents, identify evidence "
                "gaps, and recommend management actions."
            ),
            height=120,
            key="agent_goal",
        )
        st.caption(
            "Read-only investigation. Recommendations are for human review; "
            "the agent does not execute management actions."
        )
        if st.button("◈  Start Investigation", type="primary", key="start_agent"):
            goal = st.session_state.get("agent_goal", "").strip()
            if not goal:
                st.warning("Please enter a business goal first.")
            else:
                try:
                    from modules.agent import run_agent
                    with st.spinner("Investigating project data and indexed documents..."):
                        result = run_agent(goal)
                    st.session_state["agent_last_result"] = result
                    st.session_state["agent_last_goal"] = goal
                except Exception as e:
                    st.session_state["agent_last_result"] = {
                        "report": f"Agent execution failed: {type(e).__name__}: {e}",
                        "tool_log": [],
                        "status": "error",
                    }
                    st.session_state["agent_last_goal"] = goal

    # Keep the report visible after Streamlit reruns (e.g. when downloading the PDF).
    result = st.session_state.get("agent_last_result")
    if result:
        report = result.get("report", "") or "No report was returned."
        tool_log = result.get("tool_log", []) or []
        status = result.get("status", "unknown")
        goal = st.session_state.get("agent_last_goal", "")

        st.markdown(
            "<div class='agent-result-banner'>"
            "<div class='agent-result-eyebrow'>THE LEARNING FORGE / INVESTIGATION COMPLETE</div>"
            "<div class='agent-result-title'>Executive Investigation Report</div>"
            "<div class='agent-result-copy'>Findings, evidence, risks and proposed management actions</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Run status", "Completed" if status == "success" else "Needs review")
        c2.metric("Tool calls", len(tool_log))
        c3.metric("Distinct tools", len({item.get("tool") for item in tool_log if item.get("tool")}))

        if status == "success":
            st.success("Investigation report generated. Review the evidence and recommendations before acting.")
        else:
            st.warning("The investigation did not complete successfully. Review the message below and technical details if needed.")

        with st.container(border=True):
            st.markdown(report)

        col_pdf, col_tools = st.columns([1, 2])
        with col_pdf:
            try:
                pdf_bytes = build_agent_pdf(goal, report, tool_log)
                st.download_button(
                    "⬇ Download Executive Report (PDF)",
                    data=pdf_bytes,
                    file_name="project_intelligence_executive_report.pdf",
                    mime="application/pdf",
                    type="primary",
                    key="download_agent_pdf",
                    use_container_width=True,
                )
            except ImportError:
                st.warning("PDF export requires ReportLab. Install it with: pip install reportlab")
            except Exception as e:
                st.error("Could not prepare the PDF export.")
                with st.expander("PDF technical details"):
                    st.code(str(e))
        with col_tools:
            if tool_log:
                with st.expander("View investigation activity / tool calls"):
                    st.dataframe(
                        pd.DataFrame(tool_log),
                        use_container_width=True,
                        hide_index=True,
                    )

        if status != "success":
            with st.expander("Agent technical details"):
                st.code(report)
            st.caption(
                "If this is a token-rate-limit error, wait before retrying and use a focused goal."
            )

with st.expander("View source project data"):
    st.dataframe(projects, use_container_width=True, hide_index=True)

st.markdown("""
<div class="footer">
    THE LEARNING FORGE · BUILDING BETTER AI SOLUTIONS
</div>
""", unsafe_allow_html=True)