"""AG Grid (community) Reflex 래퍼 — 계정/로그인 불필요, 자체 포함.

ag-grid-react(무료 community)를 직접 감싼다. **AG Grid v33** 사용:
 - Reflex 0.9 는 React 19 → AG Grid v32는 ResizeObserver 에러. v33이 React 19 지원.
 - v33 Theming API(JS 테마) 사용 → 레거시 CSS import/클래스 쓰지 않음.
 - v33은 모듈 등록 필요: ModuleRegistry.registerModules([AllCommunityModule]).
"""
import reflex as rx


def _selected_rows(e: rx.Var):
    """on_selection_changed → 선택 행(list[dict]) 전달 (Phase 2 다중선택용)."""
    return [rx.Var(f"{e}.api.getSelectedRows()")]


class AgGrid(rx.Component):
    """ag-grid-react v33 래퍼."""

    library = "ag-grid-react@^33"
    tag = "AgGridReact"
    lib_dependencies = ["ag-grid-community@^33"]

    column_defs: rx.Var[list[dict]]
    row_data: rx.Var[list[dict]]
    default_col_def: rx.Var[dict]
    row_selection: rx.Var[str]
    pagination: rx.Var[bool]
    pagination_page_size: rx.Var[int]

    on_selection_changed: rx.EventHandler[_selected_rows]

    def add_imports(self):
        # v33 모듈/테마는 ag-grid-community 에서 import
        return {"ag-grid-community": ["ModuleRegistry", "AllCommunityModule"]}

    def add_custom_code(self):
        # 모든 community 기능 등록 (정렬/필터/페이지네이션/테마 포함)
        return ["ModuleRegistry.registerModules([AllCommunityModule]);"]


ag_grid = AgGrid.create
