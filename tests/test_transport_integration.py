from __future__ import annotations

import asyncio
import contextlib
import hashlib
import shutil
import ssl
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import h2.config
import h2.connection
import h2.errors
import h2.events
import pytest

from matic_sdk.client import HANDSHAKE_PATH, MaticClient
from matic_sdk.config import MaticConfig, TlsConfig
from matic_sdk.credentials import BotToken, serialize_token_request
from matic_sdk.protocol.grpc import GrpcFrameDecoder, frame_message
from matic_sdk.protocol.wire import encode_bytes_field
from matic_sdk.transport.h2 import (
    H2StreamReset,
    H2Transport,
    H2TransportClosed,
    H2TransportError,
)
from matic_sdk.transport.tls import TlsVerificationError

_RESET_PATH = "/test.Transport/Reset"
_GOAWAY_PATH = "/test.Transport/GoAway"
_HANG_PATH = "/test.Transport/Hang"


@dataclass(frozen=True, slots=True)
class _RecordedRequest:
    path: str
    headers: tuple[tuple[str, str], ...]
    messages: tuple[bytes, ...]
    data_frame_count: int


class _LocalHermesServer:
    """Minimal TLS/H2 gRPC peer used only over an ephemeral loopback socket."""

    def __init__(self, tmp_path: Path, *, alpn_protocols: tuple[str, ...]) -> None:
        openssl = shutil.which("openssl")
        if openssl is None:
            pytest.skip("OpenSSL is required for the local TLS integration tests")
        certificate_path = tmp_path / "synthetic-localhost-cert.pem"
        private_key_path = tmp_path / "synthetic-localhost-key.pem"
        subprocess.run(
            (
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-sha256",
                "-nodes",
                "-keyout",
                str(private_key_path),
                "-out",
                str(certificate_path),
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost,IP:127.0.0.1",
            ),
            check=True,
            capture_output=True,
            timeout=10,
        )

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certificate_path, private_key_path)
        context.set_alpn_protocols(list(alpn_protocols))

        certificate_pem = certificate_path.read_text("ascii")
        der = ssl.PEM_cert_to_DER_cert(certificate_pem)
        self.fingerprint = hashlib.sha256(der).hexdigest()
        self._context = context
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._errors: list[BaseException] = []
        self._request_event = asyncio.Event()
        self.requests: list[_RecordedRequest] = []
        self.selected_alpn_protocols: list[str | None] = []
        self.client_reset_streams: list[int] = []
        self.client_goaways = 0
        self.port = 0

    async def __aenter__(self) -> _LocalHermesServer:
        self._server = await asyncio.start_server(
            self._accept,
            host="127.0.0.1",
            port=0,
            ssl=self._context,
        )
        socket = self._server.sockets[0]
        self.port = int(socket.getsockname()[1])
        return self

    async def __aexit__(self, *_: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()
        for writer in tuple(self._writers):
            writer.close()
        for writer in tuple(self._writers):
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        if self._errors:
            raise BaseExceptionGroup("local H2 test server failed", self._errors)

    def _accept(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.create_task(self._handle(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def config(self, *, fingerprint: str | None = None) -> MaticConfig:
        return MaticConfig(
            "127.0.0.1",
            port=self.port,
            tls=TlsConfig.pinned(fingerprint or self.fingerprint),
            connect_timeout=1.0,
            operation_timeout=2.0,
        )

    async def wait_for_request(self, path: str) -> _RecordedRequest:
        async with asyncio.timeout(1.0):
            while True:
                request = next(
                    (item for item in self.requests if item.path == path),
                    None,
                )
                if request is not None:
                    return request
                self._request_event.clear()
                await self._request_event.wait()

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._writers.add(writer)
        try:
            ssl_object = writer.get_extra_info("ssl_object")
            assert ssl_object is not None
            selected_alpn = ssl_object.selected_alpn_protocol()
            self.selected_alpn_protocols.append(selected_alpn)
            if selected_alpn != "h2":
                return

            connection = h2.connection.H2Connection(
                config=h2.config.H2Configuration(
                    client_side=False,
                    header_encoding="utf-8",
                )
            )
            connection.initiate_connection()
            writer.write(connection.data_to_send())
            await writer.drain()

            headers_by_stream: dict[int, tuple[tuple[str, str], ...]] = {}
            body_by_stream: dict[int, bytearray] = {}
            data_frames_by_stream: dict[int, int] = {}
            close_after_flush = False
            while incoming := await reader.read(65_535):
                events = connection.receive_data(incoming)
                for event in events:
                    if isinstance(event, h2.events.RequestReceived):
                        headers_by_stream[event.stream_id] = tuple(event.headers)
                        body_by_stream[event.stream_id] = bytearray()
                        data_frames_by_stream[event.stream_id] = 0
                    elif isinstance(event, h2.events.DataReceived):
                        body_by_stream[event.stream_id].extend(event.data)
                        data_frames_by_stream[event.stream_id] += 1
                        connection.acknowledge_received_data(
                            event.flow_controlled_length,
                            event.stream_id,
                        )
                    elif isinstance(event, h2.events.StreamEnded):
                        path = dict(headers_by_stream[event.stream_id])[":path"]
                        decoder = GrpcFrameDecoder()
                        messages = decoder.feed(bytes(body_by_stream[event.stream_id]))
                        decoder.finish()
                        self.requests.append(
                            _RecordedRequest(
                                path=path,
                                headers=headers_by_stream[event.stream_id],
                                messages=messages,
                                data_frame_count=data_frames_by_stream[event.stream_id],
                            )
                        )
                        self._request_event.set()
                        if path == _RESET_PATH:
                            connection.reset_stream(
                                event.stream_id,
                                error_code=h2.errors.ErrorCodes.REFUSED_STREAM,
                            )
                        elif path == _GOAWAY_PATH:
                            connection.close_connection(
                                error_code=h2.errors.ErrorCodes.INTERNAL_ERROR
                            )
                            close_after_flush = True
                        elif path != _HANG_PATH:
                            connection.send_headers(
                                event.stream_id,
                                (
                                    (":status", "200"),
                                    ("content-type", "application/grpc"),
                                    ("x-server", "synthetic"),
                                ),
                            )
                            response = frame_message(f"response:{path}".encode())
                            split_at = max(1, len(response) // 2)
                            connection.send_data(
                                event.stream_id,
                                response[:split_at],
                            )
                            connection.send_data(
                                event.stream_id,
                                response[split_at:],
                            )
                            connection.send_headers(
                                event.stream_id,
                                (
                                    ("grpc-status", "0"),
                                    ("x-trailer", "complete"),
                                ),
                                end_stream=True,
                            )
                    elif isinstance(event, h2.events.StreamReset):
                        self.client_reset_streams.append(event.stream_id)
                    elif isinstance(event, h2.events.ConnectionTerminated):
                        self.client_goaways += 1

                pending = connection.data_to_send()
                if pending:
                    writer.write(pending)
                    await writer.drain()
                if close_after_flush:
                    return
        except (
            BrokenPipeError,
            ConnectionError,
            asyncio.IncompleteReadError,
            ssl.SSLError,
        ):
            pass
        except BaseException as error:
            self._errors.append(error)
        finally:
            self._writers.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


def _synthetic_token() -> BotToken:
    user_id = str(uuid.UUID(int=42))
    payload = encode_bytes_field(
        1,
        serialize_token_request(user_id),
    ) + encode_bytes_field(2, b"synthetic-test-secret")
    return BotToken.decode(payload)


async def test_authenticated_client_negotiates_pinned_h2_and_handshakes(
    tmp_path: Path,
) -> None:
    async with _LocalHermesServer(tmp_path, alpn_protocols=("h2",)) as server:
        token = _synthetic_token()
        client = await MaticClient.connect(server.config(), token=token)
        request = await server.wait_for_request(HANDSHAKE_PATH)

        assert client.connected
        assert client.connection_info is not None
        assert client.connection_info.alpn_protocol == "h2"
        assert client.connection_info.certificate_sha256 == server.fingerprint
        assert client.connection_info.verified is True
        assert not hasattr(client, "transport")
        assert dict(request.headers) == {
            ":method": "POST",
            ":scheme": "https",
            ":authority": f"127.0.0.1:{server.port}",
            ":path": HANDSHAKE_PATH,
            "content-type": "application/grpc",
            "te": "trailers",
            "user-agent": "unofficial-matic-sdk/0.1",
            "authorization": token.authorization_header(),
        }
        assert request.messages == (b"",)
        assert server.selected_alpn_protocols == ["h2"]

        await client.close()
        assert not client.connected


@pytest.mark.parametrize(
    ("alpn_protocols", "fingerprint", "message"),
    [
        (("h2",), "00" * 32, "does not match"),
        (("http/1.1",), None, "requires ALPN h2"),
    ],
)
async def test_connect_fails_closed_on_bad_pin_or_alpn(
    tmp_path: Path,
    alpn_protocols: tuple[str, ...],
    fingerprint: str | None,
    message: str,
) -> None:
    async with _LocalHermesServer(
        tmp_path,
        alpn_protocols=alpn_protocols,
    ) as server:
        transport = H2Transport(server.config(fingerprint=fingerprint))

        with pytest.raises(TlsVerificationError, match=message):
            await transport.connect()

        assert not transport.connected
        assert transport.tls_info is None
        assert server.requests == []


async def test_multiplexed_unary_dispatch_and_request_flow_control(
    tmp_path: Path,
) -> None:
    large_payload = b"x" * 200_000
    async with _LocalHermesServer(tmp_path, alpn_protocols=("h2",)) as server:
        async with H2Transport(server.config()) as transport:
            small, large = await asyncio.gather(
                transport.unary("/test.Transport/Small", b"small"),
                transport.unary("/test.Transport/Large", large_payload),
            )

            assert small.messages == (b"response:/test.Transport/Small",)
            assert large.messages == (b"response:/test.Transport/Large",)
            assert ("x-server", "synthetic") in small.headers
            assert ("x-trailer", "complete") in small.trailers
            assert small.status == large.status == 0

            small_request = await server.wait_for_request("/test.Transport/Small")
            large_request = await server.wait_for_request("/test.Transport/Large")
            assert small_request.messages == (b"small",)
            assert large_request.messages == (large_payload,)
            assert large_request.data_frame_count > 4


async def test_peer_reset_is_stream_local_but_goaway_terminates_transport(
    tmp_path: Path,
) -> None:
    async with _LocalHermesServer(tmp_path, alpn_protocols=("h2",)) as server:
        transport = H2Transport(server.config())
        await transport.connect()

        with pytest.raises(H2StreamReset, match="reset"):
            await transport.unary(_RESET_PATH, b"reset-me")
        recovered = await transport.unary("/test.Transport/AfterReset", b"still-open")
        assert recovered.messages == (b"response:/test.Transport/AfterReset",)

        with pytest.raises(H2TransportError, match="terminated"):
            await transport.unary(_GOAWAY_PATH, b"close-connection")
        with pytest.raises(H2TransportClosed, match="terminated"):
            await transport.unary("/test.Transport/AfterGoAway")

        writer = transport._writer
        assert writer is not None
        await transport.close()
        await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
        assert writer.is_closing()


async def test_local_close_fails_pending_stream_and_rejects_future_calls(
    tmp_path: Path,
) -> None:
    async with _LocalHermesServer(tmp_path, alpn_protocols=("h2",)) as server:
        transport = H2Transport(server.config())
        await transport.connect()
        stream = await transport.open_grpc_stream(_HANG_PATH)
        await stream.send_message(b"wait-for-close", end_stream=True)
        await server.wait_for_request(_HANG_PATH)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)

        await transport.close()

        with pytest.raises(H2TransportClosed, match="closed"):
            await pending
        with pytest.raises(H2TransportClosed, match="closed"):
            await transport.open_grpc_stream("/test.Transport/AfterClose")
        assert not transport.connected
