"""전역 심볼 인덱스(11d 6a) — 레포 전체를 2-pass로 훑어 심볼과 함수→함수 엣지를 만든다.

여기 레포는 전부 지어낸 것이다. 사내 모양(Protocol 포트·Depends 제공자·생성자 주입·콜백 등록·레지스트리)을
흉내내되 이름은 다르다.
"""
import json

import pytest

from src.knowledge import index as ix
from src.knowledge.flow import Name

REPO = "svc"

FILES = {
    "app/__init__.py": "",
    "app/util.py": (
        'def now():\n'
        '    return 1\n'
        '\n'
        'def helper(x):\n'
        '    return now() + x\n'
        '\n'
        'def unused():\n'
        '    return len([1])\n'),
    "app/ports.py": (
        'from typing import Protocol\n'
        '\n'
        'class AlarmRepository(Protocol):\n'
        '    def find(self, q) -> list: ...\n'
        '    def count(self, q) -> int: ...\n'
        '    def purge(self) -> None: ...\n'
        '\n'
        'class Notifier(Protocol):\n'
        '    def send(self, msg) -> None: ...\n'),
    "app/infra/alarm_repository.py": (
        'class AlarmRepository:\n'
        '    def find(self, q):\n'
        '        return mongo[cfg["mongodb_collection"]["alarm"]].find(q)\n'
        '\n'
        '    def count(self, q):\n'
        '        return mongo[cfg["mongodb_collection"]["alarm"]].count_documents(q)\n'
        '\n'
        '    def save(self, doc):\n'
        '        return mongo[cfg["mongodb_collection"]["alarm"]].insert_one(doc)\n'),
    "app/other/alarm_repository.py": (
        'class AlarmRepository:\n'
        '    """다른 도메인의 동명 클래스 — 포트와 모듈 경로가 덜 겹친다."""\n'
        '    def find(self, q):\n'
        '        return []\n'),
    "app/services/alarm.py": (
        'from app.ports import AlarmRepository, Notifier\n'
        'from app.util import helper, now as clock\n'
        'from app import util\n'
        'import app.util\n'
        '\n'
        'class Base:\n'
        '    def run(self):\n'
        '        return self.step()\n'
        '\n'
        '    def common(self):\n'
        '        return 1\n'
        '\n'
        'class AlarmService(Base):\n'
        '    def __init__(self, repo: AlarmRepository, notifier):\n'
        '        self.repo = repo\n'
        '        self.notifier = notifier\n'
        '        self.cache = Cache()\n'
        '\n'
        '    def step(self):\n'
        '        rows = self.repo.find({})\n'
        '        self.notifier.send(rows)\n'
        '        self.cache.put(rows)\n'
        '        self.common()\n'
        '        return helper(clock()) + util.now() + app.util.helper(1) + len(rows)\n'
        '\n'
        '    def tick(self):\n'
        '        c = Cache()\n'
        '        return c.get_all()\n'
        '\n'
        'class Cache:\n'
        '    def put(self, rows):\n'
        '        return rows\n'
        '\n'
        '    def get_all(self):\n'
        '        return []\n'),
    "app/wiring.py": (
        'from typing import Annotated, Optional\n'
        'from fastapi import Depends\n'
        'from app.services.alarm import AlarmService\n'
        'from app.infra.alarm_repository import AlarmRepository as MongoAlarmRepository\n'
        'from app.mail import MailNotifier\n'
        '\n'
        'def get_service() -> AlarmService:\n'
        '    return AlarmService(MongoAlarmRepository(), MailNotifier())\n'
        '\n'
        'ServiceDep = Annotated[AlarmService, Depends(get_service)]\n'
        'MaybeService = Optional[AlarmService]\n'
        '\n'
        'def handle(service: ServiceDep):\n'
        '    return service.step()\n'
        '\n'
        'def handle_maybe(service: MaybeService):\n'
        '    return service.tick()\n'
        '\n'
        'def handle_str(service: "AlarmService"):\n'
        '    return service.tick()\n'
        '\n'
        'def handle_many(services: list[AlarmService]):\n'
        '    return services.step()\n'),
    "app/mail.py": (
        'class MailNotifier:\n'
        '    def send(self, msg):\n'
        '        return producer.send(cfg["infra"]["kafka"]["producer"]["topic"]["topic1"], msg)\n'),
    "app/consumer.py": (
        'from app.services.alarm import AlarmService\n'
        '\n'
        'class Worker:\n'
        '    def __init__(self, service: AlarmService):\n'
        '        self.service = service\n'
        '\n'
        '    def start(self, consumer):\n'
        '        consumer.add_handler("t", self.on_message)\n'
        '        consumer.add_handler("u", handler=self.service.tick)\n'
        '        consumer.add_handler("v", 42)\n'
        '\n'
        '    def on_message(self, msg):\n'
        '        return self.service.step()\n'),
    "app/plugins.py": (
        'class Shape:\n'
        '    def area(self):\n'
        '        return self.compute()\n'
        '\n'
        '    def __repr__(self):\n'
        '        return "Shape"\n'
        '\n'
        'class Square(Shape):\n'
        '    def compute(self):\n'
        '        return 4\n'
        '\n'
        '    def __repr__(self):\n'
        '        return "Square"\n'
        '\n'
        'class Circle(Shape):\n'
        '    def compute(self):\n'
        '        return 3\n'
        '\n'
        '    def area(self):\n'
        '        return 9\n'
        '\n'
        'class Round(Circle):\n'
        '    def area(self):\n'
        '        return 1\n'
        '\n'
        'class Loose:\n'
        '    def compute(self):\n'
        '        return 0\n'),
    "app/noisy.py": (
        'class A:\n    def get(self):\n        return 1\n'
        'class B:\n    def get(self):\n        return 2\n'
        'class C:\n    def rare_op(self):\n        return 3\n'
        'class D:\n    def rare_op(self):\n        return 4\n'
        '\n'
        'def use(x, y):\n'
        '    x.get()\n'
        '    y.rare_op()\n'),
    "app/broken.py": 'def broken(:\n    pass\n',
    "tests/test_alarm.py": 'def test_x():\n    return 1\n',
}
# 흔한 이름 상한(12)을 넘기는 후보 — `def many(` 열세 개.
FILES["app/crowd.py"] = "".join(f"class K{i}:\n    def many(self):\n        return {i}\n\n" for i in range(13)) + \
    "def caller(z):\n    return z.many()\n"

NAMES = [Name("collection", "alarm_events", "mongodb_collection.alarm"),
         Name("topic", "mx.alarm.main", "infra.kafka.producer.topic.topic1")]


class _Src:
    def __init__(self, files=FILES):
        self.files_ = dict(files)

    async def files(self):
        return sorted(self.files_)

    async def read(self, path):
        return self.files_.get(path)


async def _index(files=FILES, names=NAMES):
    return await ix.build_index({REPO: _Src(files)}, names=names, commits={REPO: "c0ffee"})


def _sid(idx, qualname, repo=REPO):
    return idx.lookup(repo, qualname)


def _edges(idx, src_qual, type_=None):
    src = _sid(idx, src_qual)
    return {(idx.symbols[e.dst].qualname, e.type, e.certainty) for e in idx.edges
            if e.src == src and (type_ is None or e.type == type_)}


# ── 심볼 ───────────────────────────────────────────────────────────────

async def test_모든_py가_모듈_심볼이_되고_파싱_실패도_모듈로_세며_id는_배열_위치다():
    """커버리지 `.py 수 == module 수`가 성립하려면 깨진 파일도 모듈이어야 한다 — 빼면 794/794를 말할 수 없다."""
    idx = await _index()
    modules = [s for s in idx.symbols if s.kind == "module"]
    assert len(modules) == sum(ix.is_indexed(p) for p in FILES) == len(FILES) - 1
    assert all(s.id == i for i, s in enumerate(idx.symbols))
    broken = idx.symbols[_sid(idx, "app.broken")]
    assert broken.kind == "module" and broken.parse_error
    assert _sid(idx, "tests.test_alarm") is None                        # 테스트 파일은 입구에서 건너뛴다


async def test_키는_레포와_qualname이고_메서드는_class_id로_클래스를_가리킨다():
    idx = await _index()
    step = idx.symbols[_sid(idx, "app.services.alarm.AlarmService.step")]
    assert step.kind == "method" and idx.symbols[step.class_id].qualname == "app.services.alarm.AlarmService"
    assert (step.file, step.line) == ("app/services/alarm.py", 19) and step.end_line >= step.line + 5
    assert idx.key(step.id) == (REPO, "app.services.alarm.AlarmService.step")
    assert len({idx.key(s.id) for s in idx.symbols}) == len(idx.symbols)


# ── 호출 해석 (a)~(e) ───────────────────────────────────────────────────

async def test_단일_이름_호출은_모듈_정의와_import_별칭으로_풀고_builtin은_버린다():
    """`helper(clock())` — helper는 from-import, clock은 `now as clock` 별칭, len은 builtin."""
    idx = await _index()
    got = _edges(idx, "app.services.alarm.AlarmService.step", "calls")
    assert ("app.util.helper", "calls", "exact") in got and ("app.util.now", "calls", "exact") in got
    assert not any(q.endswith(".len") or q == "len" for q, _, _ in got)
    assert ("app.util.now", "calls", "exact") in _edges(idx, "app.util.helper")


async def test_self_호출은_조상에서_찾고_못_찾으면_하위_클래스로만_좁힌다():
    """`Base.run`의 `self.step()`은 Base에 없다 — 템플릿 메서드다. 레포 전체의 `step`이 아니라 **하위 클래스**의
    step만 후보(restricted)고, `self.common()`은 조상에서 찾아 확실이다."""
    idx = await _index()
    run = _edges(idx, "app.services.alarm.Base.run")
    assert run == {("app.services.alarm.AlarmService.step", "calls", "candidate")}
    assert ("app.services.alarm.Base.common", "calls", "exact") in _edges(idx, "app.services.alarm.AlarmService.step")
    area = _edges(idx, "app.plugins.Shape.area")
    assert {q for q, _, _ in area} == {"app.plugins.Square.compute", "app.plugins.Circle.compute"}
    assert all(c == "candidate" for _, _, c in area)        # Loose.compute는 하위 클래스가 아니라 빠진다


async def test_속성_경유_호출은_타입_테이블로_수신_클래스를_확정한다():
    """파라미터 힌트(repo: AlarmRepository → 포트) · 생성자 대입(self.cache = Cache()) · 지역 변수(c = Cache())."""
    idx = await _index()
    step = _edges(idx, "app.services.alarm.AlarmService.step")
    assert ("app.ports.AlarmRepository.find", "calls", "exact") in step
    assert ("app.services.alarm.Cache.put", "calls", "exact") in step
    assert ("app.services.alarm.Cache.get_all", "calls", "exact") in _edges(idx, "app.services.alarm.AlarmService.tick")


async def test_생성_지점이_하나면_힌트_없는_주입도_확실이고_그_타입으로_푼다():
    """`self.notifier = notifier`에 힌트가 없다. 생성 호출 `AlarmService(MongoAlarmRepository(), MailNotifier())`가
    레포에 하나라 notifier는 MailNotifier다 — 생성자 주입 레포에서 속성 타입이 잡히는 유일한 길."""
    idx = await _index()
    step = _edges(idx, "app.services.alarm.AlarmService.step")
    assert ("app.mail.MailNotifier.send", "calls", "exact") in step
    assert not any(q == "app.ports.Notifier.send" for q, _, _ in step)


async def test_어노테이션은_Optional_Annotated_문자열을_벗기고_컨테이너는_안_벗긴다():
    idx = await _index()
    assert ("app.services.alarm.AlarmService.step", "calls", "exact") in _edges(idx, "app.wiring.handle")
    assert ("app.services.alarm.AlarmService.tick", "calls", "exact") in _edges(idx, "app.wiring.handle_maybe")
    assert ("app.services.alarm.AlarmService.tick", "calls", "exact") in _edges(idx, "app.wiring.handle_str")
    # list[AlarmService]는 원소에 대한 호출이 아니다 — 확정 엣지가 없어야 한다.
    assert not any(c == "exact" for _, _, c in _edges(idx, "app.wiring.handle_many"))


async def test_import_경유_호출은_긴_접두사부터_대조한다():
    """`util.now()`는 `from app import util`, `app.util.helper(1)`은 `import app.util` — 머리 한 글자로는 안 풀린다."""
    idx = await _index()
    step = _edges(idx, "app.services.alarm.AlarmService.step")
    assert ("app.util.now", "calls", "exact") in step and ("app.util.helper", "calls", "exact") in step
    assert ("app.services.alarm.AlarmService.__init__", "calls", "exact") in _edges(idx, "app.wiring.get_service")


async def test_전부_실패하면_같은_이름_메서드에_candidate이되_stoplist와_상한을_지킨다():
    """`x.get()`은 stoplist라 아무것도 안 잇고, `y.rare_op()`은 둘 다 candidate, `z.many()`는 열셋이라 안 잇는다."""
    idx = await _index()
    use = _edges(idx, "app.noisy.use")
    assert use == {("app.noisy.C.rare_op", "calls", "candidate"), ("app.noisy.D.rare_op", "calls", "candidate")}
    assert _edges(idx, "app.crowd.caller") == set()
    assert idx.unresolved["stoplist"] >= 1 and idx.unresolved["too_many"] >= 1


async def test_인자로_넘긴_함수는_calls_엣지다_확정_해석만():
    """`consumer.add_handler("t", self.on_message)` — 등록처에서 실행된다. 숫자 인자는 아무것도 아니다."""
    idx = await _index()
    start = _edges(idx, "app.consumer.Worker.start")
    assert ("app.consumer.Worker.on_message", "calls", "exact") in start
    assert ("app.services.alarm.AlarmService.tick", "calls", "exact") in start
    assert all(e.via == "callback" for e in idx.edges
               if e.src == _sid(idx, "app.consumer.Worker.start") and e.type == "calls")


async def test_상속과_재정의_엣지_던더는_제외():
    idx = await _index()
    assert ("app.plugins.Shape", "inherits", "exact") in _edges(idx, "app.plugins.Square")
    assert ("app.plugins.Shape.area", "overrides", "exact") in _edges(idx, "app.plugins.Circle.area")
    assert not any(t == "overrides" for _, t, _ in _edges(idx, "app.plugins.Square.__repr__"))
    # 가장 가까운 조상 하나만 — 조상 전부에 걸면 베이스 메서드 40 × 구현 30이 1200 엣지가 된다(사내 3741).
    assert _edges(idx, "app.plugins.Round.area", "overrides") == {("app.plugins.Circle.area", "overrides", "exact")}


async def test_동명_클래스는_메서드_단위로_implements이고_없는_메서드는_엣지가_없으며_경로가_가까운_쪽이다():
    """포트 `app.ports.AlarmRepository`의 동명 클래스가 둘 — infra 쪽이 경로 접두사를 더 길게 공유… 하지는 않는다
    (둘 다 `app.`). 그러면 **메서드를 더 많이 갖춘 쪽**, 그래도 같으면 추정이다. purge는 아무도 없어 엣지가 없다."""
    idx = await _index()
    infra_find = _edges(idx, "app.infra.alarm_repository.AlarmRepository.find", "implements")
    assert infra_find == {("app.ports.AlarmRepository.find", "implements", "exact")}
    assert _edges(idx, "app.infra.alarm_repository.AlarmRepository.count", "implements") == {
        ("app.ports.AlarmRepository.count", "implements", "exact")}
    assert _edges(idx, "app.other.alarm_repository.AlarmRepository.find", "implements") == set()
    assert not any(idx.symbols[e.dst].qualname == "app.ports.AlarmRepository.purge" for e in idx.edges)
    assert any("purge" in g for g in idx.gaps)


async def test_같은_엣지는_candidate에서_exact로만_승격한다():
    idx = await _index()
    idx.add_edge(0, 1, "calls", "candidate", line=1)
    idx.add_edge(0, 1, "calls", "exact", line=2)
    idx.add_edge(0, 1, "calls", "candidate", line=3)
    hits = [e for e in idx.edges if e.src == 0 and e.dst == 1 and e.type == "calls"]
    assert len(hits) == 1 and hits[0].certainty == "exact" and hits[0].count == 3
    # 추정이 두 번이라고 확실이 되지는 않는다 — 같은 추측을 반복한 것이지 근거가 는 것이 아니다.
    idx.add_edge(2, 3, "calls", "candidate", line=1)
    idx.add_edge(2, 3, "calls", "candidate", line=2)
    twice = [e for e in idx.edges if e.src == 2 and e.dst == 3 and e.type == "calls"]
    assert len(twice) == 1 and twice[0].certainty == "candidate" and twice[0].count == 2


async def test_함수마다_자원_참조를_방향과_함께_단다():
    idx = await _index()
    save = idx.symbols[_sid(idx, "app.infra.alarm_repository.AlarmRepository.save")]
    assert ("collection", "alarm_events", "writes") in {(r.kind, r.name, r.direction) for r in save.resources}
    find = idx.symbols[_sid(idx, "app.infra.alarm_repository.AlarmRepository.find")]
    assert ("collection", "alarm_events", "reads") in {(r.kind, r.name, r.direction) for r in find.resources}
    send = idx.symbols[_sid(idx, "app.mail.MailNotifier.send")]
    assert ("topic", "mx.alarm.main", "writes") in {(r.kind, r.name, r.direction) for r in send.resources}
    assert idx.writers("collection", "alarm_events") == [save.id]


async def test_Depends_제공자의_반환_클래스가_수신_타입이다():
    """`Annotated[AlarmService, Depends(get_service)]` — 제공자가 돌려주는 것이 진짜 타입이다(포트 뒤의 구현체)."""
    files = dict(FILES)
    files["app/wiring2.py"] = (
        'from typing import Annotated\n'
        'from fastapi import Depends\n'
        'from app.ports import Notifier\n'
        'from app.mail import MailNotifier\n'
        '\n'
        'def get_notifier() -> Notifier:\n'
        '    return MailNotifier()\n'
        '\n'
        'NotifierDep = Annotated[Notifier, Depends(get_notifier)]\n'
        '\n'
        'def ping(n: NotifierDep):\n'
        '    return n.send("x")\n')
    idx = await _index(files)
    assert ("app.mail.MailNotifier.send", "calls", "exact") in _edges(idx, "app.wiring2.ping")


async def test_json으로_내보내고_다시_읽어도_같고_요약이_숫자를_준다():
    idx = await _index()
    again = ix.Index.from_dict(json.loads(json.dumps(idx.to_dict())))
    assert [s.qualname for s in again.symbols] == [s.qualname for s in idx.symbols]
    assert len(again.edges) == len(idx.edges) and again.lookup(REPO, "app.util.now") == idx.lookup(REPO, "app.util.now")
    s = idx.summary()
    assert s["symbols"] == len(idx.symbols) and s["modules"] == len(FILES) - 1 and s["parse_errors"] == 1
    assert s["edges"]["calls"]["exact"] > 0 and s["edges"]["calls"]["candidate"] > 0


# ── 실제 코드(`src/`)를 돌려 보고 드러난 것들 ─────────────────────────────

SHADOW = {
    "pkg/__init__.py": "",
    "pkg/tools.py": "def read_bundle():\n    return 1\n\n\ndef clock():\n    return 2\n",
    "pkg/use.py": (
        "from pathlib import Path\n"
        "from pkg.tools import clock\n\n\n"
        "def lazy():\n"
        "    from pkg import tools as gb\n"
        "    return gb.read_bundle()\n\n\n"
        "def param_call(clock):\n"
        "    return clock()\n\n\n"
        "def local_alias():\n"
        "    f = clock\n"
        "    return f()\n\n\n"
        "def external_only():\n"
        "    p = Path('x')\n"
        "    return Path.home(), p.read_text()\n\n\n"
        "class Ticker:\n"
        "    def __init__(self, clock):\n"
        "        self._clock = clock\n\n"
        "    def tick(self):\n"
        "        return self._clock()\n\n\n"
        "def spin(a):\n"
        "    a = a.next()\n"
        "    return a.next()\n"
    ),
}


async def test_함수_안_import도_해석하고_from_import는_정의한_모듈로_imports_엣지를_건다():
    idx = await _index(SHADOW, names=[])
    assert ("pkg.tools.read_bundle", "calls", "exact") in _edges(idx, "pkg.use.lazy")
    assert ("pkg.tools", "imports", "exact") in _edges(idx, "pkg.use", "imports")


async def test_파라미터나_지역변수_호출은_이름이_같은_함수에_안_잇고_별칭_지역은_확정으로_푼다():
    idx = await _index(SHADOW, names=[])
    assert _edges(idx, "pkg.use.param_call", "calls") == set()            # clock 파라미터 ≠ pkg.tools.clock
    assert ("pkg.tools.clock", "calls", "exact") in _edges(idx, "pkg.use.local_alias")
    assert idx.unresolved.get("variable_call", 0) >= 1


async def test_서드파티_호출과_호출_가능한_필드는_따로_세고_후보를_안_만든다():
    idx = await _index(SHADOW, names=[])
    assert _edges(idx, "pkg.use.external_only", "calls") == set()
    assert idx.unresolved.get("external", 0) >= 2                          # Path(...)·Path.home()
    assert _edges(idx, "pkg.use.Ticker.tick", "calls") == set()
    assert idx.unresolved.get("field_call", 0) == 1 and idx.unresolved.get("method_missing", 0) == 0


async def test_자기_자신으로_도는_대입은_타입_추론을_끊고_인덱스는_끝난다():
    """`a = a.next()` — a의 타입을 알려면 a.next()의 반환 타입이, 그걸 알려면 a의 타입이 필요하다. 실제 코드에서
    RecursionError로 드러났다 — 재귀 전체가 hops 한 계수를 나눠 써야 끝난다."""
    idx = await _index(SHADOW, names=[])
    spin = _sid(idx, "pkg.use.spin")
    assert spin is not None and all(e.certainty == "candidate" for e in idx.edges if e.src == spin)


async def test_테스트_파일은_입구에서_건너뛰어_심볼도_엣지도_안_만든다():
    """사내 첫 `code check`에서 정밀도 표본의 틀린 넷이 전부 테스트 파일이었다 — 테스트가 어떤 함수를 부르는지는
    조사의 "누가 부르나"에 답이 아니다. 후보 풀에서만 빼던 것을 아예 안 읽는다."""
    files = {
        "pkg/__init__.py": "",
        "pkg/a.py": "def f():\n    return 1\n",
        "tests/test_a.py": "from pkg.a import f\n\n\ndef test_f():\n    return f()\n",
        "pkg/test_b.py": "from pkg.a import f\n\n\ndef test_b():\n    return f()\n",
        "pkg/c_test.py": "def c():\n    return 1\n",
        "pkg/conftest.py": "def fixture():\n    return 1\n",
        "pkg/test/helpers.py": "def h():\n    return 1\n",
    }
    idx = await _index(files, names=[])
    assert {s.qualname for s in idx.symbols if s.kind == "module"} == {"pkg", "pkg.a"}
    assert not any(e.type == "calls" for e in idx.edges)
    assert [p for p in files if not ix.is_indexed(p)] == [
        "tests/test_a.py", "pkg/test_b.py", "pkg/c_test.py", "pkg/conftest.py", "pkg/test/helpers.py"]


# ── 미해석 진단(6b-0) — 못 푼 호출이 어떤 모양인지 ────────────────────────

SHARED = {
    ".gitmodules": '[submodule "shared_lib"]\n\tpath = shared_lib\n\turl = ../shared-lib.git\n',
    "pkg/__init__.py": "",
    "pkg/svc.py": (
        "import httpx\n"
        "from shared_lib.store import Store\n"
        "from shared_lib import util\n\n\n"
        "class Svc:\n"
        "    def __init__(self, repo, client):\n"
        "        self.repo = repo\n"
        "        self.client = httpx.Client()\n"
        "        self.store = Store()\n\n"
        "    def run(self, db, store: Store):\n"
        "        resp = httpx.get('x')\n"
        "        self.repo.find_all()\n"
        "        self.client.post('y')\n"
        "        self.store.save()\n"
        "        db.run_query('q')\n"
        "        store.save()\n"
        "        resp.raise_for_status()\n"
        "        util.helper()\n"
        "        return httpx.Timeout(3)\n"
    ),
    "pkg/many.py": "".join(f"class M{i}:\n    def fetch(self):\n        return {i}\n\n\n" for i in range(13))
                   + "def use(x):\n    return x.fetch()\n",
}


async def test_미해석_호출은_수신자_모양별로_세어_6b가_무엇을_먼저_지을지_숫자로_정한다():
    """사내 두 번째 숫자: too_many 823 · unknown 1009 · external 2228. 이게 `self.attr` 주입인지, 힌트 없는
    파라미터인지, 서드파티 객체인지, 아직 인덱스에 없는 **공유 라이브러리**(서브모듈)인지에 따라 다음 커밋이
    다르다 — 모양을 안 보고 지으면 사내 코드가 아니라 상상 속 코드에 짓는 것이다."""
    idx = await _index(SHARED, names=[])
    assert idx.shared_prefixes == {REPO: ["shared_lib"]}
    sh = idx.unresolved_shapes
    assert sh["external_shared"] == {"shared_lib": 2}                   # Store()·util.helper()
    assert sh["external_third"] == {"httpx": 3}                          # Client()·get()·Timeout()
    assert sh["self_attr_param"] == {"repo": 1} and sh["self_attr_call"] == {"client": 1}
    assert sh["self_attr_shared"] == {"store": 1}
    assert sh["param"] == {"db": 1, "x": 1} and sh["param_shared"] == {"store": 1}
    assert sh["local_external"] == {"resp": 1}
    assert sh["too_many_method"] == {"fetch": 1}
    assert "stoplist" not in sh and "builtin" not in sh                  # 자명한 것은 모양을 안 센다
    again = ix.Index.from_dict(json.loads(json.dumps(idx.to_dict())))
    assert again.unresolved_shapes == sh and again.shared_prefixes == idx.shared_prefixes
