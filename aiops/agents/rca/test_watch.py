"""Deterministic tests for the sensing and detection layer. No cluster, no
network: the transports are injected, so every branch below is proven before
the code ever touches your endpoints.

    python -m pytest rca/test_watch.py -v
"""
import json
import math

import pytest

try:  # in-repo
    from rca.live_reads import (
        Detector,
        HpaState,
        KubectlReads,
        PromReads,
        Readings,
    )
except ImportError:  # standalone
    from live_reads import (
        Detector,
        HpaState,
        KubectlReads,
        PromReads,
        Readings,
    )


# --- Prometheus response parsing ------------------------------------------- #
def _prom(payload: dict):
    return lambda url, timeout: json.dumps(payload).encode()


def test_parses_vector_result():
    p = PromReads(fetcher=_prom(
        {"status": "success", "data": {"resultType": "vector",
         "result": [{"metric": {}, "value": [1710000000, "0.94"]}]}}))
    assert p.instant("q") == pytest.approx(0.94)


def test_parses_scalar_result():
    p = PromReads(fetcher=_prom(
        {"status": "success", "data": {"resultType": "scalar",
         "result": [1710000000, "0.22"]}}))
    assert p.instant("q") == pytest.approx(0.22)


def test_empty_series_is_nan_not_zero():
    p = PromReads(fetcher=_prom(
        {"status": "success", "data": {"resultType": "vector", "result": []}}))
    assert math.isnan(p.instant("q"))


def test_query_error_raises():
    p = PromReads(fetcher=_prom({"status": "error", "error": "bad query"}))
    with pytest.raises(RuntimeError):
        p.instant("q")


# --- HPA JSON parsing ------------------------------------------------------- #
def _hpa_json(metrics, current, mx):
    return json.dumps({
        "metadata": {"name": "cartservice"},
        "spec": {"minReplicas": 2, "maxReplicas": mx,
                 "metrics": [{"resource": {"name": n}} for n in metrics]},
        "status": {"currentReplicas": current, "desiredReplicas": current},
    })


def test_parses_cpu_only_hpa_at_max():
    k = KubectlReads(runner=lambda args, t: _hpa_json(["cpu"], 8, 8))
    s = k.hpa("cartservice")
    assert s.at_max is True
    assert s.has_memory_metric is False
    assert s.metrics == ("cpu",)


def test_parses_cpu_and_memory_hpa():
    k = KubectlReads(runner=lambda args, t: _hpa_json(["cpu", "memory"], 4, 8))
    s = k.hpa("cartservice")
    assert s.at_max is False
    assert s.has_memory_metric is True


# --- detector state machine ------------------------------------------------- #
def _r(mem, cpu=0.05, at_max=False, metrics=("cpu",)):
    cur = 3 if at_max else 2
    hpa = HpaState("h", cur, 3, 2, 3, metrics, at_max)
    return Readings("cartservice", "boutique", hpa, mem, cpu, 0)


def test_for_duration_holds_before_firing():
    d = Detector(for_polls=3)
    assert d.evaluate(_r(0.95)).kind == "pending"
    assert d.evaluate(_r(0.95)).kind == "pending"
    assert d.evaluate(_r(0.95)).kind == "firing"


def test_fires_flavor_a_cpu_low_at_min():
    # the common blind spot: cpu-only HPA sitting at min, memory saturating,
    # cpu low so it never scales. This is the one the live limit-cut reproduces.
    d = Detector(for_polls=1)
    assert d.evaluate(_r(0.95, cpu=0.05, at_max=False)).kind == "firing"


def test_fires_flavor_b_at_max_exhausted():
    # scaled to max on cpu and still saturating on memory: autoscaler exhausted
    d = Detector(for_polls=1)
    assert d.evaluate(_r(0.95, cpu=0.70, at_max=True)).kind == "firing"


def test_ordinary_load_does_not_fire():
    # high mem AND high cpu AND room to scale: the HPA can still add replicas on
    # cpu, so this is ordinary load, not the blind spot. Hold and let it scale.
    d = Detector(for_polls=1)
    assert d.evaluate(_r(0.95, cpu=0.80, at_max=False)).kind == "ok"


def test_dedup_holds_after_firing():
    d = Detector(for_polls=1)
    assert d.evaluate(_r(0.95)).kind == "firing"
    # still true next poll: held, NOT a second fresh fire
    assert d.evaluate(_r(0.95)).kind == "firing"
    assert d.evaluate(_r(0.95)).reason.endswith("(held)")


def test_resolves_and_rearms():
    d = Detector(for_polls=1)
    assert d.evaluate(_r(0.95)).kind == "firing"
    assert d.evaluate(_r(0.40)).kind == "resolved"
    assert d.evaluate(_r(0.40)).kind == "ok"
    # re-arm: a new saturation fires again
    assert d.evaluate(_r(0.95)).kind == "firing"


def test_memory_target_present_suppresses_fire():
    d = Detector(for_polls=1)
    assert d.evaluate(_r(0.95, metrics=("cpu", "memory"))).kind == "ok"


def test_nan_reading_does_not_fire():
    d = Detector(for_polls=1)
    assert d.evaluate(_r(math.nan)).kind == "ok"
