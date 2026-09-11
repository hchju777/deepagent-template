"""집계 쪽 fixture를 그대로 빌려 쓴다.

pytest의 fixture는 디렉터리를 넘지 않으므로 여기서 다시 노출시킨다. **복사하지
않는 이유**: 같은 창·같은 문서 모양을 두 벌 정의하면, 한쪽을 고칠 때 렌더링
테스트가 집계 테스트와 다른 데이터를 보면서 둘 다 초록이 된다.
"""
from ..report.conftest import clock, source, window       # noqa: F401
