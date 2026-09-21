"""Small literal dotenv reader: no interpolation, execution, or secret logging."""

ALLOWED_KEYS = ("WIFI_SSID", "WIFI_PASSWORD", "GITHUB_TOKEN")


def load_environment(path=".env"):
    try:
        with open(path, "r") as handle:
            text = handle.read(8193)
    except OSError:
        raise ValueError("board .env is missing or unreadable; provision it explicitly")
    if len(text) > 8192:
        raise ValueError("board .env exceeds 8 KiB")
    result = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("invalid .env syntax at line %d" % number)
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key not in ALLOWED_KEYS or key in result:
            raise ValueError("unknown or duplicate .env key at line %d" % number)
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("unclosed .env quote at line %d" % number)
            value = value[1:-1]
        if not value or len(value) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("empty or invalid .env value at line %d" % number)
        result[key] = value
    for key in ALLOWED_KEYS:
        if key not in result:
            raise ValueError("board .env requires " + key)
    if any(not 33 <= ord(c) <= 126 for c in result["GITHUB_TOKEN"]):
        raise ValueError("GITHUB_TOKEN must contain visible ASCII without spaces")
    return result
