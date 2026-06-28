import reflex as rx

# Reflex 앱 설정 (Stage 3 — Streamlit 과 공존, 같은 Supabase + 같은 서비스 계층)
# AG Grid 는 자체 래퍼(pcb_reflex/ag_grid_wrap.py, ag-grid-react community)로 — 로그인 불필요.
config = rx.Config(
    app_name="pcb_reflex",
)
