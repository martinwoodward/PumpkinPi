from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
FIRMWARE = ROOT / "firmware" / "plasma2350w"
if str(FIRMWARE) not in sys.path:
    sys.path.insert(0, str(FIRMWARE))


def period(epoch=None):
    return time.strftime("%Y-%m", time.gmtime(epoch or time.time()))


def snapshot(sequence=1, used=20, limit=100, quality="reported-derived",
             confirmed=False, stream="stream-a", observed=None, **changes):
    observed = int(time.time()) if observed is None else observed
    value = {
        "schema_version": 1, "source": "test", "stream_id": stream,
        "sequence": sequence, "profile_id": "profile",
        "config_generation": 1, "balance_basis": "test-basis",
        "budget_revision": "rev-1", "billing_period": period(),
        "observed_at": observed, "source_as_of": None,
        "used_microcredits": used, "limit_microcredits": limit,
        "remaining_microcredits": max(limit - used, 0),
        "quality": quality, "exhaustion_confirmed": confirmed,
    }
    value.update(changes)
    return value


PROVISION = {
    "profile_id": "profile", "config_generation": 1,
    "balance_basis": "test-basis", "budget_revision": "rev-1",
    "allow_confirmed_exhaustion": True,
}
