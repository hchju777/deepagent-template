"""팩트시트 — 집계 결과를 **사람이 눈으로 검토할** JSON으로.

9a의 `window.describe()`와 같은 성격이다. 렌더링 파이프라인이 아니라 **검토
도구**다: 9c의 HTML은 `Facts`와 유도 함수를 타입 그대로 받아 쓴다. 여기서
dict로 평탄화한 것을 렌더러가 다시 읽으면, 화면에 틀린 숫자가 떴을 때 집계가
틀린 것인지 평탄화가 틀린 것인지 구별할 수 없게 된다.

그래서 이 파일의 유일한 소비자는 `report aggregate` 명령이다.
"""
from datetime import date

from src.report.facts import (Facts, freshness, ranking_by_gbm, repeats,
                              scenario_lifecycle, spikes, status_breakdown)
from src.report.window import WEEKDAY_LABEL


def _day(value: date | None) -> str | None:
    return f"{value.isoformat()}({WEEKDAY_LABEL[value.weekday()]})" if value else None


def _change(change) -> dict:
    out: dict = {"건수": change.value}
    if change.vs_previous_day:
        delta, ratio = change.vs_previous_day
        out["전일 대비"] = {"기준일": _day(change.previous_day_label),
                        "기준값": change.previous_day,
                        "차이": delta, "배수": round(ratio, 2) if ratio else None}
    if change.vs_previous_week:
        delta, ratio = change.vs_previous_week
        out["전주 동요일 대비"] = {"기준일": _day(change.previous_week_label),
                             "기준값": change.previous_week,
                             "차이": delta, "배수": round(ratio, 2) if ratio else None}
    return out


def fact_sheet(facts: Facts) -> dict:
    window = facts.window
    yesterday = window.yesterday
    top_n = facts.thresholds.top_n
    lifecycle = scenario_lifecycle(facts)      # 두 번 부르면 전 행을 두 번 훑는다

    sheet: dict = {
        "기준": {
            "어제": _day(yesterday),
            "집계 대상": [_day(d) for d in window.days],
            "표본이 완전한가": facts.complete,
        },
        "① 총 알람": _change(facts.compare(yesterday)),
        "② 미해제": _change(facts.compare(yesterday, unresolved=True)),
        "③ GBM별": {gbm: _change(facts.compare(yesterday, gbm=gbm))
                  for gbm in sorted({row.gbm for row in facts.rows})},
        "④ 법인별": {f"{o.gbm}/{o.fct}": _change(facts.compare(yesterday, site=o.site))
                  for o in facts.ok_sites},
        "⑤ 일별 추세": {_day(d): n for d, n in facts.daily().items()},
        "⑥ 법인 TOP": [{"법인": k, "건수": n}
                    for k, n in facts.tally(lambda r: r.plant, day=yesterday, limit=top_n)],
        # GBM별로 나눠 보여 준다 — 전사 하나로 줄을 세우면 알람이 많은 GBM이
        # 목록을 통째로 차지해서 다른 GBM의 문제가 안 보인다(실제로 그랬다).
        "⑦ 라인 TOP": {
            g.gbm: [{"법인": s.key[0], "라인": f"{s.key[1]} {s.key[2]}", "건수": s.count,
                     "GBM 내 비중": f"{s.ratio * 100:.1f}%"} for s in g.items]
            for g in ranking_by_gbm(facts, lambda r: (r.plant, r.line_code, r.line_name),
                                    day=yesterday, limit=facts.thresholds.top_per_gbm)},
        "⑧ 알람 항목 TOP": {
            g.gbm: [{"항목": f"{s.key[1]}({s.key[0]})", "건수": s.count,
                     "GBM 내 비중": f"{s.ratio * 100:.1f}%"} for s in g.items]
            for g in ranking_by_gbm(facts, lambda r: r.scenario, day=yesterday,
                                    limit=facts.thresholds.top_per_gbm)},
        "⑨ status 분포": dict(status_breakdown(facts, day=yesterday)),
        "⑩ 급증(직전 평일 평균 대비)": {
            "임계": f"{facts.thresholds.spike_ratio}배 이상 · "
                  f"최소 {facts.thresholds.spike_min_count}건",
            "법인": [{"GBM": s.key[0], "법인": s.key[1], "건수": s.count,
                    "기준값": round(s.baseline, 1), "배수": round(s.ratio, 2)}
                   for s in spikes(facts, lambda r: (r.gbm, r.plant), day=yesterday)],
            "GBM·법인·항목": [{"GBM": s.key[0], "법인": s.key[1],
                          "항목": f"{s.key[3]}({s.key[2]})",
                          "건수": s.count, "기준값": round(s.baseline, 1),
                          "배수": round(s.ratio, 2)}
                         for s in spikes(facts, lambda r: (r.gbm, r.plant,
                                                          r.scenario_id,
                                                          r.scenario_name),
                                         day=yesterday)],
        },
        "⑪ 반복 알람": {
            "임계": f"기간 내 {facts.thresholds.repeat_min_count}회 이상 · "
                  f"{facts.thresholds.repeat_min_days}일 이상에 걸쳐",
            "목록": [{"GBM": r.gbm, "법인": r.plant,
                   "라인": f"{r.line_code} {r.line_name}",
                   "항목": f"{r.scenario_name}({r.scenario_id})",
                   "건수": r.count, "발생일수": r.days}
                  for r in repeats(facts, limit=top_n)],
        },
        "⑫ 알람 항목 신규·소멸": {
            "신규": [{"GBM": i.gbm, "항목": f"{i.name}({i.scenario_id})",
                   "건수": i.count, "법인": list(i.plants)} for i in lifecycle.appeared],
            "소멸": [{"GBM": i.gbm, "항목": f"{i.name}({i.scenario_id})",
                   "이전 건수": i.count, "법인": list(i.plants)}
                  for i in lifecycle.vanished],
        },
        "⑬ 이슈": _issues(facts),
    }
    return sheet


def _issues(facts: Facts) -> dict:
    """리포트 하단에 실릴 것들. **비어 있는 것이 정상이고, 비어 있지 않으면 본문이다.**"""
    issues: dict = {}

    unavailable = [{"사이트": o.site, "상태": o.status, "이유": o.error or o.reason}
                   for o in facts.unavailable]
    if unavailable:
        issues["읽지 못한 법인"] = unavailable

    truncated = [{"사이트": o.site, "이유": o.truncated_reason}
                 for o in facts.ok_sites if not o.complete]
    if truncated:
        # 잘린 표본으로 낸 건수는 **하한일 뿐이다.** 이 줄이 빠지면 리포트가
        # "어제 5만 건"이라고 단정하는데 실제로는 그 이상이다.
        issues["표본이 잘린 법인 — 건수는 하한이다"] = truncated

    stalled = [{"사이트": f.site, "어제": f.yesterday_count, "기간 전체": f.window_count,
                "마지막 알람": f.last_seen.isoformat() if f.last_seen else None}
               for f in freshness(facts) if f.stalled]
    if stalled:
        issues["어제 데이터가 없는 법인(기간 중에는 있었다)"] = stalled

    quality = facts.problems.describe()
    if quality:
        issues["데이터 품질"] = quality

    issues["읽은 양"] = [_read_volume(o) for o in facts.ok_sites]
    return issues


def _read_volume(outcome) -> dict:
    volume = {"사이트": outcome.site, "받아온 문서": outcome.fetched,
              "집계에 쓴 행": outcome.kept}
    dropped = outcome.problems.dropped_breakdown()
    if dropped:
        volume["버린 이유"] = dropped
        # 합이 안 맞으면 이 층에 세지 않는 탈락 경로가 있다는 뜻이다. 숨기지 않고
        # 드러내야 다음 사람이 그 경로를 찾는다.
        unexplained = outcome.fetched - outcome.kept - sum(dropped.values())
        if unexplained:
            volume["설명되지 않은 차이"] = unexplained
    return volume
