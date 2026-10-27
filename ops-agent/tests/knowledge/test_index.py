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


async def test_renamed는_자원_이름만_바꾼_새_인덱스를_준다():
    """번들의 인덱스는 GBM 기준값으로 서 있다(11e-2). 사이트 층이 덮은 값으로 묻는 조사(`code.uses`)가 맞으려면 함수별
    자원 이름을 그 사이트 것으로 바꾼 사본이 필요하다 — 엣지·심볼 id는 그대로고 원본은 안 바뀐다."""
    idx = await _index()
    save = _sid(idx, "app.infra.alarm_repository.AlarmRepository.save")
    got = idx.renamed({("collection", "alarm_events"): "gumi_alarm_events"})
    assert got.writers("collection", "gumi_alarm_events") == [save] and got.writers("collection", "alarm_events") == []
    assert idx.writers("collection", "alarm_events") == [save]                       # 원본 그대로
    assert [e for e in got.edges] == [e for e in idx.edges] and [s.id for s in got.symbols] == [s.id for s in idx.symbols]
    kept = idx.symbols[save]
    assert {(r.direction, r.line, r.via) for r in got.symbols[save].resources} == {(r.direction, r.line, r.via) for r in kept.resources}
    assert idx.renamed({}) is idx and idx.renamed({("topic", "없는이름"): "x"}).writers("collection", "alarm_events") == [save]


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
        "        return httpx.Timeout(3)\n\n\n"
        "class Ext(Store):\n"
        "    def __init__(self):\n"
        "        super().__init__()\n"
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
    assert sh["external_shared"] == {"shared_lib": 3}                   # Store()·util.helper()·super()→Store
    assert sh["external_third"] == {"httpx": 3}                          # Client()·get()·Timeout()
    assert sh["self_attr_param"] == {"repo": 1} and sh["self_attr_call"] == {"client": 1}
    assert sh["self_attr_shared"] == {"store": 1}
    assert sh["param"] == {"db": 1, "x": 1} and sh["param_shared"] == {"store": 1}
    assert sh["local_external"] == {"resp": 1}
    assert sh["too_many_method"] == {"fetch": 1}
    assert "stoplist" not in sh and "builtin" not in sh                  # 자명한 것은 모양을 안 센다
    again = ix.Index.from_dict(json.loads(json.dumps(idx.to_dict())))
    assert again.unresolved_shapes == sh and again.shared_prefixes == idx.shared_prefixes


# ── 6b-1: super()·f().m()·재정의 뿌리로 접기 ────────────────────────────

SUPER = {
    "pkg/__init__.py": "",
    "pkg/base.py": (
        "class Base:\n"
        "    def __init__(self, n):\n"
        "        self.n = n\n\n"
        "    def save(self):\n"
        "        return 0\n\n"
        "    def hello(self):\n"
        "        return 'b'\n"
    ),
    "pkg/mixin.py": "class Mixin:\n    def hello(self):\n        return 'm'\n",
    "pkg/impl.py": (
        "from pkg.base import Base\n"
        "from pkg.mixin import Mixin\n\n\n"
        "class Child(Base):\n"
        "    def __init__(self, n):\n"
        "        super().__init__(n)\n\n"
        "    def save(self):\n"
        "        return super().save() + 1\n\n\n"
        "class Both(Base, Mixin):\n"
        "    def hello(self):\n"
        "        return super().hello()\n\n\n"
        "def make():\n"
        "    return Child(1)\n\n\n"
        "def use():\n"
        "    return make().save()\n\n\n"
        "def anywhere(x):\n"
        "    return x.save()\n"
    ),
    "pkg/stores.py": "from pkg.base import Base\n\n\n" + "".join(
        f"class S{i}(Base):\n    def save(self):\n        return {i}\n\n\n" for i in range(13)),
}


async def test_super_호출은_조상에서_확실로_풀고_조상이_갈리면_그_조상들만_후보다():
    """사내 세 번째 숫자: 체인 머리 `super` 257건 + 버린 `__init__` 85건 — `super().m()`을 `()`가 낀 체인이라
    동명 후보로 떨어뜨리고 있었다. 조상에서 찾으면 확실이다."""
    idx = await _index(SUPER, names=[])
    assert ("pkg.base.Base.__init__", "calls", "exact") in _edges(idx, "pkg.impl.Child.__init__")
    assert ("pkg.base.Base.save", "calls", "exact") in _edges(idx, "pkg.impl.Child.save")
    assert _edges(idx, "pkg.impl.Both.hello", "calls") == {
        ("pkg.base.Base.hello", "calls", "candidate"), ("pkg.mixin.Mixin.hello", "calls", "candidate")}
    assert idx.unresolved.get("too_many", 0) == 0


async def test_함수_호출_결과에_대한_호출은_그_함수의_반환_클래스에서_푼다():
    idx = await _index(SUPER, names=[])
    assert ("pkg.impl.Child.save", "calls", "exact") in _edges(idx, "pkg.impl.use")


async def test_동명_후보가_상한을_넘으면_재정의_뿌리로_접어_베이스_메서드_하나에_candidate다():
    """`storage.save()` — 구현체 30개에 후보 30개를 거는 대신, 전부가 재정의하는 `Base.save` 하나로 접는다.
    `overrides`가 구현체로 이어 주므로 impact 질의는 그대로 된다. 공통 뿌리가 없으면(crowd) 지금처럼 버린다."""
    idx = await _index(SUPER, names=[])
    src = _sid(idx, "pkg.impl.anywhere")
    hits = [e for e in idx.edges if e.src == src and e.type == "calls"]
    assert [(idx.symbols[e.dst].qualname, e.certainty, e.via) for e in hits] == [("pkg.base.Base.save", "candidate", "root")]
    assert idx.unresolved.get("too_many", 0) == 0 and "too_many_method" not in idx.unresolved_shapes


async def test_추정_후보는_부르는_쪽_레포_안에서만_고른다():
    """다른 레포의 함수는 이 프로세스에 없다 — 레포 사이는 HTTP·Kafka로 잇고(흐름 그래프), 공유 라이브러리는 레포마다
    자기 핀으로 들어온다(6b-2). 후보 풀이 레포를 건너면 공유 라이브러리가 레포 수만큼 겹쳐 같은 이름 후보가 다섯 배가
    된다 — 측정판에서 다른 레포의 공유 `now()`가 후보로 잡혀 드러났다."""
    a = {"a/__init__.py": "", "a/m.py": "def tick():\n    return now()\n\n\ndef poke(x):\n    return x.flush()\n"}
    b = {"b/__init__.py": "", "b/k.py": "def now():\n    return 1\n\n\nclass W:\n    def flush(self):\n        return 2\n"}
    idx = await ix.build_index({"ra": _Src(a), "rb": _Src(b)}, names=[], commits={"ra": "c1", "rb": "c2"})
    for q in ("a.m.tick", "a.m.poke"):
        sid = idx.lookup("ra", q)
        assert sid is not None and [e for e in idx.edges if e.src == sid and e.type == "calls"] == []
    assert idx.unresolved.get("unknown", 0) == 2


# ── 6b-2 후속: `import *` 재수출·공유 라이브러리 진단·클로저 변수 ─────────

STAR = {
    ".gitmodules": '[submodule "lib"]\n\tpath = lib\n\turl = ../lib.git\n',
    "lib/__init__.py": "from .core import *\nfrom .hidden import *\n",
    "lib/core.py": ("__all__ = ['Store', 'helper']\n\n\n"
                    "class Store:\n    def save(self):\n        return 1\n\n\n"
                    "def helper():\n    return 2\n\n\n"
                    "def internal():\n    return 3\n"),
    # `from . import *` — 패키지로 되돌아가는 순환. 본 모듈을 다시 열면 안 끝난다.
    "lib/hidden.py": "from . import *\n\n\ndef _private():\n    return 4\n\n\ndef visible():\n    return 5\n",
    "app/__init__.py": "",
    "app/use.py": ("from lib import Store, helper, internal, visible, _private, gone\n\n\n"
                   "def run():\n"
                   "    helper()\n"
                   "    visible()\n"
                   "    Store().save()\n"
                   "    internal()\n"
                   "    _private()\n"
                   "    return gone()\n\n\n"
                   "def deco(func):\n"
                   "    def wrapper(*a):\n"
                   "        return func(*a)\n"
                   "    return wrapper\n"),
    "app/bare.py": "from lib.core import *\n\n\ndef go():\n    return helper()\n",
}


async def test_import_별표로_재수출한_이름을_따라가되_all과_밑줄과_순환을_지킨다():
    """사내 다섯 번째 숫자: 서브모듈이 다섯 레포 모두 채워졌는데 공유 라이브러리 470이 남았다. 공유 라이브러리의
    `__init__.py`가 `from .core import *`로 내보내면 인덱서가 `*`를 버려서 `from <공유> import Store`가 안 풀렸다."""
    idx = await _index(STAR, names=[])
    got = _edges(idx, "app.use.run", "calls")
    assert ("lib.core.helper", "calls", "exact") in got
    assert ("lib.hidden.visible", "calls", "exact") in got
    assert ("lib.core.Store.save", "calls", "exact") in got
    assert not any(q.endswith(("internal", "_private", "gone")) for q, _, _ in got)   # __all__ 밖·밑줄·없는 이름
    assert ("lib.core.helper", "calls", "exact") in _edges(idx, "app.bare.go")          # `import *` 뒤 맨 이름
    sh = idx.unresolved_shapes
    assert sh["shared_unnamed"] == {"lib.internal": 1, "lib._private": 1, "lib.gone": 1}
    assert "shared_absent" not in sh


async def test_공유_라이브러리_미해석은_레포에_안_들어옴과_이름_못_찾음으로_가른다():
    """서브모듈이 안 채워진 레포는 공유 모듈이 하나도 없다 — 그 몫은 `code status`가 고칠 일이고, 모듈은 있는데
    이름을 못 찾은 몫은 인덱서가 고칠 일이다. 한 숫자로 세면 어느 쪽인지 사내에 한 번 더 물어야 한다."""
    absent = {".gitmodules": STAR[".gitmodules"], "app/__init__.py": "",
              "app/x.py": "from lib.core import helper\n\n\ndef go():\n    return helper()\n"}
    idx = await _index(absent, names=[])
    assert idx.unresolved_shapes["shared_absent"] == {REPO: 1}
    assert "shared_unnamed" not in idx.unresolved_shapes


async def test_바깥_함수의_인자를_부르는_클로저는_변수_호출이다():
    """데코레이터의 `wrapper`가 `func(*a)`를 부른다 — 사내 맨 이름 `func` 28건. 무엇이 올지는 실행 시점에 정해지므로
    못 푸는 것이 맞고, 맨 이름(진짜 모르는 것)에 섞이면 진단이 흐려진다."""
    idx = await _index(STAR, names=[])
    assert _edges(idx, "app.use.deco.wrapper", "calls") == set()
    assert "func" not in idx.unresolved_shapes.get("bare_name", {})
    assert idx.unresolved.get("variable_call", 0) >= 1


# ── 6b-2 후속 2: 다른 모듈의 모듈 수준 값(싱글턴) ──────────────────────────

SINGLETON = {
    ".gitmodules": STAR[".gitmodules"],
    "lib/__init__.py": "",
    "lib/log_reexport.py": "from loguru import logger\n",
    "lib/log_bound.py": "from loguru import logger as _base\n\nlogger = _base.bind(app='x')\n",
    "lib/log_custom.py": "class AppLog:\n    def info(self, m):\n        return m\n\n\nlogger = AppLog()\n",
    "lib/clients.py": "import httpx\n\nclient = httpx.Client()\n",
    "app/__init__.py": "",
    "app/use.py": ("from lib.log_reexport import logger\n"
                   "from lib.log_bound import logger as bound\n"
                   "from lib.log_custom import logger as applog\n"
                   "from lib.clients import client\n"
                   "import lib.log_custom\n\n\n"
                   "def run():\n"
                   "    logger.info('a')\n"
                   "    bound.info('b')\n"
                   "    applog.info('c')\n"
                   "    lib.log_custom.logger.info('d')\n"
                   "    return client.get('e')\n"),
}


async def test_다른_모듈의_싱글턴은_정의한_모듈에서_값의_클래스를_정하고_서드파티_값이면_서드파티로_센다():
    """사내 여섯 번째 숫자: 공유 라이브러리 470이 전부 `<공유>.logging.logger.logger` 하나였다 — 공유 쪽 로깅 모듈의
    모듈 수준 변수를 import해 `logger.info()`로 부른다. 인덱서가 다른 모듈의 모듈 수준 변수를 안 따라가서, 그것이
    우리 클래스의 인스턴스면 엣지가 없고 서드파티 객체를 다시 내보낸 것이면 공유 라이브러리로 잘못 셌다."""
    idx = await _index(SINGLETON, names=[])
    assert _edges(idx, "app.use.run", "calls") == {("lib.log_custom.AppLog.info", "calls", "exact")}
    run = _sid(idx, "app.use.run")
    hit = next(e for e in idx.edges if e.src == run and e.type == "calls")
    assert hit.count == 2                                          # `applog.info`와 `lib.log_custom.logger.info` 둘 다
    sh = idx.unresolved_shapes
    assert sh["external_third"] == {"loguru": 2, "httpx": 1}       # 다시 내보낸 것 · bind()로 감싼 것 · 서드파티 클래스
    assert "external_shared" not in sh and "shared_unnamed" not in sh


# ── 6c-1 후속: 이름이 다른 포트와 구현 · `= Depends(공급자)` ─────────────

PROTO = {
    "src/__init__.py": "",
    "src/ports.py": ("from abc import ABC, abstractmethod\nfrom typing import Protocol\n\n\n"
                     "class BadgeServiceProtocol(Protocol):\n    def get_badge(self, line): ...\n\n\n"
                     "class IStore(ABC):\n    @abstractmethod\n    def put(self, x):\n        ...\n\n\n"
                     "class ReportPort(Protocol):\n    def render(self): ...\n\n\n"
                     "class CachePort(ABC):\n    @abstractmethod\n    def fetch(self, k):\n        ...\n"),
    "src/services.py": ("class BadgeService:\n    def get_badge(self, line):\n        return line\n\n\n"
                        "class Store:\n    def put(self, x):\n        return x\n\n\n"
                        "class Report:\n    def unrelated(self):\n        return 0\n\n\n"
                        "from src.ports import CachePort\n\n\n"
                        "class Cache(CachePort):\n    def fetch(self, k):\n        return k\n"),
    "src/deps.py": "from src.services import BadgeService\n\n\ndef get_badge_service():\n    return BadgeService()\n",
    "src/routes.py": (
        "from typing import Annotated\nfrom fastapi import Depends\nfrom src.ports import BadgeServiceProtocol\n"
        "from src.deps import get_badge_service\n\n\n"
        "@router.get('/a')\ndef by_default(svc: BadgeServiceProtocol = Depends(get_badge_service)):\n    return svc.get_badge('L1')\n\n\n"
        "@router.get('/b')\ndef by_annotated(svc: Annotated[BadgeServiceProtocol, Depends(get_badge_service)]):\n"
        "    return svc.get_badge('L1')\n\n\n"
        "class Handler:\n    def __init__(self, svc: BadgeServiceProtocol):\n        self.svc = svc\n\n"
        "    def run(self):\n        return self.svc.get_badge('L1')\n"),
}


async def test_이름_규칙이_다른_포트와_구현을_메서드_단위로_잇고_메서드가_없으면_안_잇는다():
    """`XProtocol`·`IX`·`XPort` → `X`. 사내 presentation이 `XxxServiceProtocol`로 부르고 `XxxService`가 구현인데, 인덱서가
    이름이 **똑같은** 포트와 구현만 이어서 구현 쪽에서 보면 "부르는 곳이 없다"였다(11b 추적기는 이 규칙을 봤다)."""
    idx = await _index(PROTO, names=[])
    got = {(idx.symbols[e.src].qualname, idx.symbols[e.dst].qualname, e.certainty, e.via)
           for e in idx.edges if e.type == "implements"}
    assert ("src.services.BadgeService.get_badge", "src.ports.BadgeServiceProtocol.get_badge", "exact", "name_rule") in got
    assert ("src.services.Store.put", "src.ports.IStore.put", "exact", "name_rule") in got
    assert not any(dst.startswith("src.ports.ReportPort") for _, dst, _, _ in got)   # `Report`는 render가 없다
    assert not any("ReportPort" in g for g in idx.gaps)            # "구현인데 render가 없다"는 거짓 gap도 없다
    # 포트를 직접 상속한 구현은 `overrides`가 이미 잇는다 — 같은 디스패치를 두 번 걸지 않는다.
    assert not any(src.startswith("src.services.Cache.") for src, _, _, _ in got)
    assert ("src.ports.CachePort.fetch", "overrides", "exact") in _edges(idx, "src.services.Cache.fetch", "overrides")


async def test_기본값_자리의_Depends도_공급자가_돌려주는_구현이_수신_타입이다():
    """`svc: XProtocol = Depends(공급자)` — 어노테이션은 포트이고 실제로 들어오는 것은 공급자가 만든 구현이다.
    `Annotated[XProtocol, Depends(공급자)]` 꼴은 이미 그렇게 풀었는데 기본값 자리만 어노테이션이 이겼다."""
    idx = await _index(PROTO, names=[])
    impl = ("src.services.BadgeService.get_badge", "calls", "exact")
    assert impl in _edges(idx, "src.routes.by_default", "calls")
    assert impl in _edges(idx, "src.routes.by_annotated", "calls")
    assert ("src.ports.BadgeServiceProtocol.get_badge", "calls", "exact") in _edges(idx, "src.routes.Handler.run", "calls")


async def test_함수마다_못_푼_호출과_getattr_자리가_심볼에_남고_번들을_오간다():
    """6d-3 — 끝점 사슬을 인덱스에서 만들 때 "못 따라감"(getattr·후보)을 추적기와 같게 내려면 함수별로 남아 있어야
    한다. 집계만 있던 것(6b-0)을 함수에도 적는다."""
    files = {"app/__init__.py": "",
             "app/h.py": ("import json\n\n\ndef h(cfg, box):\n    box.spin()\n    fmt = getattr(formatters, cfg[\"f\"])\n"
                          "    return json.dumps(fmt(1))\n")}
    idx = await ix.build_index({"r": _Src(files)}, commits={"r": "c0ffee"})
    h = idx.symbols[idx.lookup("r", "app.h.h")]
    assert ("box.spin()", "unknown") in {(u[1], u[2]) for u in h.unresolved}
    assert (6, "getattr(formatters, ...)", "getattr") in h.unresolved
    assert ("json.dumps()", "external") in {(u[1], u[2]) for u in h.unresolved}   # 서드파티도 같은 모양으로 남는다
    assert all(u[2] not in ("builtin", "stoplist") for u in h.unresolved)         # `getattr()` 호출 자체는 내장이라 안 남는다
    again = ix.Index.from_dict(json.loads(json.dumps(idx.to_dict())))
    assert again.symbols[h.id].unresolved == h.unresolved


# ── R3-1c ①-a — code.resources: 이 클래스가 읽고 쓰는 자원 ──

RESOURCES_FILES = {**FILES, "app/batch.py": (
    'class Refresh:\n'
    '    def __init__(self, cfg, redis, producer):\n'
    '        self.cfg, self.redis, self.producer = cfg, redis, producer\n'
    '\n'
    '    def _run(self):\n'
    '        src = self.redis.get(self.cfg["redis_key"]["alarm_source"].format(line="L1"))\n'
    '        self.producer.send(self.cfg["infra"]["kafka"]["producer"]["topic"]["topic1"], src)\n'
    '\n'
    '    def _nothing(self):\n'
    '        return 1\n'
    '\n'
    'class Empty:\n'
    '    def go(self):\n'
    '        return 2\n')}
RESOURCES_NAMES = NAMES + [Name("rediskey", "alarm:source:{line}", "redis_key.alarm_source")]


async def test_resources는_클래스의_메서드들이_읽고_쓰는_자원을_나눠_준다():
    """다음 홉의 재료(R3-1c ①-a) — "쓴 쪽이 무엇을 읽나". 읽기가 앞, 메서드와 줄을 같이. 자원이 없는 클래스는 그 사실을 말한다(키를
    조립식으로 만들면 텍스트로는 못 찾는다 — 없는 것이 아니라 못 본 것)."""
    from src.knowledge import query as qy
    idx = await _index(RESOURCES_FILES, RESOURCES_NAMES)
    lines = qy.resources_lines(idx, _sid(idx, "app.batch.Refresh"), where=lambda sid: "[svc]")
    assert lines[0] == "app.batch.Refresh (app/batch.py:L1) [svc]"
    assert lines[1] == "  읽기 1:" and lines[2] == "    alarm:source:{line} [rediskey] — app.batch.Refresh._run (app/batch.py:L6)"
    assert lines[3] == "  쓰기 1:" and lines[4].startswith("    mx.alarm.main [topic] — app.batch.Refresh._run (app/batch.py:L7)")
    empty = qy.resources_lines(idx, _sid(idx, "app.batch.Empty"), where=lambda sid: "")
    assert empty[1] == "  읽거나 쓰는 자원이 인덱스에 없다 — 메서드 1개 (키를 조립식으로 만들면 텍스트로는 못 찾는다)"


async def test_옛_번들의_여섯_칸_자원_튜플도_그대로_읽힌다():
    """R3-1b ②가 자원 튜플에 여섯째 칸(`how`)을 넣어 번들을 만들었고 사내가 그걸로 측정했다(#8). R3-1c ①-b가 그 칸을 걷어냈어도 그 번들은
    읽혀야 한다. R3-5r ①부터 여섯째 칸은 다시 있다(`handoff` — 조립한 값을 넘긴 동사): 동사표의 동사만 받고 그 밖의 값(옛 "command")은 버린다."""
    idx = await _index()
    old = idx.to_dict()
    old["symbols"] = [{**s, "resources": [[*r[:5], "command"] for r in s["resources"]]} for s in old["symbols"]]
    back = ix.Index.from_dict(old)
    assert [s.resources for s in back.symbols] == [s.resources for s in idx.symbols]
    assert all(len(ix.asdict_tuple(r)) == 6 and r.handoff == "" for s in idx.symbols for r in s.resources)
    verbed = {**old, "symbols": [{**s, "resources": [[*r[:5], "send"] for r in s["resources"]]} for s in old["symbols"]]}
    assert all(r.handoff == "send" for s in ix.Index.from_dict(verbed).symbols for r in s.resources)
    five = {**old, "symbols": [{**s, "resources": [r[:5] for r in s["resources"]]} for s in old["symbols"]]}
    assert [s.resources for s in ix.Index.from_dict(five).symbols] == [s.resources for s in idx.symbols]


# ── R3-5f ③ — 클래스 속성 리터럴은 그 클래스의 것 ──

KEYED = {'k/__init__.py': '', 'k/base.py': 'class KeyedJob:\n    def _run(self):\n        data = self.redis.get(self.cfg.get("redis_key", self.redis_key))\n        return self.redis.get(self.mode) if data else None\n', 'k/a.py': 'from k.base import KeyedJob\n\n\nclass A(KeyedJob):\n    redis_key = "k:a"\n    mode = "material"\n\n    def peek(self):\n        return self.redis.get(self.redis_key)\n', 'k/b.py': 'from k.a import A\n\n\nclass B(A):\n    redis_key = "k:b"\n', 'k/c.py': 'from k.base import KeyedJob\n\n\nclass C(KeyedJob):\n    pass\n', 'k/d.py': 'from k.base import KeyedJob\n\n\nclass D(KeyedJob):\n    mode = "k:b"\n', 'k/other.py': 'def redis_key_user(redis_key):\n    return redis_key\n'}
KEYED_NAMES = [Name("rediskey", "k:a", "redis_key.a"), Name("rediskey", "k:b", "redis_key.b")]


async def test_클래스_속성_리터럴은_모듈_별칭이_아니라_self_속성을_읽는_메서드에_가족_클래스마다_붙는다():
    """측정 #16 P0-c: 부모 메서드가 `config.get("redis_key", self.<속성>)`으로 키를 고르는데, 클래스 수준 `redis_key = "<키>"`가 모듈 별칭 표에 들어가
    (식별자당 첫 모듈이 이긴다) 그 식별자가 보이는 모든 함수에 한 하위 클래스의 키가 붙었다 — 다른 하위 클래스에 남의 키가 입력으로 실려 c-10이 `키가 없다`에서
    멈췄다. 클래스 속성은 클래스별로 두고, `self.<속성>`을 읽는 메서드에는 그 속성을 정의한 가족 클래스(자기·조상·후손)마다 `attr:<속성>:<클래스>`로 붙인다
    — 어느 것을 쓸지는 질의가 MRO로 고른다(`query.resources_lines`)."""
    idx = await _index(KEYED, KEYED_NAMES)
    run = idx.symbols[_sid(idx, "k.base.KeyedJob._run")].resources
    assert sorted((r.name, r.direction, r.via) for r in run) == [
        ("k:a", "reads", "attr:redis_key:k.a.A"), ("k:b", "reads", "attr:mode:k.d.D"), ("k:b", "reads", "attr:redis_key:k.b.B")]
    peek = idx.symbols[_sid(idx, "k.a.A.peek")].resources
    assert sorted((r.name, r.via) for r in peek) == [("k:a", "attr:redis_key:k.a.A"), ("k:b", "attr:redis_key:k.b.B")]       # 자기와 후손 — 형제 C는 아니다
    assert idx.symbols[_sid(idx, "k.other.redis_key_user")].resources == ()                                                 # 식별자가 같아도 별칭이 아니다
    assert idx.symbols[_sid(idx, "k.c.C")].resources == () and not any(r.via == "alias" for s in idx.symbols for r in s.resources)


async def test_클래스_속성_리터럴은_자원이_아니어도_클래스_심볼에_남고_빈_리터럴은_정의가_아니다():
    """측정 #17 작은 결함: 자원 아닌 리터럴(`"material"`)로 정의한 자기 속성이 "없음"으로 보였다 — 후보가 config 이름뿐이라서. 리터럴은 전부 클래스 심볼에
    남겨 질의가 "풀렸으나 자원 아님"을 가른다(R3-5g ①). JSON으로 나갔다 와도 같다. 빈 문자열은 자리표시자라 정의가 아니다."""
    idx = await _index(KEYED, KEYED_NAMES)
    assert idx.symbols[_sid(idx, "k.a.A")].consts == (("mode", "material"), ("redis_key", "k:a"))
    assert idx.symbols[_sid(idx, "k.b.B")].consts == (("redis_key", "k:b"),) and idx.symbols[_sid(idx, "k.base.KeyedJob")].consts == ()
    again = ix.Index.from_dict(json.loads(json.dumps(idx.to_dict())))
    assert again.symbols[_sid(again, "k.a.A")].consts == (("mode", "material"), ("redis_key", "k:a"))
    blank = await _index({**KEYED, "k/e.py": 'from k.base import KeyedJob\n\n\nclass E(KeyedJob):\n    redis_key = ""\n'}, KEYED_NAMES)
    assert blank.symbols[_sid(blank, "k.e.E")].consts == ()


# ── R3-5i ③ — config로 푼 이름은 쓰임새로 흐를 때만 자원 ──

FLOW = {'f/__init__.py': '', 'f/jobs.py': (
    'class Base:\n    def _run(self):\n        key = self.cfg.get("tmp_alarm")\n        if self.cmd and key:\n            return self.step()\n\n\n'
    'class Reader:\n    def _run(self):\n        key = self.cfg.get("tmp_alarm")\n        return self.redis.get(key)\n\n\n'
    'class Direct:\n    def _run(self):\n        return self.redis.get(self.cfg.get("tmp_alarm"))\n\n\n'
    'class Builder:\n    def _run(self):\n        key = self.cfg.get("tmp_alarm")\n        full = f"{key}:L1"\n        for k in [full]:\n            self.redis.get(k)\n\n\n'
    'class Giver:\n    def _run(self):\n        return self.cfg.get("tmp_alarm")\n\n\n'
    'class Keeper:\n    def _run(self):\n        self.key = self.cfg.get("tmp_alarm")\n\n\n'
    'class Packer:\n    def _run(self):\n        return {"k": self.cfg.get("tmp_alarm")}\n\n\n'
    'class Checker:\n    def _run(self):\n        if self.cfg.get("tmp_alarm"):\n            return 1\n        return 0\n')}
FLOW_NAMES = [Name("rediskey", "tmp:alarm:{line}", "redis_key.tmp_alarm")]


async def test_config_키_토큰으로_푼_이름은_풀린_값이_쓰임새로_흐를_때만_그_함수의_자원이다():
    """측정 #19 P0-b: `키가 없다 — <임시 알람 키>` 틀린 사실 2건 — 부모 메서드가 `config.get`으로 이름만 풀고 `if <명령> and <변수>:` 참거짓에만 썼다.
    `key` 표식은 토큰이 따옴표로 있는 줄이면 자원으로 세었다. 이제 풀린 값이 지역 변수에 묶이면 그 변수(와 그것으로 만든 변수·반복 변수)가 호출 인자·return·
    self 속성으로 흐를 때만; 조건식에만 쓰이면 아니다. 호출 인자 안에 바로 있거나 return이면 그대로 자원, 모르는 모양(사전 안 등)은 자원으로 둔다(보수)."""
    idx = await _index(FLOW, FLOW_NAMES)

    def res(cls):
        return [(r.name, r.direction, r.via) for r in idx.symbols[_sid(idx, f"f.jobs.{cls}._run")].resources]

    assert res("Base") == [] and res("Checker") == []                                               # 조건식에만
    assert res("Reader") == [("tmp:alarm:{line}", "reads", "key")] and res("Direct") == res("Reader")
    assert res("Builder") == res("Reader") and res("Giver") == res("Reader")                          # f-string·반복 변수를 거쳐도, return도
    assert res("Keeper") == res("Reader") and res("Packer") == res("Reader")                          # self 속성 대입·사전 안(모르는 모양)


# ── R3-5j ③ — 속성으로 푼 토큰도, 받는 함수의 매개변수까지 ──

FLOW2 = {'g/__init__.py': '', 'g/jobs.py': (
    'class Parent:\n    redis_key = ""\n\n    def _run(self):\n        key = self.cfg.get("redis_key", self.redis_key)\n'
    '        self.later(lambda: self.reset(cmd=self.cmd, key=key))\n\n    def reset(self, cmd, key):\n        if cmd and key:\n            return 1\n\n'
    '    def later(self, fn):\n        return fn()\n\n\n'
    'class Child(Parent):\n    redis_key = "tmp_alarm"\n\n\n'
    'class Parent2:\n    redis_key = ""\n\n    def _run(self):\n        key = self.cfg.get("redis_key", self.redis_key)\n        return self.load(key)\n\n'
    '    def load(self, key):\n        return self.redis.get(key)\n\n\n'
    'class Child2(Parent2):\n    redis_key = "tmp_alarm"\n\n\n'
    'class Passer:\n    def _run(self):\n        return self.check(self.cfg.get("tmp_alarm"))\n\n    def check(self, key):\n        if key:\n            return 1\n\n\n'
    'class Passer2:\n    def _run(self):\n        return self.check(self.cfg.get("tmp_alarm"))\n\n    def check(self, key):\n        return self.redis.get(key)\n\n\n'
    'class Deep:\n    def _run(self):\n        key = self.cfg.get("tmp_alarm")\n        self.a(key)\n\n    def a(self, k):\n        self.b(k)\n\n'
    '    def b(self, k):\n        self.c(k)\n\n    def c(self, k):\n        self.d(k)\n\n    def d(self, k):\n        if k:\n            return 1\n\n\n'
    'class Outside:\n    def _run(self):\n        key = self.cfg.get("tmp_alarm")\n        helper(key)\n\n\n'
    'def helper(key):\n    if key:\n        return 1\n\n\n'
    'class RoleParent:\n    role = ""\n\n    def _run(self):\n        return 1 if self.role == "material" else 0\n\n\n'
    'class RoleChild(RoleParent):\n    role = "tmp_alarm"\n')}
FLOW2_NAMES = [Name("rediskey", "tmp:alarm:{line}", "redis_key.tmp_alarm")]


async def test_속성으로_푼_토큰도_흐름을_보고_호출_인자로_넘기면_받는_함수의_매개변수_쓰임새까지_이어_본다():
    """측정 #20: `키가 없다 — <임시 알람 키>`가 그대로였다 — (a) 그 줄의 토큰이 리터럴이 아니라 `self.<속성>`(값은 하위 클래스, `attr:` 표식)이라 흐름 검사 밖,
    (b) 리터럴이어도 람다 안 `self.<리셋>(…, <인자>=<변수>)` 키워드 인자라 "호출 인자 = 쓰임새"로 끝났는데 받는 함수 안은 `if <명령> and <인자>:`뿐.
    이제 `attr:` 표식도 검사하고, 받는 함수를 가족 메서드·같은 모듈 함수에서 풀어 그 매개변수의 쓰임새를 이어 본다(깊이 3 — 넘으면 자원, 못 풀어도 자원 —
    저장소 클라이언트가 바로 그것이다). 람다 인자는 싱크가 아니고 그 몸체의 호출을 본다."""
    idx = await _index(FLOW2, FLOW2_NAMES)

    def res(cls):
        return sorted((r.name, r.direction, r.via) for r in idx.symbols[_sid(idx, f"g.jobs.{cls}._run")].resources)

    assert res("Parent") == []                                                                   # (a)+(b): 람다 안 reset(key=key) → 조건식뿐
    assert res("Parent2") == [("tmp:alarm:{line}", "reads", "attr:redis_key:g.jobs.Child2")]      # load(key) → self.redis.get(key)
    assert res("Passer") == [] and res("Passer2") == [("tmp:alarm:{line}", "reads", "key")]       # 토큰이 바로 호출 인자 — 받는 쪽으로
    assert res("Deep") == [("tmp:alarm:{line}", "reads", "key")]                                  # 네 번째 함수까지는 안 따라간다 — 자원(보수)
    assert res("Outside") == []                                                                   # 같은 모듈 함수도 푼다
    assert res("RoleParent") == []                                                                # 비교에만 쓰는 속성은 자원이 아니다 — 형제의 값이 키 이름이어도


# ── R3-5k ① — 키워드 인자의 값도 받는 함수로 ──

FLOW3 = {'h/__init__.py': '', 'h/jobs.py': (
    'class KwParent:\n    role = ""\n\n    def _run(self):\n        self.reset(cmd=self.cmd, key=self.role)\n\n'
    '    def reset(self, cmd, key):\n        if cmd and key:\n            return 1\n\n\n'
    'class KwChild(KwParent):\n    role = "tmp_alarm"\n\n\n'
    'class KwRegistry:\n    role = ""\n\n    def _run(self):\n        self.reset(key=self.role)\n\n'
    '    def reset(self, key):\n        return self.registry.get("proc").process({"key": key})\n\n\n'
    'class KwRegistryChild(KwRegistry):\n    role = "tmp_alarm"\n\n\n'
    'class KwToken:\n    def _run(self):\n        self.check(key=self.cfg.get("tmp_alarm"))\n\n    def check(self, key):\n        if key:\n            return 1\n')}


async def test_키워드_인자로_넘긴_값도_받는_함수의_매개변수_쓰임새까지_이어_본다():
    """측정 #21: 남은 부모 조회 줄의 속성 읽기가 `<매개변수>=self.<역할 속성>` 키워드 인자였다 — 부모 노드가 `keyword`라 호출 인자로 못 보고 "모르는 모양"으로
    자원이 됐다. 키워드 값도 그 호출의 인자로 보고 받는 함수의 매개변수를 따라간다. 받는 함수가 값을 못 푸는 호출(레지스트리 디스패치)로 넘기면 전처럼
    자원이다 — 그 규칙이 저장소 클라이언트 읽기를 지킨다(측정 #21 판단: 남긴다, P1-7)."""
    idx = await _index(FLOW3, FLOW2_NAMES)

    def res(cls):
        return sorted((r.name, r.direction, r.via) for r in idx.symbols[_sid(idx, f"h.jobs.{cls}._run")].resources)

    assert res("KwParent") == [] and res("KwToken") == []                                         # 받는 함수는 조건식뿐
    assert res("KwRegistry") == [("tmp:alarm:{line}", "reads", "attr:role:h.jobs.KwRegistryChild")]  # 못 푸는 호출로 넘김 — 남긴다


# ── R3-5r ① — 동사 없는 줄의 방향은 조립한 값이 처음 닿는 호출의 동사로 ──

HANDOFF = {'h/__init__.py': '', 'h/jobs.py': (
    'class Direct:\n    def _run(self, line):\n        command = {"op": "refresh", "target": self.cfg["redis_key"]["alarm_stats"].format(line=line)}\n'
    '        self.producer.send("t", command)\n\n\n'
    'class Assembler:\n    def target_key(self, line):\n        return self.cfg["redis_key"]["alarm_stats"].format(line=line)\n\n'
    '    def _run(self, line):\n        cmd = {"op": "refresh", "target": self.target_key(line)}\n        self.producer.send("t", cmd)\n\n\n'
    'class Saver:\n    def _run(self, line):\n        key = self.cfg["redis_key"]["alarm_stats"].format(line=line)\n        self.store.save(Command(key=key))\n\n\n'
    'class Helped:\n    def target_key(self, line):\n        return self.cfg["redis_key"]["alarm_stats"].format(line=line)\n\n'
    '    def _run(self, line):\n        return json.loads(self.redis.get(self.target_key(line)) or "{}")\n\n\n'
    'class Mixed:\n    def target_key(self, line):\n        return self.cfg["redis_key"]["alarm_stats"].format(line=line)\n\n'
    '    def read(self, line):\n        return self.redis.get(self.target_key(line))\n\n'
    '    def push(self, line):\n        self.producer.send("t", {"target": self.target_key(line)})\n\n\n'
    'class Quiet:\n    def target_key(self, line):\n        return self.cfg["redis_key"]["alarm_stats"].format(line=line)\n\n\n'
    'class Setter:\n    def _run(self):\n        key = self.cfg.get("alarm_stats")\n        self.redis.set(key, 1)\n\n\n'
    'class Logged:\n    def _run(self, line):\n        key = self.cfg["redis_key"]["alarm_stats"].format(line=line)\n        self.log.info(key)\n        self.redis.set(key, 1)\n\n\n'
    'class Deep:\n    def _run(self, line):\n        key = self.cfg["redis_key"]["alarm_stats"].format(line=line)\n        self.a(key)\n\n'
    '    def a(self, k):\n        self.b(k)\n\n    def b(self, k):\n        self.producer.send("t", k)\n\n\n'
    'class Far:\n    def _run(self, line):\n        key = self.cfg["redis_key"]["alarm_stats"].format(line=line)\n        self.a(key)\n\n'
    '    def a(self, k):\n        self.b(k)\n\n    def b(self, k):\n        self.c(k)\n\n    def c(self, k):\n        self.d(k)\n\n    def d(self, k):\n        self.producer.send("t", k)\n\n\n'
    'def make_key(cfg):\n    return cfg["redis_key"]["alarm_stats"]\n\n\ndef job(cfg, producer):\n    producer.send("t", {"target": make_key(cfg)})\n\n\n'
    'class Verbed:\n    def _run(self, line):\n        return self.redis.get(self.cfg["redis_key"]["alarm_stats"].format(line=line))\n')}
HANDOFF_NAMES = [Name("rediskey", "alarm:stats:{line}", "redis_key.alarm_stats")]


async def test_동사_없는_자원_줄의_방향은_조립한_값이_처음_닿는_호출의_동사로_정하고_부르는_쪽까지_따라간다():
    """평가자 회신(10-30) 4(a): 사내 `<PGM 처리기>`의 `<대상 자원 속성>`이 키를 조립하고 명령 DTO로 `<싱크>`가 쓴다 — 그 줄엔 동사가 없어 인덱스가 읽기로
    적었다(이 모양 43개, `code.uses` 쓰기 0줄 → 걷기가 쓴 쪽을 모른다). 같은 줄 동사가 없으면 조립한 값이 **처음 닿는 호출의 동사**로: 대입·사전·생성자를
    지나고, `return`이면 부르는 쪽(exact `calls`)에서 이어 본다(깊이 3). config 조회는 호출이 아니라 푸는 자리. 부르는 쪽이 읽기·쓰기로 갈리면 읽기(보수).
    넘긴 동사는 `Resource.handoff`에 남아 `code.resources`·`code.uses` 줄이 보인다. 같은 줄에 동사가 있으면 전과 같다."""
    idx = await _index(HANDOFF, HANDOFF_NAMES)

    def res(qual):
        return [(r.direction, r.handoff) for r in idx.symbols[_sid(idx, f"h.jobs.{qual}")].resources]

    assert res("Direct._run") == [("writes", "send")]                      # 사전에 담아 send
    assert res("Assembler.target_key") == [("writes", "send")]             # return → 부르는 쪽의 사전 → send
    assert res("Saver._run") == [("writes", "save")]                       # 생성자 안에서 save의 인자
    assert res("Helped.target_key") == [("reads", "get")]                  # return → 부르는 쪽의 redis.get
    assert res("Mixed.target_key") == [("reads", "")]                      # 부르는 쪽이 갈린다 — 읽기(보수)
    assert res("Quiet.target_key") == [("reads", "")]                      # 부르는 쪽 없음
    assert res("Setter._run") == [("writes", "set")]                       # config 조회는 호출이 아니다 — 그다음 set
    assert res("Logged._run") == [("writes", "set")]                       # 동사 없는 호출(log.info)은 지나간다
    assert res("Deep._run") == [("writes", "send")]                        # 가족 메서드 둘을 지나 send(깊이 2)
    assert res("Far._run") == [("reads", "")]                              # 깊이 3을 넘는다
    assert res("make_key") == [("writes", "send")]                         # 모듈 함수도 부르는 쪽으로
    assert res("Verbed._run") == [("reads", "")]                           # 같은 줄 동사 — 전과 같다
    writers = {idx.symbols[s].qualname for s in idx.writers("rediskey", "alarm:stats:{line}")}
    assert writers == {"h.jobs.Direct._run", "h.jobs.Assembler.target_key", "h.jobs.Saver._run", "h.jobs.Setter._run", "h.jobs.Logged._run",
                       "h.jobs.Deep._run", "h.jobs.make_key"}
    again = ix.Index.from_dict(json.loads(json.dumps(idx.to_dict())))
    assert [s.resources for s in again.symbols] == [s.resources for s in idx.symbols]


async def test_넘긴_동사는_resources와_uses_줄에_보인다():
    from src.knowledge import query as qy
    idx = await _index(HANDOFF, HANDOFF_NAMES)
    lines = qy.resources_lines(idx, _sid(idx, "h.jobs.Direct"), where=lambda sid: "")
    assert lines[1:] == ["  쓰기 1:", "    alarm:stats:{line} [rediskey] — h.jobs.Direct._run (h/jobs.py:L3) · 키를 조립해 send()로 넘긴다"]
    helped = qy.resources_lines(idx, _sid(idx, "h.jobs.Helped"), where=lambda sid: "")
    assert helped[1:] == ["  읽기 1:", "    alarm:stats:{line} [rediskey] — h.jobs.Helped.target_key (h/jobs.py:L24) · 키를 조립해 get()로 넘긴다"]
    quiet = qy.resources_lines(idx, _sid(idx, "h.jobs.Quiet"), where=lambda sid: "")
    assert quiet[1:] == ["  읽기 1:", "    alarm:stats:{line} [rediskey] — h.jobs.Quiet.target_key (h/jobs.py:L43)"]
    uses = qy.uses_lines(idx, qy.Graph(idx), qy.uses(idx, "alarm:stats:{line}"), where=lambda sid: "[r]", cap=None)
    assembler = next(l for l in uses if l.startswith("    h.jobs.Assembler.target_key (h/jobs.py:L9) [r] — 진입점: "))
    assert assembler.endswith(" · 키를 조립해 send()로 넘긴다") and uses.index(assembler) < uses.index("  읽기 5:")
    assert "    h.jobs.Quiet.target_key (h/jobs.py:L43) [r] — 진입점: 이 함수(부르는 곳 없음)" in uses
