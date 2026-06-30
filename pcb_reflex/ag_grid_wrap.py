"""AG Grid (community) Reflex 래퍼 — 계정/로그인 불필요, 자체 포함.

ag-grid-react(무료 community)를 직접 감싼다. **AG Grid v33** 사용:
 - Reflex 0.9 는 React 19 → AG Grid v32는 ResizeObserver 에러. v33이 React 19 지원.
 - v33 Theming API(JS 테마) 사용 → 레거시 CSS import/클래스 쓰지 않음.
 - v33은 모듈 등록 필요: ModuleRegistry.registerModules([AllCommunityModule]).
"""
import reflex as rx


def _selected_rows(e: rx.Var):
    """on_selection_changed → 선택 행(list[dict]) 전달 (다중선택용)."""
    return [rx.Var(f"{e}.api.getSelectedRows()")]


def _clicked_row(e: rx.Var):
    """on_row_clicked → 클릭한 행 데이터(dict) 전달 (단일 선택 → 세부보기용)."""
    return [rx.Var(f"{e}.data")]


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
    on_row_clicked: rx.EventHandler[_clicked_row]

    def add_imports(self):
        # v33 모듈/테마는 ag-grid-community 에서, createElement 는 react 에서 import
        return {
            "ag-grid-community": ["ModuleRegistry", "AllCommunityModule"],
            "react": ["createElement"],
        }

    def add_custom_code(self):
        # 모든 community 기능 등록 (정렬/필터/페이지네이션/테마 포함)
        # + PDBe-KB 스타일 행내 커버리지 렌더러: 도메인(배경) + 구조 잔기범위 막대
        return [
            "ModuleRegistry.registerModules([AllCommunityModule]);",
            _PDB_COV_RENDERER_JS,
        ]


# AG Grid 셀 렌더러 (raw JS) — params.data 의 dom_segs / cov_left / cov_width 를 읽어 막대 그림.
# ⚠️ ag-grid-react(React 버전)는 cellRenderer 반환값을 **React 노드**로 기대한다.
#    DOM 엘리먼트(document.createElement)를 반환하면 "Objects are not valid as a React child"
#    런타임 에러가 난다 → React.createElement 로 React 엘리먼트를 만들어 반환해야 한다.
_PDB_COV_RENDERER_JS = """
function pdbCovRenderer(params) {
  var d = params.data || {};
  var children = [];
  var segs = d.dom_segs || [];
  for (var i = 0; i < segs.length; i++) {
    var s = segs[i];
    children.push(createElement('div', {
      key: 'dom' + i,
      title: s.label || '',
      style: {
        position: 'absolute', top: 0, height: '16px',
        left: s.left, width: s.width,
        background: s.color, opacity: 0.30, borderRadius: '2px'
      }
    }));
  }
  if (d.cov_width) {
    children.push(createElement('div', {
      key: 'cov',
      title: (d.structure_id || '') + ': ' + (d.residue_range || ''),
      style: {
        position: 'absolute', top: '3px', height: '10px',
        left: d.cov_left, width: d.cov_width,
        background: 'var(--accent-9, #6b7cff)', borderRadius: '2px',
        boxShadow: '0 0 0 1px rgba(0,0,0,0.25)'
      }
    }));
  }
  return createElement('div', {
    style: {
      position: 'relative', width: '100%', height: '16px', marginTop: '13px',
      background: 'rgba(255,255,255,0.06)', borderRadius: '3px'
    }
  }, children);
}
"""


ag_grid = AgGrid.create
