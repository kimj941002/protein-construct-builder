import os

import reflex as rx

# Reflex 앱 설정 (Stage 3 — Streamlit 과 공존, 같은 Supabase + 같은 서비스 계층)
# AG Grid 는 자체 래퍼(pcb_reflex/ag_grid_wrap.py, ag-grid-react community)로 — 로그인 불필요.
#
# 배포(공유) 시: API_URL 환경변수에 공개 https 주소를 넣으면 프론트가 그 주소로 백엔드(websocket)에 연결.
#   예) API_URL=https://pdb.example.com
# 로컬 개발 시: 미설정 → http://localhost:8000 기본값.
# API_URL 이 있을 때만 명시(배포용). 로컬은 Reflex 자동 감지에 맡긴다.
# 다크 테마를 RadixThemesPlugin 로 설정(권장 경로). App(theme=...) 는 deprecated 이며
# 배포 빌드에서 appearance 가 누락돼 브라우저/OS 의 라이트모드를 따라가는 문제가 있었다.
_theme = rx.theme(
    appearance="dark",
    accent_color="gray",
    gray_color="slate",
    radius="large",
    scaling="100%",
)

_cfg = {
    "app_name": "pcb_reflex",
    "plugins": [
        rx.plugins.RadixThemesPlugin(theme=_theme),
        rx.plugins.SitemapPlugin(),
    ],
}
if os.environ.get("API_URL"):
    _cfg["api_url"] = os.environ["API_URL"]
config = rx.Config(**_cfg)
