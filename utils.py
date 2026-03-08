# utils.py
# API 호출 유틸리티 함수 모음
# 네트워크 오류, 속도 제한, 타임아웃 등 모든 케이스를 처리합니다.

from __future__ import annotations
import time
import requests
from requests.exceptions import ConnectionError, Timeout
from config import MAX_RETRIES, REQUEST_TIMEOUT


def api_call_with_retry(url: str, params: dict = None, max_retries: int = MAX_RETRIES,
                        session=None) -> dict | None:
    """
    URL에 GET 요청을 보내고 JSON 응답을 반환합니다.
    오류 발생 시 자동으로 재시도합니다.

    Parameters:
        url (str): 요청할 API URL
        params (dict): URL 쿼리 파라미터 (선택사항)
        max_retries (int): 최대 재시도 횟수 (기본값: 3)
        session: requests.Session 또는 CachedSession 객체
                 None이면 새 Session을 만들어 사용합니다.

    Returns:
        dict: API 응답 JSON 데이터
        None: 404 (데이터 없음) 또는 모든 재시도 실패 시

    처리하는 HTTP 상태코드:
        200: 정상 응답 → JSON 반환
        404: 데이터 없음 → None 반환 (재시도 없음)
        429: 요청 너무 많음 → 지수 백오프 후 재시도
        500/502/503: 서버 오류 → 일정 시간 후 재시도
        기타: 재시도
    """
    # session이 없으면 기본 requests.Session 사용
    use_session = session if session is not None else requests.Session()

    for attempt in range(max_retries):
        try:
            # API 호출
            response = use_session.get(url, params=params, timeout=REQUEST_TIMEOUT)

            # ──── 상태코드별 처리 ────

            if response.status_code == 200:
                # 정상 응답: JSON 파싱 후 반환
                return response.json()

            elif response.status_code == 404:
                # 데이터 없음: 재시도 없이 None 반환
                return None

            elif response.status_code == 429:
                # 요청 너무 많음 (Rate Limit): 지수 백오프 적용
                # 1번째 실패: 2초 대기, 2번째: 4초, 3번째: 8초
                wait_time = 2 ** (attempt + 1)
                print(f"  [WARN] 429 Rate Limit - {wait_time}초 대기 후 재시도 ({attempt + 1}/{max_retries})")
                time.sleep(wait_time)

            elif response.status_code in (500, 502, 503):
                # 서버 오류: 5초 대기 후 재시도
                wait_time = 5
                print(f"  [WARN] 서버 오류 {response.status_code} - {wait_time}초 대기 후 재시도 ({attempt + 1}/{max_retries})")
                time.sleep(wait_time)

            else:
                # 기타 오류: 3초 대기 후 재시도
                print(f"  [WARN] HTTP {response.status_code} - 3초 대기 후 재시도 ({attempt + 1}/{max_retries})")
                time.sleep(3)

        except Timeout:
            # 타임아웃: 요청 시간 초과
            print(f"  [WARN] Timeout - 5초 대기 후 재시도 ({attempt + 1}/{max_retries}): {url[:60]}...")
            time.sleep(5)

        except ConnectionError:
            # 연결 오류: 인터넷 연결 문제 또는 서버 접근 불가
            print(f"  [WARN] ConnectionError - 10초 대기 후 재시도 ({attempt + 1}/{max_retries}): {url[:60]}...")
            time.sleep(10)

        except Exception as e:
            # 기타 예외: 예상치 못한 오류
            print(f"  [WARN] 예외 발생 ({type(e).__name__}) - 재시도 ({attempt + 1}/{max_retries}): {e}")
            time.sleep(3)

    # 모든 재시도 소진 → None 반환
    print(f"  [ERR] 모든 재시도 실패: {url[:80]}")
    return None


def create_cached_session(cache_name: str = None) -> "requests_cache.CachedSession":
    """
    API 응답을 캐시하는 Session을 생성합니다.
    같은 URL을 다시 요청할 때 캐시에서 바로 반환하므로 빠릅니다.

    Parameters:
        cache_name (str): 캐시 파일 경로 (None이면 config.py의 CACHE_PATH 사용)

    Returns:
        requests_cache.CachedSession 객체
    """
    import requests_cache
    from config import CACHE_PATH, CACHE_EXPIRE

    name = cache_name if cache_name else CACHE_PATH
    session = requests_cache.CachedSession(
        cache_name=name,
        backend="sqlite",
        expire_after=CACHE_EXPIRE
    )
    return session


# ─────────────────────────────────────────────
# 직접 실행 시 기본 동작 테스트
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("utils.py 임포트 테스트...")
    print("  api_call_with_retry 함수 정의됨 [OK]")
    print("  create_cached_session 함수 정의됨 [OK]")

    # 간단한 연결 테스트
    print("\nRCSB API 연결 테스트 중...")
    result = api_call_with_retry("https://data.rcsb.org/rest/v1/core/entry/2WGJ")
    if result:
        print(f"  [OK] RCSB API 응답 성공: {list(result.keys())[:5]}")
    else:
        print("  [ERR] RCSB API 응답 실패")
