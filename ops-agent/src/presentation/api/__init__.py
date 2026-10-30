"""운영 API — 컨테이너 하나에서 uvicorn으로 뜬다(`app.py`).

운영용(`/internal/v1/ops/...`)은 리포트·메일·작업·출력, 진단용(`/internal/v1/ops/diag/...`)은 doctor·네트워크·
LLM·env. CLI로 이미 있는 일은 작업 실행기(`jobs.py`)가 하위 프로세스로 그 명령을 그대로 부르고, CLI에 없는
진단(네트워크 단계·egress·trace·env 모양·LLM 원시 호출)만 앱 안에서 한다.
"""
