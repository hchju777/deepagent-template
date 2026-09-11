"""픽스처는 디렉터리를 넘지 않으므로 여기서 다시 노출시킨다.

정의는 `tests/support.py` 한 곳에만 있다 — 두 벌로 두면 한쪽을 고칠 때
다른 디렉터리의 테스트가 다른 데이터를 보면서 둘 다 초록이 된다.

**절대 import여야 한다.** 상대 import(`from ..support import`)는
`tests/__init__.py`가 없는 환경에서 수집 자체를 실패시킨다 —
`tests/support.py` 첫머리 참고.
"""
from tests.support import clock, source, window       # noqa: F401
