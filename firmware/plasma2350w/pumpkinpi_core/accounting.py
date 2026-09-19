"""Validated fixed-point balance snapshots and accounting state."""

import time as _time

from pumpkinpi_core.timebase import unix_gmtime

MAX_MICROCREDITS = 9_000_000_000_000_000
QUALITIES = ("reported-derived", "estimated", "unknown")


class SnapshotError(ValueError):
    pass


def _bounded_int(value, name, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SnapshotError("%s must be an integer" % name)
    if value < 0 or value > MAX_MICROCREDITS:
        raise SnapshotError("%s is out of range" % name)
    return value


def valid_period(value):
    if not isinstance(value, str) or len(value) != 7 or value[4] != "-":
        return False
    if not value[:4].isdigit() or not value[5:].isdigit():
        return False
    try:
        year = int(value[:4])
        month = int(value[5:])
    except ValueError:
        return False
    return 2000 <= year <= 9999 and 1 <= month <= 12


def validate_snapshot(raw, provision):
    if not isinstance(raw, dict):
        raise SnapshotError("snapshot must be an object")
    if isinstance(raw.get("schema_version"), bool) \
            or not isinstance(raw.get("schema_version"), int) \
            or raw.get("schema_version") != 1:
        raise SnapshotError("unsupported schema version")
    required_text = ("source", "stream_id", "profile_id", "balance_basis",
                     "billing_period", "quality", "budget_revision")
    for name in required_text:
        value = raw.get(name)
        if not isinstance(value, str) or not value or len(value) > 96:
            raise SnapshotError("invalid %s" % name)
    if not valid_period(raw["billing_period"]):
        raise SnapshotError("invalid billing_period")
    if raw["quality"] not in QUALITIES:
        raise SnapshotError("invalid quality")
    sequence = _bounded_int(raw.get("sequence"), "sequence")
    generation = _bounded_int(raw.get("config_generation"), "config_generation")
    for name in ("profile_id", "balance_basis", "config_generation"):
        if raw.get(name) != provision.get(name):
            raise SnapshotError("unauthorized %s" % name)
    expected_revision = provision.get("budget_revision")
    if expected_revision is not None and raw["budget_revision"] != expected_revision:
        raise SnapshotError("unauthorized budget_revision")
    used = _bounded_int(raw.get("used_microcredits"), "used_microcredits", True)
    limit = _bounded_int(raw.get("limit_microcredits"), "limit_microcredits", True)
    remaining = _bounded_int(raw.get("remaining_microcredits"),
                             "remaining_microcredits", True)
    unlimited = raw.get("unlimited", False)
    if not isinstance(unlimited, bool):
        raise SnapshotError("unlimited must be boolean")
    if unlimited:
        if not provision.get("allow_unlimited", False):
            raise SnapshotError("unlimited balance is not authorized")
        if raw["quality"] == "unknown" or used is None or limit is not None or remaining is not None:
            raise SnapshotError("unlimited requires known usage and no finite limit or remaining")
    elif raw["quality"] == "unknown":
        if used is not None or limit is not None or remaining is not None:
            raise SnapshotError("unknown quality requires unknown amounts")
    elif used is None or limit is None:
        raise SnapshotError("known quality requires used and limit")
    if not unlimited and (used is None) != (limit is None):
        raise SnapshotError("used and limit must both be present or absent")
    if used is not None and not unlimited:
        computed = max(limit - used, 0)
        if remaining is not None and remaining != computed:
            raise SnapshotError("contradictory remaining_microcredits")
        remaining = computed
    elif remaining is not None:
        raise SnapshotError("remaining requires used and limit")
    confirmed = raw.get("exhaustion_confirmed", False)
    if not isinstance(confirmed, bool):
        raise SnapshotError("exhaustion_confirmed must be boolean")
    if confirmed and (remaining != 0 or raw["quality"] == "unknown"):
        raise SnapshotError("confirmed exhaustion requires a known zero")
    if confirmed and not provision.get("allow_confirmed_exhaustion", False):
        raise SnapshotError("confirmed exhaustion is not authorized for this basis")
    observed = raw.get("observed_at")
    source_as_of = raw.get("source_as_of")
    for name, value in (("observed_at", observed), ("source_as_of", source_as_of)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                  or value < 0):
            raise SnapshotError("invalid %s" % name)
    result = dict(raw)
    result["sequence"] = sequence
    result["config_generation"] = generation
    result["used_microcredits"] = used
    result["limit_microcredits"] = limit
    result["remaining_microcredits"] = remaining
    result["unlimited"] = unlimited
    return result


class AccountingReducer:
    """Accept ordered snapshots and expose bounded display events."""

    def __init__(self, provision, stale_after=900):
        self.provision = provision
        self.stale_after = stale_after
        self.snapshot = None
        self.last_sequence = -1
        self.stream_id = None
        self.event_delta = 0
        self.error = None
        self.received_at = None
        self.last_utc = None
        self.max_clock_jump = 21600
        self.authorized_stream = None

    def authorize_reconfiguration(self, provision):
        self.provision = provision
        self.snapshot = None
        self.last_sequence = -1
        self.stream_id = None
        self.event_delta = 0
        self.authorized_stream = None

    def authorize_reconnect(self, stream_id):
        if not isinstance(stream_id, str) or not stream_id:
            raise SnapshotError("invalid reconnect stream")
        self.authorized_stream = stream_id

    def accept(self, raw, now_utc):
        snap = validate_snapshot(raw, self.provision)
        if now_utc is None:
            raise SnapshotError("UTC clock is unavailable")
        observed = snap.get("observed_at")
        if observed is not None and observed > now_utc + 300:
            raise SnapshotError("observed_at is in the future")
        if self.last_utc is not None and now_utc + 5 < self.last_utc:
            raise SnapshotError("UTC clock rolled backward")
        self.last_utc = now_utc
        current_period = _period_from_epoch(now_utc)
        if snap["billing_period"] != current_period:
            raise SnapshotError("snapshot is not for current UTC period")
        if self.stream_id is not None:
            if snap["stream_id"] != self.stream_id:
                if snap["stream_id"] != self.authorized_stream:
                    raise SnapshotError("unexpected stream_id")
                self.snapshot = None
                self.last_sequence = -1
                self.event_delta = 0
            if snap["sequence"] <= self.last_sequence:
                raise SnapshotError("duplicate or out-of-order snapshot")
        prior = self.snapshot
        self.event_delta = 0
        if prior is not None:
            same_basis = (
                prior["billing_period"] == snap["billing_period"]
                and prior["budget_revision"] == snap["budget_revision"]
            )
            if same_basis and prior["used_microcredits"] is not None \
                    and snap["used_microcredits"] is not None:
                delta = snap["used_microcredits"] - prior["used_microcredits"]
                if delta > 0:
                    self.event_delta = delta
        self.snapshot = snap
        self.stream_id = snap["stream_id"]
        self.authorized_stream = None
        self.last_sequence = snap["sequence"]
        self.received_at = now_utc
        self.error = None
        return self.event_delta

    def record_error(self, message):
        self.error = str(message)[:160]

    def state(self, now_utc):
        if now_utc is None or self.snapshot is None:
            return {"known": False, "stale": True, "error": self.error}
        if self.last_utc is not None and now_utc + 5 < self.last_utc:
            return {"known": False, "stale": True, "error": "UTC clock rollback"}
        if self.last_utc is not None and now_utc - self.last_utc > self.max_clock_jump:
            return {"known": False, "stale": True, "error": "UTC clock jumped forward"}
        if _period_from_epoch(now_utc) != self.snapshot["billing_period"]:
            return {"known": False, "stale": True, "error": "billing period changed"}
        observed = self.snapshot.get("observed_at")
        stale = observed is None or observed > now_utc + 300 \
            or now_utc - observed > self.stale_after
        limit = self.snapshot["limit_microcredits"]
        remaining = self.snapshot["remaining_microcredits"]
        fraction = None
        unlimited = self.snapshot.get("unlimited", False)
        if unlimited:
            fraction = 1.0
        elif limit:
            fraction = remaining / float(limit)
        return {
            "known": (unlimited or remaining is not None) and self.snapshot["quality"] != "unknown",
            "unlimited": unlimited,
            "stale": stale,
            "fraction": fraction,
            "remaining_microcredits": remaining,
            "quality": self.snapshot["quality"],
            "confirmed_zero": bool(
                self.snapshot["exhaustion_confirmed"] and remaining == 0
            ),
            "error": self.error,
        }


def _period_from_epoch(epoch):
    try:
        value = unix_gmtime(epoch, _time)
        return "%04d-%02d" % (value[0], value[1])
    except Exception as exc:
        raise SnapshotError("cannot determine UTC period: %s" % exc)
