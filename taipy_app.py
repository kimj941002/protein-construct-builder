# taipy_app.py
import pandas as pd
from taipy.gui import Gui, State, notify

# 기존 백엔드 모듈 (수정 없이 그대로 임포트!)
from uniprot_fetcher import fetch_protein
from pdb_fetcher import fetch_all_structures
from database import get_structures_by_uniprot
from utils import create_cached_session

# ─────────────────────────────────────────────
# 1. 상태(State) 변수 초기화
# Streamlit의 st.session_state를 대체합니다.
# 여기에 선언된 변수들은 Taipy가 자동으로 추적하여 UI와 동기화합니다.
# ─────────────────────────────────────────────
search_query = ""
protein_info_text = "위 검색창에 단백질 이름을 입력하고 검색 버튼을 누르세요."
is_loading = False

# Ag-Grid를 대체할 빈 데이터프레임 세팅
pdb_df = pd.DataFrame(columns=[
    "PDB ID", "Method", "Res (Å)", "Complex", "Chain", "Residue Range"
])


# ─────────────────────────────────────────────
# 2. 이벤트 핸들러 (콜백 함수)
# 버튼 클릭 등의 액션이 발생할 때만 실행됩니다. (전체 코드 재실행 X)
# ─────────────────────────────────────────────
def on_search_click(state: State):
    if not state.search_query.strip():
        notify(state, "W", "단백질 이름을 입력해주세요.") # Taipy의 Toast 알림
        return

    # 로딩 상태 켜기 (버튼 비활성화)
    state.is_loading = True
    state.protein_info_text = f"'{state.search_query}' 검색 중..."
    
    try:
        session = create_cached_session()
        
        # Step 1: UniProt 정보 수집 (기존 함수 재사용)
        protein_data, pdb_ids, message = fetch_protein(state.search_query, session=session)
        
        if not protein_data:
            notify(state, "E", f"검색 실패: {message}")
            state.protein_info_text = "단백질을 찾을 수 없습니다."
            return

        uniprot_id = protein_data["uniprot_id"]
        gene_name = protein_data["gene_name"]
        seq_len = protein_data['sequence_length']
        
        # UI 텍스트 업데이트
        state.protein_info_text = f"**UniProt ID:** {uniprot_id} | **Gene:** {gene_name} | **Length:** {seq_len} aa"

        # Step 2: PDB 구조 수집 (기존 함수 재사용)
        notify(state, "I", f"PDB 구조 수집 중... (총 {len(pdb_ids)}개)")
        fetch_all_structures(pdb_ids, uniprot_id)

        # Step 3: DB에서 불러와 DataFrame 업데이트
        structures = get_structures_by_uniprot(uniprot_id)
        if structures:
            rows = [{
                "PDB ID": s["structure_id"],
                "Method": s["method"],
                "Res (Å)": s["resolution"],
                "Complex": s["complex_type"],
                "Chain": s["chain_id"],
                "Residue Range": s["residue_range"]
            } for s in structures]
            
            # DataFrame 변수가 업데이트되면 UI의 테이블이 '부분적'으로만 새로고침 됨
            state.pdb_df = pd.DataFrame(rows)
            notify(state, "S", "데이터 수집 및 업데이트 완료!")
        else:
            state.pdb_df = pd.DataFrame()
            notify(state, "W", "수집된 구조가 없습니다.")

    except Exception as e:
        notify(state, "E", f"오류 발생: {str(e)}")
    finally:
        # 로딩 상태 끄기
        state.is_loading = False


# ─────────────────────────────────────────────
# 3. 마크다운(Markdown) 기반 UI 구성
# Streamlit의 레이아웃 함수들을 대체합니다.
# <|변수명|컴포넌트|속성|> 형태로 작성합니다.
# ─────────────────────────────────────────────
page_layout = """
# 🧬 Protein Construct Builder *(Taipy Version)*

<|layout|columns=4 1|
<|{search_query}|input|label=단백질 이름 입력 (예: cMET)|class_name=full-width|>

<|🔍 검색|button|on_action=on_search_click|active={not is_loading}|class_name=full-width|>
|>

<br/>
<|{protein_info_text}|text|mode=markdown|>

---
### 📊 PDB 구조 목록
<|{pdb_df}|table|page_size=10|filter=True|sort=True|>
"""

# ─────────────────────────────────────────────
# 4. 앱 실행
# ─────────────────────────────────────────────
if __name__ == "__main__":
    Gui(page=page_layout).run(
        title="Protein Builder", 
        dark_mode=True, 
        use_reloader=True, # 코드 수정 시 자동 새로고침 (Streamlit과 유사)
        port=5000
    )