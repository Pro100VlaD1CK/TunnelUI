import asyncio
import os
import socket
import stat
import struct
import uuid

from tunnelui.agent.dispatcher import AgentDispatcher, DispatchError
from tunnelui.agent.protocol import (
    MAX_MESSAGE_BYTES,
    AgentResponse,
    ProtocolError,
    decode_request,
    encode_response,
)

NIL_REQUEST_ID = uuid.UUID(int=0)
REQUEST_READ_TIMEOUT = 5.0


class AgentServer:
    def __init__(self, dispatcher: AgentDispatcher, allowed_uid: int):
        self.dispatcher = dispatcher
        self.allowed_uid = allowed_uid

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = _peer_credentials(writer)
        if peer is None or peer[1] != self.allowed_uid:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), REQUEST_READ_TIMEOUT)
            except TimeoutError:
                pass
            return
        peer_pid = peer[0]
        request_id = NIL_REQUEST_ID
        try:
            data = await asyncio.wait_for(reader.readuntil(b"\n"), REQUEST_READ_TIMEOUT)
            if len(data) - 1 > MAX_MESSAGE_BYTES:
                raise ProtocolError("request_too_large")
            request, arguments = decode_request(data[:-1])
            request_id = request.request_id
            result = await self.dispatcher.dispatch(request, arguments, peer_pid)
            response = AgentResponse(request_id=request_id, ok=True, result=result)
        except asyncio.LimitOverrunError:
            response = AgentResponse(request_id=request_id, ok=False, error="request_too_large")
        except asyncio.IncompleteReadError:
            response = AgentResponse(
                request_id=request_id, ok=False, error="malformed_request"
            )
        except TimeoutError:
            response = AgentResponse(request_id=request_id, ok=False, error="request_timeout")
        except ProtocolError as error:
            response = AgentResponse(request_id=request_id, ok=False, error=error.code)
        except DispatchError as error:
            response = AgentResponse(request_id=request_id, ok=False, error=error.code)
        except Exception:
            response = AgentResponse(request_id=request_id, ok=False, error="agent_failure")
        try:
            try:
                payload = encode_response(response)
            except ProtocolError:
                payload = encode_response(
                    AgentResponse(request_id=request_id, ok=False, error="response_too_large")
                )
            writer.write(payload)
            await asyncio.wait_for(writer.drain(), REQUEST_READ_TIMEOUT)
        except (OSError, TimeoutError):
            pass
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), REQUEST_READ_TIMEOUT)
            except TimeoutError:
                pass


def activated_socket() -> socket.socket:
    try:
        listen_pid = int(os.environ.get("LISTEN_PID", "0"))
        listen_fds = int(os.environ.get("LISTEN_FDS", "0"))
    except ValueError:
        raise RuntimeError("invalid socket activation environment") from None
    if listen_pid != os.getpid() or listen_fds != 1:
        raise RuntimeError("exactly one systemd socket is required")
    descriptor = 3
    sock = socket.fromfd(descriptor, socket.AF_UNIX, socket.SOCK_STREAM)
    if (
        not stat.S_ISSOCK(os.fstat(sock.fileno()).st_mode)
        or sock.getsockopt(socket.SOL_SOCKET, socket.SO_DOMAIN) != socket.AF_UNIX
        or sock.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
        or not sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
    ):
        sock.close()
        raise RuntimeError("activated descriptor is not a listening Unix socket")
    os.set_inheritable(sock.fileno(), False)
    return sock


def _peer_credentials(writer: asyncio.StreamWriter) -> tuple[int, int, int] | None:
    if not hasattr(socket, "SO_PEERCRED"):
        return None
    sock = writer.get_extra_info("socket")
    if sock is None:
        return None
    size = struct.calcsize("3i")
    return struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, size))

