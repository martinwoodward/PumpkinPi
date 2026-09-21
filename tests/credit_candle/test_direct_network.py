import errno
import os
import shutil
import socket
import ssl
import struct
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from .support import ROOT
from providers import direct_network as net


def ticks_diff(now, then):
    return ((now - then + 32768) % 65536) - 32768


def dns_name(name):
    return b"".join(bytes((len(label),)) + label.encode() for label in name.split(".")) + b"\0"


def dns_record(owner=b"\xc0\x0c", kind=1, data=b"\x01\x02\x03\x04", cls=1):
    return owner + struct.pack("!HHIH", kind, cls, 60, len(data)) + data


def dns_reply(query, records=None, flags=0x8180, question=None):
    records = [dns_record()] if records is None else records
    question = query._packet[12:] if question is None else question
    return query._id + struct.pack("!HHHHH", flags, 1, len(records), 0, 0) + question + b"".join(records)


def ntp_reply(query, unix=1789722000, flags=0x24, stratum=2):
    packet = bytearray(48)
    packet[0], packet[1] = flags, stratum
    packet[24:32] = query._nonce
    packet[40:44] = struct.pack("!I", (unix + 2208988800) % 4294967296)
    packet[44:48] = b"\0\0\0\x01"
    return bytes(packet)


class FakeUDP:
    def __init__(self):
        self.sent = []
        self.incoming = []
        self.closed = False
        self.blocking = None
        self.send_error = None

    def setblocking(self, value):
        self.blocking = value

    def sendto(self, packet, address):
        if self.send_error:
            error, self.send_error = self.send_error, None
            raise error
        self.sent.append((packet, address))
        return len(packet)

    def recvfrom(self, size):
        if not self.incoming:
            raise OSError(errno.EAGAIN)
        packet, source = self.incoming.pop(0)
        return packet[:size], source

    def close(self):
        self.closed = True


class UDPTests(unittest.TestCase):
    def test_nonblocking_dns_and_close(self):
        sock = FakeUDP()
        query = net.DNSLookup("API.GITHUB.COM", "192.0.2.53")
        with patch.object(net.socket, "socket", return_value=sock), \
                patch.object(net.socket, "getaddrinfo", side_effect=AssertionError("blocking DNS")):
            self.assertIsNone(query.step(0, ticks_diff))
            self.assertFalse(sock.blocking)
            self.assertEqual(sock.sent[0][1], ("192.0.2.53", 53))
            self.assertIsNone(query.step(1, ticks_diff))
            sock.incoming.append((dns_reply(query), ("192.0.2.53", 53)))
            self.assertEqual(query.step(2, ticks_diff), "1.2.3.4")
            self.assertTrue(sock.closed)
            self.assertEqual(query.step(3, ticks_diff), "1.2.3.4")

    def test_udp_would_block_send_retries_and_tick_wrap_deadline(self):
        sock = FakeUDP()
        sock.send_error = OSError(errno.EAGAIN)
        query = net.NTPQuery("192.0.2.1", timeout_ms=10)
        with patch.object(net.socket, "socket", return_value=sock):
            self.assertIsNone(query.step(65530, ticks_diff))
            self.assertIsNone(query.step(65531, ticks_diff))
            self.assertEqual(len(sock.sent), 1)
            self.assertIsNone(query.step(3, ticks_diff))
            with self.assertRaisesRegex(net.NetworkError, "^network-timeout$"):
                query.step(4, ticks_diff)
        self.assertTrue(sock.closed)

    def test_cancel_before_first_step_has_no_io(self):
        query = net.DNSLookup("api.github.com", "192.0.2.53")
        query.close()
        with patch.object(net.socket, "socket", side_effect=AssertionError("unexpected socket")):
            with self.assertRaisesRegex(net.NetworkError, "operation-closed"):
                query.step(0, ticks_diff)

    def test_wrong_source_port_address_and_oversize_close(self):
        for source, data in [(("192.0.2.2", 123), None), (("192.0.2.1", 124), None),
                             (("192.0.2.1", 123), bytes(513))]:
            with self.subTest(source=source, oversized=data is not None):
                sock = FakeUDP()
                query = net.NTPQuery("192.0.2.1")
                sock.incoming.append((ntp_reply(query) if data is None else data, source))
                with patch.object(net.socket, "socket", return_value=sock):
                    query.step(0, ticks_diff)
                    with self.assertRaises(net.NetworkError):
                        query.step(1, ticks_diff)
                self.assertTrue(sock.closed)

    def test_invalid_addresses_are_not_resolved(self):
        for value in ("dns.example", "1.2.3", "1.2.3.256", "01.2.3.4", "1.2.3.-1", ""):
            with self.subTest(value=value), self.assertRaises(net.NetworkError):
                net.NTPQuery(value)


class DNSTests(unittest.TestCase):
    def setUp(self):
        self.query = net.DNSLookup("api.github.com", "192.0.2.53")

    def test_cname_and_out_of_order_a_answers(self):
        records = [
            dns_record(dns_name("target.github.com")),
            dns_record(kind=5, data=dns_name("target.github.com")),
        ]
        self.assertEqual(self.query._parse(dns_reply(self.query, records)), "1.2.3.4")

    def test_compressed_cname_rdata(self):
        # target.github.com reuses the "github.com" suffix in the question.
        alias = b"\x06target\xc0\x10"
        records = [dns_record(kind=5, data=alias),
                   dns_record(dns_name("target.github.com"))]
        self.assertEqual(self.query._parse(dns_reply(self.query, records)), "1.2.3.4")

    def test_flags_rcode_transaction_question_and_limits(self):
        valid = dns_reply(self.query)
        cases = [b"", b"x" + valid[1:],
                 dns_reply(self.query, flags=0x0180),
                 dns_reply(self.query, flags=0x8380),
                 dns_reply(self.query, flags=0x8980),
                 dns_reply(self.query, flags=0x81C0),
                 dns_reply(self.query, flags=0x8183),
                 dns_reply(self.query, question=dns_name("evil.example") + b"\0\1\0\1"),
                 dns_reply(self.query, question=dns_name("api.github.com") + b"\0\x1c\0\1"),
                 dns_reply(self.query, [dns_record()] * 33),
                 valid[:4] + b"\0\2" + valid[6:], valid + b"x"]
        for packet in cases:
            with self.subTest(packet=packet[:12]), self.assertRaises(net.NetworkError):
                self.query._parse(packet)

    def test_bounded_malformed_names_records_and_truncation(self):
        valid = dns_reply(self.query)
        for length in range(len(valid)):
            with self.subTest(length=length), self.assertRaises(net.NetworkError):
                self.query._parse(valid[:length])
        for record in [
                dns_record(owner=b"\xc0\xff"),
                dns_record(owner=b"\xc0\x00"),
                dns_record(owner=b"\x80bad"),
                dns_record(data=b"\1\2\3"),
                dns_record(cls=3),
                dns_record(kind=5, data=b"\xc0\xff"),
                dns_record(kind=5, data=b"\xc0\x0cX"),
                dns_record(owner=b"\xff\xff"),
                dns_record(owner=b"\x01\xff\0")]:
            with self.subTest(record=record), self.assertRaises(net.NetworkError):
                self.query._parse(dns_reply(self.query, [record]))
        # A self-referential owner pointer cannot spin.
        offset = len(self.query._packet)
        pointer = bytes((0xC0 | (offset >> 8), offset & 255))
        with self.assertRaises(net.NetworkError):
            self.query._parse(dns_reply(self.query, [dns_record(owner=pointer)]))

    def test_alias_cycle_ambiguity_missing_or_unrelated_answers(self):
        cases = [
            [dns_record(kind=5, data=dns_name("api.github.com"))],
            [dns_record(), dns_record(kind=5, data=dns_name("other.example"))],
            [dns_record(kind=5, data=dns_name("one.example")),
             dns_record(kind=5, data=dns_name("two.example"))],
            [dns_record(owner=dns_name("evil.example"))],
            [dns_record(kind=28, data=bytes(16))], [],
        ]
        for records in cases:
            with self.subTest(records=records), self.assertRaises(net.NetworkError):
                self.query._parse(dns_reply(self.query, records))

    def test_alias_chain_bound(self):
        records = []
        owner = "api.github.com"
        for index in range(10):
            target = "n%d.example" % index
            records.append(dns_record(dns_name(owner), 5, dns_name(target)))
            owner = target
        records.append(dns_record(dns_name(owner)))
        with self.assertRaisesRegex(net.NetworkError, "dns-alias-limit"):
            self.query._parse(dns_reply(self.query, records))

    def test_unknown_answer_and_invalid_labels_are_rejected(self):
        with self.assertRaisesRegex(net.NetworkError, "dns-unsupported-answer"):
            self.query._parse(dns_reply(self.query, [dns_record(), dns_record(kind=99)]))
        with self.assertRaisesRegex(net.NetworkError, "dns-malformed-name"):
            self.query._parse(dns_reply(self.query, [dns_record(owner=dns_name("-bad.example"))]))

    def test_compression_hop_bound(self):
        packet = bytearray(bytes(12) + dns_name("api.github.com"))
        offset = 12
        for _ in range(18):
            pointer = bytes((0xC0 | (offset >> 8), offset & 255))
            offset = len(packet)
            packet.extend(pointer)
        with self.assertRaisesRegex(net.NetworkError, "dns-compression-limit"):
            net._dns_name(packet, offset)


class NTPTests(unittest.TestCase):
    def test_nonce_is_fresh_and_transmit_echo_required(self):
        one, two = net.NTPQuery("192.0.2.1"), net.NTPQuery("192.0.2.1")
        self.assertNotEqual(one._nonce, two._nonce)
        self.assertEqual(one._packet[40:48], one._nonce)
        self.assertEqual(one._packet[0], 0x23)
        self.assertEqual(one._parse(ntp_reply(one)), 1789722000)
        with self.assertRaisesRegex(net.NetworkError, "ntp-challenge-mismatch"):
            two._parse(ntp_reply(one))

    def test_2036_era_and_date_bounds(self):
        query = net.NTPQuery("192.0.2.1")
        for unix in (1577836800, 2085978495, 2085978496, 2208988800, 4102444800):
            with self.subTest(unix=unix):
                self.assertEqual(query._parse(ntp_reply(query, unix)), unix)
        for unix in (1577836799, 4102444801):
            with self.subTest(unix=unix), self.assertRaisesRegex(net.NetworkError, "out-of-range"):
                query._parse(ntp_reply(query, unix))

    def test_invalid_server_stratum_mode_version_leap_and_zero(self):
        query = net.NTPQuery("192.0.2.1")
        cases = [ntp_reply(query, flags=flags) for flags in (0x23, 0x25, 0x14, 0x2C, 0xE4)]
        cases += [ntp_reply(query, stratum=stratum) for stratum in (0, 16, 255)]
        cases += [ntp_reply(query)[:47], ntp_reply(query)[:40] + bytes(8)]
        for packet in cases:
            with self.subTest(packet=packet[:2]), self.assertRaises(net.NetworkError):
                query._parse(packet)
        self.assertEqual(query._parse(ntp_reply(query, flags=0x1C)), 1789722000)

    def test_ntp_success_closes_socket(self):
        sock = FakeUDP()
        query = net.NTPQuery("192.0.2.1")
        sock.incoming.append((ntp_reply(query), ("192.0.2.1", 123)))
        with patch.object(net.socket, "socket", return_value=sock):
            query.step(0, ticks_diff)
            self.assertEqual(query.step(1, ticks_diff), 1789722000)
        self.assertTrue(sock.closed)


class HTTPParserTests(unittest.TestCase):
    def parse(self, packet, size=1, limit=32768, eof=False):
        parser = net._HTTPResponse(limit)
        result = None
        for start in range(0, len(packet), size):
            result = parser.feed(packet[start:start + size])
        if eof:
            result = parser.feed(b"", eof=True)
        return result

    def test_content_length_fragmentation_and_case_insensitive_headers(self):
        result = self.parse(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nX-Test: yes\r\n\r\nhello")
        self.assertEqual(result, {"status": 200, "headers": {"content-length": "5", "x-test": "yes"},
                                  "body": b"hello"})

    def test_chunked_with_extensions_and_bounded_trailers(self):
        packet = (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                  b"2;some=value\r\nhe\r\n3\r\nllo\r\n0\r\nX-Checksum: ok\r\n\r\n")
        for size in (1, 2, 7, 1024):
            with self.subTest(size=size):
                result = self.parse(packet, size=size)
                self.assertEqual(result["body"], b"hello")
                self.assertNotIn("x-checksum", result["headers"])

    def test_close_delimited_and_empty_responses(self):
        result = self.parse(b"HTTP/1.0 200 OK\r\n\r\nhello", eof=True)
        self.assertEqual(result["body"], b"hello")
        for status in (204, 304):
            self.assertEqual(self.parse(("HTTP/1.1 %s Empty\r\n\r\n" % status).encode())["body"], b"")
        self.assertEqual(self.parse(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")["body"], b"")

    def test_redirect_and_error_are_returned_not_followed(self):
        for code in (301, 302, 307, 401, 403, 429, 500):
            packet = ("HTTP/1.1 %d Test\r\nLocation: https://evil.example/\r\n"
                      "Retry-After: 30\r\nContent-Length: 0\r\n\r\n" % code).encode()
            result = self.parse(packet)
            self.assertEqual(result["status"], code)
            self.assertEqual(result["headers"]["retry-after"], "30")

    def test_malformed_or_contradictory_headers_and_status(self):
        cases = [
            b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx",
            b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip, chunked\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nContent-Encoding: identity\r\nContent-Encoding: identity\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nBad Header: x\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nX: good\r\n folded\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nX: bad\x00value\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nX: \xff\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nNoColon\r\n\r\n",
            b"HTTP/1.1 204 Empty\r\nContent-Length: 1\r\n\r\n",
            b"HTTP/1.1 304 Empty\r\nTransfer-Encoding: chunked\r\n\r\n",
            b"HTTP/2 200 OK\r\n\r\n", b"HTTP/1.1 101 Switch\r\n\r\n",
            b"HTTP/1.1 600 Bad\r\n\r\n", b"HTTP/1.1 abc Bad\r\n\r\n",
            b"HTTP/1.1 200 Bad\x01\r\n\r\n",
        ]
        for length in ("-1", "+1", "1, 1", "one", "", "99999999999"):
            cases.append(("HTTP/1.1 200 OK\r\nContent-Length: %s\r\n\r\n" % length).encode())
        for packet in cases:
            with self.subTest(packet=packet[:80]), self.assertRaises(net.NetworkError):
                self.parse(packet, size=1024)

    def test_body_header_chunk_and_trailer_bounds(self):
        cases = [
            b"HTTP/1.1 200 OK\r\nX: " + b"a" * 8192,
            b"HTTP/1.1 200 OK\r\nX: " + b"a" * 8192 + b"\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nContent-Length: 32769\r\n\r\n",
            b"HTTP/1.0 200 OK\r\n\r\n" + b"a" * 32769,
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n8001\r\n",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\nX: " + b"a" * 8192,
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\nX: " + b"a" * 8192 + b"\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1;" + b"a" * 1024 + b"\r\n",
        ]
        for packet in cases:
            with self.subTest(length=len(packet)), self.assertRaises(net.NetworkError):
                self.parse(packet, size=1024)
        for mode in (b"Content-Length: 5", b"Transfer-Encoding: chunked", b""):
            packet = b"HTTP/1.1 200 OK\r\n" + mode + b"\r\n\r\n"
            if mode == b"Transfer-Encoding: chunked":
                packet += b"5\r\nhello\r\n0\r\n\r\n"
            else:
                packet += b"hello"
            with self.subTest(mode=mode), self.assertRaises(net.NetworkError):
                self.parse(packet, size=1024, limit=4)

    def test_invalid_chunks_and_forbidden_trailers(self):
        prefix = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        for payload in (b"-1\r\n", b"+1\r\n", b"xyz\r\n", b"\r\n", b"1 \r\n",
                        b"1;bad\x00\r\n", b"1\r\nxXX", b"0\r\nContent-Length: 0\r\n\r\n",
                        b"0\r\nTransfer-Encoding: chunked\r\n\r\n",
                        b"0\r\n folded\r\n\r\n", b"0\r\n\r\nextra"):
            with self.subTest(payload=payload), self.assertRaises(net.NetworkError):
                self.parse(prefix + payload, size=1024)

    def test_truncation_and_extra_data(self):
        packets = [
            b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n",
        ]
        for packet in packets:
            for end in range(len(packet)):
                with self.subTest(end=end, kind=packet[17:35]), self.assertRaises(net.NetworkError):
                    self.parse(packet[:end], size=1024, eof=True)
        with self.assertRaisesRegex(net.NetworkError, "http-extra-data"):
            self.parse(packets[0] + b"extra", size=1024)


class FakeTCP:
    def __init__(self):
        self.closed = False
        self.connected_to = None
        self.blocking = None

    def setblocking(self, value):
        self.blocking = value

    def connect(self, address):
        self.connected_to = address
        raise OSError(errno.EINPROGRESS)

    def getsockopt(self, *args):
        return 0

    def close(self):
        self.closed = True


class FakePoll:
    def __init__(self):
        self.events = [(1, net.select.POLLOUT)]

    def register(self, *args):
        pass

    def unregister(self, *args):
        pass

    def poll(self, timeout):
        assert timeout == 0
        return self.events


class MicroPythonTLS:
    """Deliberately no send, recv, setblocking, or do_handshake methods."""

    def __init__(self):
        self.writes = []
        self.write_results = []
        self.read_results = [b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"]
        self.closed = False
        self.io_calls = 0

    def write(self, data):
        self.io_calls += 1
        result = self.write_results.pop(0) if self.write_results else len(data)
        if isinstance(result, Exception):
            raise result
        if result is not None and result > 0:
            self.writes.append(data[:result])
        return result

    def read(self, size):
        assert size <= 1024
        self.io_calls += 1
        result = self.read_results.pop(0) if self.read_results else None
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed = True


class MicroPythonContext:
    __slots__ = ("verify_mode", "stream", "cafile", "kwargs", "raw")

    def __init__(self, stream):
        self.stream = stream
        self.verify_mode = None
        self.cafile = None
        self.kwargs = None
        self.raw = None

    def load_verify_locations(self, cafile):
        self.cafile = cafile

    def wrap_socket(self, raw, **kwargs):
        self.raw, self.kwargs = raw, kwargs
        return self.stream


class HTTPSStateTests(unittest.TestCase):
    def setUp(self):
        self.tcp, self.stream, self.poll = FakeTCP(), MicroPythonTLS(), FakePoll()
        self.context = MicroPythonContext(self.stream)
        self.patches = [
            patch.object(net.socket, "socket", return_value=self.tcp),
            patch.object(net.select, "poll", return_value=self.poll),
            patch.object(net.ssl, "SSLContext", return_value=self.context),
            patch.object(net.socket, "getaddrinfo", side_effect=AssertionError("blocking DNS")),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def request(self, **kwargs):
        args = dict(ip="192.0.2.1", host="api.github.com", path="/test",
                    token="test-secret", ca_file="roots.pem")
        args.update(kwargs)
        return net.HTTPSRequest(**args)

    def drive(self, request):
        for index in range(100):
            before = self.stream.io_calls
            result = request.step(index, ticks_diff)
            self.assertLessEqual(self.stream.io_calls - before, 1)
            if result is not None:
                return result
        self.fail("request did not complete")

    def test_micropython_stream_shape_partial_writes_and_would_block(self):
        self.stream.write_results = [
            None, OSError(errno.EAGAIN), ssl.SSLWantReadError(), ssl.SSLWantWriteError(), 3, 5]
        self.stream.read_results[:0] = [None, OSError(errno.EAGAIN), ssl.SSLWantReadError(),
                                        ssl.SSLWantWriteError()]
        request = self.request()
        result = self.drive(request)
        self.assertEqual(result["body"], b"{}")
        self.assertTrue(self.stream.closed)
        self.assertFalse(self.tcp.blocking)
        self.assertEqual(self.tcp.connected_to, ("192.0.2.1", 443))
        self.assertEqual(self.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(self.context.cafile, "roots.pem")
        self.assertEqual(self.context.kwargs, {"server_hostname": "api.github.com",
                                              "do_handshake_on_connect": False})
        wire = b"".join(self.stream.writes)
        for header in (b"GET /test HTTP/1.1\r\n", b"Host: api.github.com\r\n",
                       b"Authorization: Bearer test-secret\r\n",
                       b"Accept: application/vnd.github+json\r\n",
                       b"X-GitHub-Api-Version: 2026-03-10\r\n",
                       b"User-Agent: PumpkinPi\r\n", b"Connection: close\r\n",
                       b"Accept-Encoding: identity\r\n"):
            self.assertIn(header, wire)
        self.assertEqual(wire.count(b"Authorization:"), 1)
        self.assertEqual(request._request, b"")

    def test_large_request_writes_are_bounded(self):
        request = self.request(token="t" * 4096)
        self.drive(request)
        self.assertTrue(all(len(data) <= 1024 for data in self.stream.writes))

    def test_tcp_poll_waits_and_timeout_covers_all_states(self):
        request = self.request(timeout_ms=10)
        self.poll.events = []
        request.step(65530, ticks_diff)
        request.step(65531, ticks_diff)
        with self.assertRaisesRegex(net.NetworkError, "network-timeout"):
            request.step(4, ticks_diff)
        self.assertTrue(self.tcp.closed)
        self.assertEqual(self.stream.writes, [])
        request = self.request(timeout_ms=10)
        self.poll.events = [(1, net.select.POLLOUT)]
        self.stream.write_results = [None] * 20
        for offset in range(10):
            request.step((65530 + offset) % 65536, ticks_diff)
        with self.assertRaisesRegex(net.NetworkError, "network-timeout"):
            request.step(4, ticks_diff)
        self.assertTrue(self.stream.closed)

    def test_poll_error_closes_tcp_without_tls_write(self):
        self.poll.events = [(1, net.select.POLLERR)]
        request = self.request()
        request.step(0, ticks_diff)
        with self.assertRaisesRegex(net.NetworkError, "tcp-connect-failed"):
            request.step(1, ticks_diff)
        self.assertTrue(self.tcp.closed)
        self.assertEqual(self.stream.writes, [])

    def test_certificate_and_socket_errors_are_safe_and_closed(self):
        self.stream.write_results = [ssl.SSLCertVerificationError("test-secret raw certificate")]
        request = self.request()
        with self.assertRaisesRegex(net.NetworkError, "^tls-write-failed$") as raised:
            self.drive(request)
        self.assertNotIn("test-secret", str(raised.exception))
        self.assertEqual(self.stream.writes, [])
        self.assertTrue(self.stream.closed)
        self.assertEqual(request._request, b"")

    def test_parse_failure_is_not_empty_success_and_closes_stream(self):
        self.stream.read_results = [b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nx", b""]
        request = self.request()
        with self.assertRaisesRegex(net.NetworkError, "http-truncated"):
            self.drive(request)
        self.assertTrue(self.stream.closed)

    def test_read_errors_and_zero_writes_close_without_exposing_data(self):
        self.stream.read_results = [OSError("test-secret response body")]
        with self.assertRaisesRegex(net.NetworkError, "^tls-read-failed$"):
            self.drive(self.request())
        self.assertTrue(self.stream.closed)
        self.stream.closed = False
        self.stream.write_results = [0]
        with self.assertRaisesRegex(net.NetworkError, "^tls-short-write$"):
            self.drive(self.request())
        self.assertTrue(self.stream.closed)

    def test_wrap_failure_closes_raw_socket_without_sending(self):
        with patch.object(MicroPythonContext, "wrap_socket",
                          side_effect=OSError("test-secret certificate data")):
            with self.assertRaisesRegex(net.NetworkError, "^network-io-failed$"):
                self.drive(self.request())
        self.assertTrue(self.tcp.closed)
        self.assertEqual(self.stream.writes, [])

    def test_redirect_never_connects_again_or_forwards_token(self):
        self.stream.read_results = [
            b"HTTP/1.1 302 Found\r\nLocation: https://evil.example/\r\nContent-Length: 0\r\n\r\n"]
        request = self.request()
        result = self.drive(request)
        self.assertEqual(result["status"], 302)
        self.assertEqual(self.drive(request), result)
        self.assertEqual(self.tcp.connected_to, ("192.0.2.1", 443))
        self.assertNotIn(b"evil.example", b"".join(self.stream.writes))
        self.assertEqual(b"".join(self.stream.writes).count(b"Authorization:"), 1)

    def test_missing_tls_features_and_ca_failure_fail_closed(self):
        request = self.request()
        with patch.object(net.ssl, "PROTOCOL_TLS_CLIENT", create=True) as feature:
            del net.ssl.PROTOCOL_TLS_CLIENT
            with self.assertRaisesRegex(net.NetworkError, "tls-verification-unavailable"):
                self.drive(request)
            net.ssl.PROTOCOL_TLS_CLIENT = feature
        self.assertIsNone(self.tcp.connected_to)
        with patch.object(MicroPythonContext, "load_verify_locations",
                          side_effect=OSError("secret trust file contents")):
            with self.assertRaisesRegex(net.NetworkError, "^tls-trust-configuration-failed$"):
                self.drive(self.request())
        self.assertEqual(self.stream.writes, [])

    def test_invalid_request_fields_are_rejected_before_io(self):
        for changes in ({"host": "api.github.com\r\nX: x"}, {"path": "/x\r\nX: x"},
                        {"path": "https://evil.example/"}, {"path": "/has space"},
                        {"path": "/#fragment"}, {"token": "x\r\nX: y"},
                        {"token": ""}, {"token": "contains space"}, {"max_body": 32769},
                        {"ip": "api.github.com"}, {"timeout_ms": 0}):
            with self.subTest(changes=changes), self.assertRaises(net.NetworkError):
                self.request(**changes)
        self.assertIsNone(self.tcp.connected_to)

    def test_cancel_closes_tls_and_discards_request(self):
        request = self.request()
        for index in range(3):
            request.step(index, ticks_diff)
        request.close()
        self.assertTrue(self.stream.closed)
        self.assertEqual(request._request, b"")
        with self.assertRaisesRegex(net.NetworkError, "operation-closed"):
            request.step(4, ticks_diff)


class RealTLSLoopbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.openssl = shutil.which("openssl")
        if not cls.openssl:
            raise RuntimeError("openssl is required for certificate validation tests")
        cls.work = ROOT / ".test-work" / ("direct-network-tls-%d" % os.getpid())
        cls.work.mkdir(parents=True, exist_ok=False)
        cls.addClassCleanup(shutil.rmtree, cls.work)
        cls.run_ssl("req", "-new", "-x509", "-newkey", "ec", "-pkeyopt",
                    "ec_paramgen_curve:prime256v1", "-nodes", "-sha256", "-days", "2",
                    "-subj", "/CN=Loopback Test Root", "-keyout", "ca.key", "-out", "ca.pem")
        cls.run_ssl("req", "-new", "-x509", "-newkey", "ec", "-pkeyopt",
                    "ec_paramgen_curve:prime256v1", "-nodes", "-sha256", "-days", "2",
                    "-subj", "/CN=Wrong Test Root", "-keyout", "wrong.key", "-out", "wrong.pem")
        cls.run_ssl("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                    "-nodes", "-subj", "/CN=api.github.com", "-keyout", "server.key", "-out", "server.csr")
        (cls.work / "extensions.cnf").write_text(
            "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
            "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:api.github.com\n")
        cls.run_ssl("x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
                    "-set_serial", "2", "-days", "2", "-sha256", "-extfile", "extensions.cnf",
                    "-out", "server.pem")
        (cls.work / "index").write_text("")
        (cls.work / "serial").write_text("03\n")
        (cls.work / "ca.cnf").write_text(
            "[ca]\ndefault_ca=local\n[local]\ndatabase=index\nserial=serial\n"
            "new_certs_dir=.\ncertificate=ca.pem\nprivate_key=ca.key\ndefault_md=sha256\n"
            "policy=policy\nx509_extensions=server\n[policy]\ncommonName=supplied\n"
            "[server]\nbasicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n"
            "subjectAltName=DNS:api.github.com\n")
        cls.run_ssl("ca", "-batch", "-config", "ca.cnf", "-in", "server.csr", "-out", "expired.pem",
                    "-startdate", "20200101000000Z", "-enddate", "20200102000000Z")

    @classmethod
    def run_ssl(cls, *args):
        subprocess.run([cls.openssl] + list(args), cwd=cls.work, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def exchange(self, cert="server.pem", ca="ca.pem", host="api.github.com", expected_error=False):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)
        address = listener.getsockname()
        received, errors = [], []
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.work / cert), str(self.work / "server.key"))

        def server():
            try:
                conn, _ = listener.accept()
                conn.settimeout(3)
                with conn:
                    with context.wrap_socket(conn, server_side=True) as secure:
                        request = b""
                        while b"\r\n\r\n" not in request:
                            part = secure.recv(1024)
                            if not part:
                                break
                            received.append(part)
                            request += part
                        secure.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                listener.close()

        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        request = net.HTTPSRequest("127.0.0.1", host, "/loopback", "loopback-test-token",
                                   str(self.work / ca), timeout_ms=3000)
        request._address = address
        result, failure = None, None
        start = time.monotonic()
        try:
            while time.monotonic() - start < 4:
                try:
                    result = request.step(int(time.monotonic() * 1000), lambda a, b: a - b)
                except net.NetworkError as exc:
                    failure = exc
                    break
                if result is not None:
                    break
                time.sleep(0.001)
        finally:
            request.close()
            thread.join(4)
        self.assertFalse(thread.is_alive(), "loopback server did not stop")
        if expected_error:
            self.assertIsNotNone(failure)
            self.assertNotIn("loopback-test-token", str(failure))
            self.assertNotEqual(str(failure), "network-timeout")
            self.assertEqual(received, [], "untrusted server received application bytes")
            self.assertTrue(errors)
        else:
            self.assertIsNone(failure)
            self.assertEqual(errors, [])
            self.assertEqual(result["body"], b"{}")
            self.assertIn(b"Authorization: Bearer loopback-test-token\r\n", b"".join(received))

    def test_trusted_chain_and_hostname(self):
        self.exchange()

    def test_wrong_ca_receives_no_application_bytes(self):
        self.exchange(ca="wrong.pem", expected_error=True)

    def test_wrong_hostname_receives_no_application_bytes(self):
        self.exchange(host="wrong.example", expected_error=True)

    def test_expired_certificate_receives_no_application_bytes(self):
        self.exchange(cert="expired.pem", expected_error=True)


if __name__ == "__main__":
    unittest.main()
