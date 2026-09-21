"""Cooperative IPv4 DNS, bootstrap NTP, and authenticated HTTPS.

Every operation owns its socket and has a deadline measured from its first
step. Callers supply ticks_diff so MicroPython's wrapping clock is respected.
NTP is an unauthenticated clock bootstrap, not a substitute for TLS validation.
"""

import errno
import os
import select
import socket
import ssl
import struct


class NetworkError(Exception):
    """A fixed, credential-free failure code safe for diagnostics."""

    def __init__(self, code, retry_after=None):
        super().__init__(code)
        self.retry_after = retry_after


def _ipv4(value):
    if not isinstance(value, str):
        raise NetworkError("invalid-ipv4")
    parts = value.split(".")
    if len(parts) != 4:
        raise NetworkError("invalid-ipv4")
    for part in parts:
        if not part or len(part) > 3 or any(c not in "0123456789" for c in part):
            raise NetworkError("invalid-ipv4")
        if int(part) > 255 or (len(part) > 1 and part[0] == "0"):
            raise NetworkError("invalid-ipv4")
    return value


def _hostname(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 253:
        raise NetworkError("invalid-host")
    value = value.lower()
    for label in value.split("."):
        if not 1 <= len(label) <= 63 or label[0] == "-" or label[-1] == "-":
            raise NetworkError("invalid-host")
        if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in label):
            raise NetworkError("invalid-host")
    return value


def _would_block(exc):
    for name in ("SSLWantReadError", "SSLWantWriteError"):
        cls = getattr(ssl, name, None)
        if cls is not None and isinstance(exc, cls):
            return True
    code = exc.args[0] if exc.args else None
    return code in (errno.EAGAIN, errno.EWOULDBLOCK)


class _Operation:
    def __init__(self, timeout_ms):
        if not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise NetworkError("invalid-timeout")
        self.timeout_ms = timeout_ms
        self._started = None
        self._socket = None
        self._closed = False
        self._result = None

    def close(self):
        sock, self._socket = self._socket, None
        self._closed = True
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def step(self, now_ms, ticks_diff):
        if self._result is not None:
            return self._result
        if self._closed:
            raise NetworkError("operation-closed")
        if self._started is None:
            self._started = now_ms
        try:
            if ticks_diff(now_ms, self._started) >= self.timeout_ms:
                raise NetworkError("network-timeout")
            result = self._advance()
            if result is not None:
                self._result = result
                self.close()
            return result
        except NetworkError:
            self.close()
            raise
        except Exception:
            self.close()
            raise NetworkError("network-io-failed") from None


class _UDPQuery(_Operation):
    def __init__(self, server_ip, port, timeout_ms):
        super().__init__(timeout_ms)
        self._address = (_ipv4(server_ip), port)
        self._sent = False

    def _advance(self):
        if self._socket is None:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.setblocking(False)
        if not self._sent:
            try:
                sent = self._socket.sendto(self._packet, self._address)
            except OSError as exc:
                if _would_block(exc):
                    return None
                raise
            if sent is None:
                return None
            if sent != len(self._packet):
                raise NetworkError("udp-short-write")
            self._sent = True
            return None
        try:
            packet, address = self._socket.recvfrom(513)
        except OSError as exc:
            if _would_block(exc):
                return None
            raise
        if address[0] != self._address[0] or address[1] != self._address[1]:
            raise NetworkError("udp-source-mismatch")
        if len(packet) > 512:
            raise NetworkError("udp-packet-too-large")
        return self._parse(packet)


def _dns_name(packet, offset):
    labels = []
    end = None
    visited = []
    size = 0
    while True:
        if offset >= len(packet) or offset in visited or len(visited) >= 128:
            raise NetworkError("dns-malformed-name")
        visited.append(offset)
        length = packet[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise NetworkError("dns-malformed-name")
            pointer = ((length & 0x3F) << 8) | packet[offset + 1]
            # Compression references a prior name, never arbitrary future data.
            if pointer < 12 or pointer >= offset:
                raise NetworkError("dns-malformed-name")
            if end is None:
                end = offset + 2
            offset = pointer
            if sum(1 for item in visited if packet[item] & 0xC0 == 0xC0) > 16:
                raise NetworkError("dns-compression-limit")
            continue
        if length & 0xC0:
            raise NetworkError("dns-malformed-name")
        offset += 1
        if length == 0:
            return ".".join(labels), end if end is not None else offset
        if offset + length > len(packet):
            raise NetworkError("dns-malformed-name")
        raw = packet[offset:offset + length]
        if any(c > 127 for c in raw):
            raise NetworkError("dns-malformed-name")
        label = raw.decode().lower()
        if not label or label[0] == "-" or label[-1] == "-" \
                or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in label):
            raise NetworkError("dns-malformed-name")
        labels.append(label)
        size += length + 1
        if size > 254:
            raise NetworkError("dns-malformed-name")
        offset += length


class DNSLookup(_UDPQuery):
    def __init__(self, hostname, dns_server, timeout_ms=5000):
        super().__init__(dns_server, 53, timeout_ms)
        self._hostname = _hostname(hostname)
        self._id = os.urandom(2)
        name = b"".join(bytes((len(label),)) + label.encode()
                        for label in self._hostname.split(".")) + b"\0"
        self._packet = self._id + struct.pack("!HHHHH", 0x0100, 1, 0, 0, 0)
        self._packet += name + b"\0\x01\0\x01"

    def _parse(self, packet):
        if len(packet) < 12 or packet[:2] != self._id:
            raise NetworkError("dns-transaction-mismatch")
        flags, questions, answers, authority, additional = struct.unpack(
            "!HHHHH", packet[2:12])
        if not flags & 0x8000 or flags & 0x7A40:
            raise NetworkError("dns-unsupported-flags")
        if flags & 15:
            raise NetworkError("dns-response-error")
        if questions != 1 or answers + authority + additional > 32:
            raise NetworkError("dns-record-limit")
        question, offset = _dns_name(packet, 12)
        if question != self._hostname or packet[offset:offset + 4] != b"\0\x01\0\x01":
            raise NetworkError("dns-question-mismatch")
        offset += 4
        records = []
        for index in range(answers + authority + additional):
            owner, offset = _dns_name(packet, offset)
            if offset + 10 > len(packet):
                raise NetworkError("dns-malformed-record")
            kind, cls, _ttl, length = struct.unpack("!HHIH", packet[offset:offset + 10])
            offset += 10
            end = offset + length
            if end > len(packet):
                raise NetworkError("dns-malformed-record")
            value = None
            if kind == 1:
                if length != 4:
                    raise NetworkError("dns-malformed-address")
                value = ".".join(str(c) for c in packet[offset:end])
            elif kind == 5:
                value, name_end = _dns_name(packet, offset)
                if name_end != end or not value:
                    raise NetworkError("dns-malformed-alias")
            if index < answers:
                if cls != 1:
                    raise NetworkError("dns-unsupported-class")
                if kind not in (1, 5):
                    raise NetworkError("dns-unsupported-answer")
                records.append((owner, kind, value))
            offset = end
        if offset != len(packet):
            raise NetworkError("dns-trailing-data")
        name = self._hostname
        seen = []
        for _ in range(9):
            if name in seen:
                raise NetworkError("dns-alias-cycle")
            seen.append(name)
            addresses = [value for owner, kind, value in records if owner == name and kind == 1]
            aliases = [value for owner, kind, value in records if owner == name and kind == 5]
            if addresses and aliases or len(set(aliases)) > 1:
                raise NetworkError("dns-ambiguous-answer")
            if addresses:
                return addresses[0]
            if not aliases:
                raise NetworkError("dns-no-address")
            name = aliases[0]
        raise NetworkError("dns-alias-limit")


class NTPQuery(_UDPQuery):
    def __init__(self, server_ip, timeout_ms=5000):
        super().__init__(server_ip, 123, timeout_ms)
        self._nonce = os.urandom(8)
        self._packet = b"\x23" + bytes(39) + self._nonce

    def _parse(self, packet):
        if len(packet) < 48:
            raise NetworkError("ntp-short-packet")
        flags = packet[0]
        if flags & 7 != 4 or (flags >> 3) & 7 not in (3, 4) or flags >> 6 == 3:
            raise NetworkError("ntp-invalid-server")
        if not 1 <= packet[1] <= 15:
            raise NetworkError("ntp-invalid-stratum")
        if packet[24:32] != self._nonce:
            raise NetworkError("ntp-challenge-mismatch")
        if packet[40:48] == bytes(8):
            raise NetworkError("ntp-zero-time")
        seconds = struct.unpack("!I", packet[40:44])[0] - 2208988800
        if seconds < 1577836800:
            seconds += 4294967296
        if not 1577836800 <= seconds <= 4102444800:
            raise NetworkError("ntp-time-out-of-range")
        return seconds


_HEADER_LIMIT = 8192
_TOKEN_CHARS = "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyz"


def _header(line):
    if b":" not in line:
        raise NetworkError("http-malformed-header")
    name, value = line.split(b":", 1)
    if not name or any(c > 127 for c in name + value):
        raise NetworkError("http-malformed-header")
    name = name.decode().lower()
    if any(c not in _TOKEN_CHARS for c in name):
        raise NetworkError("http-malformed-header")
    if any(c < 32 and c != 9 or c == 127 for c in value):
        raise NetworkError("http-malformed-header")
    return name, value.decode().strip(" \t")


class _HTTPResponse:
    def __init__(self, max_body):
        self.max_body = max_body
        self.buffer = bytearray()
        self.body = bytearray()
        self.headers = None
        self.status = None
        self.mode = "headers"
        self.remaining = 0
        self.trailer_size = 0
        self.framing_size = 0
        self.done = False

    def _body(self, data):
        if len(self.body) + len(data) > self.max_body:
            raise NetworkError("http-body-too-large")
        self.body.extend(data)

    def _headers(self, raw):
        lines = raw.split(b"\r\n")
        status = lines.pop(0).split(b" ", 2)
        if len(status) < 2 or status[0] not in (b"HTTP/1.0", b"HTTP/1.1") \
                or len(status[1]) != 3 or any(c < 48 or c > 57 for c in status[1]):
            raise NetworkError("http-malformed-status")
        if any(c < 32 or c > 126 for c in raw.split(b"\r\n", 1)[0]):
            raise NetworkError("http-malformed-status")
        self.status = int(status[1])
        if not 200 <= self.status <= 599:
            raise NetworkError("http-unsupported-status")
        self.headers = {}
        for line in lines:
            name, value = _header(line)
            if name in self.headers:
                if name in ("content-length", "transfer-encoding", "content-encoding"):
                    raise NetworkError("http-duplicate-framing")
                value = self.headers[name] + ", " + value
            self.headers[name] = value
        length = self.headers.get("content-length")
        transfer = self.headers.get("transfer-encoding")
        if self.headers.get("content-encoding", "identity").lower() != "identity":
            raise NetworkError("http-compression-unsupported")
        if length is not None and transfer is not None:
            raise NetworkError("http-conflicting-framing")
        if transfer is not None and transfer.lower() != "chunked":
            raise NetworkError("http-transfer-unsupported")
        if length is not None:
            if not length or len(length) > 10 or any(c not in "0123456789" for c in length):
                raise NetworkError("http-invalid-length")
            self.remaining = int(length)
            if self.remaining > self.max_body:
                raise NetworkError("http-body-too-large")
        if self.status in (204, 304):
            if transfer is not None or self.status == 204 and self.remaining:
                raise NetworkError("http-invalid-empty-response")
            self.mode = "empty"
        elif transfer is not None:
            self.mode = "chunk-size"
        elif length is not None:
            self.mode = "length"
        else:
            self.mode = "close"

    def feed(self, data, eof=False):
        self.buffer.extend(data)
        while not self.done:
            if self.mode == "headers":
                end = self.buffer.find(b"\r\n\r\n")
                if end < 0:
                    if len(self.buffer) > _HEADER_LIMIT:
                        raise NetworkError("http-headers-too-large")
                    break
                if end + 4 > _HEADER_LIMIT:
                    raise NetworkError("http-headers-too-large")
                raw = bytes(self.buffer[:end])
                del self.buffer[:end + 4]
                self._headers(raw)
            elif self.mode == "empty":
                self.done = True
            elif self.mode == "close":
                self._body(self.buffer)
                self.buffer = bytearray()
                self.done = eof
                break
            elif self.mode == "length":
                take = min(len(self.buffer), self.remaining)
                self._body(self.buffer[:take])
                del self.buffer[:take]
                self.remaining -= take
                self.done = self.remaining == 0
                if not self.done:
                    break
            elif self.mode in ("chunk-size", "trailers"):
                end = self.buffer.find(b"\r\n")
                if end < 0:
                    limit = 1024 if self.mode == "chunk-size" else _HEADER_LIMIT - self.trailer_size
                    if len(self.buffer) > limit:
                        raise NetworkError("http-framing-too-large")
                    break
                line = bytes(self.buffer[:end])
                del self.buffer[:end + 2]
                self.framing_size += end + 2
                if self.framing_size > 65536:
                    raise NetworkError("http-framing-too-large")
                if self.mode == "trailers":
                    self.trailer_size += end + 2
                    if self.trailer_size > _HEADER_LIMIT:
                        raise NetworkError("http-trailers-too-large")
                    if not line:
                        self.done = True
                    else:
                        name, _ = _header(line)
                        if name in ("content-length", "transfer-encoding", "content-encoding",
                                    "host", "authorization", "connection", "trailer"):
                            raise NetworkError("http-invalid-trailer")
                else:
                    if len(line) > 1024:
                        raise NetworkError("http-framing-too-large")
                    size = line.split(b";", 1)[0]
                    if not size or len(size) > 8 or any(c not in b"0123456789abcdefABCDEF" for c in size):
                        raise NetworkError("http-invalid-chunk")
                    if any(c < 32 or c > 126 for c in line):
                        raise NetworkError("http-invalid-chunk")
                    self.remaining = int(size, 16)
                    if self.remaining + len(self.body) > self.max_body:
                        raise NetworkError("http-body-too-large")
                    self.mode = "chunk-data" if self.remaining else "trailers"
            elif self.mode == "chunk-data":
                take = min(len(self.buffer), self.remaining)
                self._body(self.buffer[:take])
                del self.buffer[:take]
                self.remaining -= take
                if self.remaining:
                    break
                self.mode = "chunk-end"
            elif self.mode == "chunk-end":
                if len(self.buffer) < 2:
                    break
                if self.buffer[:2] != b"\r\n":
                    raise NetworkError("http-invalid-chunk")
                del self.buffer[:2]
                self.framing_size += 2
                self.mode = "chunk-size"
        if self.done:
            if self.buffer:
                raise NetworkError("http-extra-data")
            return {"status": self.status, "headers": self.headers, "body": bytes(self.body)}
        if eof:
            raise NetworkError("http-truncated")
        return None


class HTTPSRequest(_Operation):
    def __init__(self, ip, host, path, token, ca_file, timeout_ms=15000, max_body=32768):
        super().__init__(timeout_ms)
        self._address = (_ipv4(ip), 443)
        self._host = _hostname(host)
        if not isinstance(path, str) or not path.startswith("/") or len(path) > 2048 \
                or any(ord(c) < 33 or ord(c) > 126 or c == "#" for c in path):
            raise NetworkError("invalid-path")
        if not isinstance(token, str) or not 1 <= len(token) <= 4096 \
                or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise NetworkError("invalid-token")
        if not isinstance(max_body, int) or not 0 <= max_body <= 32768:
            raise NetworkError("invalid-body-limit")
        self._ca_file = ca_file
        self._request = (
            "GET %s HTTP/1.1\r\nHost: %s\r\nAuthorization: Bearer %s\r\n"
            "Accept: application/vnd.github+json\r\nX-GitHub-Api-Version: 2026-03-10\r\n"
            "User-Agent: PumpkinPi\r\nConnection: close\r\nAccept-Encoding: identity\r\n\r\n"
            % (path, self._host, token)).encode()
        self._offset = 0
        self._state = "connect"
        self._context = None
        self._poll = None
        self._parser = _HTTPResponse(max_body)

    def close(self):
        self._request = b""
        self._context = None
        self._poll = None
        super().close()

    def _tls_context(self):
        if not all(hasattr(ssl, name) for name in
                   ("SSLContext", "PROTOCOL_TLS_CLIENT", "CERT_REQUIRED")):
            raise NetworkError("tls-verification-unavailable")
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.verify_mode = ssl.CERT_REQUIRED
            if context.verify_mode != ssl.CERT_REQUIRED:
                raise NetworkError("tls-verification-unavailable")
            if hasattr(context, "check_hostname"):
                context.check_hostname = True
                if not context.check_hostname:
                    raise NetworkError("tls-verification-unavailable")
            context.load_verify_locations(cafile=self._ca_file)
            return context
        except Exception:
            raise NetworkError("tls-trust-configuration-failed") from None

    def _advance(self):
        if self._state == "connect":
            self._context = self._tls_context()
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.setblocking(False)
            self._poll = select.poll()
            self._poll.register(self._socket, select.POLLOUT | select.POLLERR | select.POLLHUP)
            try:
                self._socket.connect(self._address)
            except OSError as exc:
                code = exc.args[0] if exc.args else None
                if not _would_block(exc) and code not in (errno.EINPROGRESS, errno.EALREADY):
                    raise
            self._state = "connecting"
            return None
        if self._state == "connecting":
            events = self._poll.poll(0)
            if not events:
                return None
            if any(event[1] & (select.POLLERR | select.POLLHUP | select.POLLNVAL)
                   for event in events):
                raise NetworkError("tcp-connect-failed")
            if not any(event[1] & select.POLLOUT for event in events):
                return None
            if hasattr(socket, "SO_ERROR") and self._socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
                raise NetworkError("tcp-connect-failed")
            self._poll.unregister(self._socket)
            self._poll = None
            self._state = "wrap"
            return None
        if self._state == "wrap":
            self._socket = self._context.wrap_socket(
                self._socket, server_hostname=self._host, do_handshake_on_connect=False)
            self._state = "write"
            return None
        if self._state == "write":
            # mbedTLS drives the deferred handshake from write/read. No plaintext
            # is emitted until certificate and hostname verification succeed.
            part = self._request[self._offset:self._offset + 1024]
            try:
                count = self._socket.write(part)
            except OSError as exc:
                if _would_block(exc):
                    return None
                raise NetworkError("tls-write-failed") from None
            if count is None:
                return None
            if not isinstance(count, int) or count <= 0 or count > len(part):
                raise NetworkError("tls-short-write")
            self._offset += count
            if self._offset == len(self._request):
                self._request = b""
                self._state = "read"
            return None
        try:
            data = self._socket.read(1024)
        except OSError as exc:
            if _would_block(exc):
                return None
            raise NetworkError("tls-read-failed") from None
        if data is None:
            return None
        return self._parser.feed(data, eof=data == b"")
