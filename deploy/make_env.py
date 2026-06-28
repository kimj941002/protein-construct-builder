# deploy/make_env.py
# .streamlit/secrets.toml 의 값을 읽어 배포용 prod.env 파일을 만든다.
# (reflex deploy --envfile prod.env 로 클라우드에 환경변수 전달)
# prod.env 는 .gitignore(*.env)로 보호되어 깃에 올라가지 않는다.
import pathlib

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

ROOT = pathlib.Path(__file__).resolve().parent.parent
secrets = tomllib.loads((ROOT / ".streamlit" / "secrets.toml").read_text(encoding="utf-8"))

KEYS = [
    "APP_PASSWORD",
    "SUPABASE_DB_PASSWORD",
    "SUPABASE_PROJECT_REF",
    "SUPABASE_POOLER_HOST",
    "ANTHROPIC_API_KEY",
]

lines = []
missing = []
for k in KEYS:
    v = str(secrets.get(k, "") or "")
    if v:
        lines.append(f"{k}={v}")
    else:
        missing.append(k)

out = ROOT / "prod.env"
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"[OK] prod.env 생성 ({len(lines)}개 값).  ->  reflex deploy --envfile prod.env")
if missing:
    print(f"[주의] secrets.toml 에 비어있는 값: {missing}  (특히 APP_PASSWORD 는 배포 시 꼭 채우세요)")
