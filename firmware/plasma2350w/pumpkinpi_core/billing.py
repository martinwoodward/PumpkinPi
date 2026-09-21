"""Exact, bounded billing normalization for the MicroPython runtime."""

import json
import re

MAX_MICROCREDITS = 9_000_000_000_000_000
NUMBER = r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$"
QUANTITIES = ("grossQuantity", "discountQuantity", "netQuantity")


class BillingError(ValueError):
    pass


def source_limit(source):
    unlimited = source.get("unlimited", False)
    if not isinstance(unlimited, bool):
        raise BillingError("unlimited must be boolean")
    limit = source.get("allowance_microcredits")
    if unlimited:
        if limit is not None:
            raise BillingError("unlimited requires null allowance_microcredits")
        return None
    if type(limit) is not int or not 0 <= limit <= MAX_MICROCREDITS:
        raise BillingError("configure a bounded allowance_microcredits")
    return limit


def response_period(value):
    if isinstance(value, str):
        if len(value) == 7 and value[4] == "-" and value[:4].isdigit() \
                and value[5:].isdigit():
            year, month = int(value[:4]), int(value[5:])
            if 2000 <= year <= 9999 and 1 <= month <= 12:
                return value
    if isinstance(value, dict):
        year, month = value.get("year"), value.get("month")
        if type(year) is int and type(month) is int \
                and 2000 <= year <= 9999 and 1 <= month <= 12:
            return "%04d-%02d" % (year, month)
    raise BillingError("malformed response timePeriod")


def exact_microcredits(values):
    parts = []
    exponent = 0
    for value in values:
        if isinstance(value, (bool, float)):
            raise BillingError("quantity must be an exact number or decimal string")
        text = str(value)
        if len(text) > 128 or not re.match(NUMBER, text) or text.startswith("-"):
            raise BillingError("quantity must be finite and nonnegative")
        split = text.lower().split("e")
        power = int(split[1]) if len(split) == 2 else 0
        whole = split[0].split(".")
        digits = whole[0] + (whole[1] if len(whole) == 2 else "")
        power -= len(whole[1]) if len(whole) == 2 else 0
        while len(digits) > 1 and digits.endswith("0"):
            digits = digits[:-1]
            power += 1
        if not -18 <= power <= 18 or len(digits) > 32:
            raise BillingError("quantity precision or magnitude exceeds supported bounds")
        parts.append((int(digits), power))
        exponent = min(exponent, power)
        if len(parts) > 512:
            raise BillingError("too many billing rows")
    total = sum(coefficient * 10 ** (power - exponent)
                for coefficient, power in parts)
    if exponent + 6 >= 0:
        result = total * 10 ** (exponent + 6)
    else:
        divisor = 10 ** (-exponent - 6)
        if total % divisor:
            raise BillingError("unsupported sub-microcredit precision")
        result = total // divisor
    if result > MAX_MICROCREDITS:
        raise BillingError("quantity exceeds supported range")
    return result


def validate_source(source):
    if not isinstance(source, dict):
        raise BillingError("GitHub source must be an object")
    if source.get("owner_type") not in ("user", "organization", "enterprise"):
        raise BillingError("unsupported billing owner type")
    for key in ("owner", "subject"):
        value = source.get(key)
        if key == "subject" and value is None:
            continue
        if not isinstance(value, str) or not value or len(value) > 96 \
                or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in value):
            raise BillingError("invalid billing " + key)
    if source["owner_type"] == "user" and source.get("subject"):
        raise BillingError("personal billing does not accept a subject filter")
    source_limit(source)
    mapping = source.get("mapping")
    if not isinstance(mapping, dict):
        raise BillingError("configure the verified billing unit mapping")
    for key in ("unit_type", "product", "sku"):
        value = mapping.get(key)
        if not isinstance(value, str) or not value or len(value) > 128:
            raise BillingError("configure exact mapping " + key)
    if mapping.get("quantity_field") not in QUANTITIES:
        raise BillingError("unsupported quantity field")
    if source.get("quality", "estimated") != "estimated":
        raise BillingError("a configured allowance is estimated, not a GitHub wallet")


def normalize_usage(data, source, period):
    if not isinstance(data, dict) or not isinstance(data.get("usageItems"), list):
        raise BillingError("usage response lacks usageItems")
    if response_period(data.get("timePeriod")) != period:
        raise BillingError("response timePeriod differs from requested month")
    mapping = source.get("mapping")
    if not isinstance(mapping, dict):
        raise BillingError("no owner-approved unit mapping configured")
    field = mapping.get("quantity_field")
    if field not in QUANTITIES:
        raise BillingError("unsupported quantity field")
    values = []
    if len(data["usageItems"]) > 512:
        raise BillingError("too many billing rows")
    for item in data["usageItems"]:
        if not isinstance(item, dict):
            raise BillingError("malformed usage item")
        if (item.get("unitType") == mapping.get("unit_type")
                and item.get("product") == mapping.get("product")
                and item.get("sku") == mapping.get("sku")):
            if field not in item:
                raise BillingError("mapped quantity field is absent")
            values.append(item[field])
    if not values and data["usageItems"]:
        raise BillingError("no rows match the approved mapping")
    limit = source_limit(source)
    return exact_microcredits(values), limit


def loads_exact(raw):
    """Keep JSON decimal lexemes intact on ports without json.parse_float."""
    if len(raw) > 32768:
        raise BillingError("billing JSON exceeds 32 KiB")
    try:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        output = []
        index = start = 0
        while index < len(text):
            char = text[index]
            if char == '"':
                end = index + 1
                while end < len(text):
                    if text[end] == "\\":
                        end += 2
                    elif text[end] == '"':
                        end += 1
                        break
                    else:
                        end += 1
                index = end
            elif char in "-0123456789":
                end = index + 1
                while end < len(text) and text[end] not in " \t\r\n,]}:":
                    end += 1
                number = text[index:end]
                if len(number) > 128 or not re.match(NUMBER, number):
                    raise BillingError("invalid JSON number")
                following = end
                while following < len(text) and text[following] in " \t\r\n":
                    following += 1
                if following < len(text) and text[following] == ":":
                    raise BillingError("JSON object keys must be quoted")
                if any(c in number for c in ".eE"):
                    output.extend((text[start:index], '"', number, '"'))
                    start = end
                index = end
            else:
                index += 1
        output.append(text[start:])
        return json.loads("".join(output))
    except (ValueError, UnicodeError):
        raise BillingError("invalid billing JSON")


def github_path(source, period):
    period = response_period(period)
    routes = {"user": "users", "organization": "organizations", "enterprise": "enterprises"}
    route = "/%s/%s/settings/billing/ai_credit/usage" % (
        routes[source["owner_type"]], source["owner"])
    query = "?year=%s&month=%d" % (period[:4], int(period[5:]))
    if source.get("subject"):
        query += "&user=" + source["subject"]
    return route + query
