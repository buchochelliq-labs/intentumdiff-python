"""
intentumdiff.live_server
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``LiveServer`` — a persistent socket server for keystroke-level semantic
diffing.

Architecture
────────────
* The server keeps a pool of already-initialised ``SemanticDiffer`` instances
  alive between requests, amortising the ~100 ms Wasm initialisation cost.
* Each connected client sends newline-delimited JSON requests and receives
  newline-delimited JSON responses on the same connection.
* Per-path **debouncing** (default 150 ms) coalesces bursts of rapid keystrokes
  into a single diff request, matching the behaviour of ``FileWatcher``.
* A monotonically increasing ``seq`` field lets the server discard stale
  responses when a newer request for the same path has already been queued.

Request format
--------------
One JSON object per line::

    {"path": "src/main.py", "content": "...", "seq": 42,
     "ref": "HEAD", "deltas": [...]}

``path``    — repository-relative file path (required).
``content`` — current buffer contents as a string (required).
``seq``     — monotonically increasing integer; older responses are dropped.
``ref``     — git ref for the "old" version (optional; default "HEAD").
``deltas``  — list of ``EditDelta`` dicts for incremental parsing (optional).

The repository root is fixed at server startup and cannot be overridden
per-request.

Response format (non-streaming)
---------------------------------
::

    {"seq": 42, "diff": <SemanticDiff JSON>}
    {"seq": 42, "error": "message"}

Response format (streaming, ``stream_analysis=True``)
-----------------------------------------------------
Multiple lines per request, followed by a ``done`` sentinel::

    {"seq": 42, "event": <ChangeStreamEvent JSON>}
    ...
    {"seq": 42, "done": true}

Transport
---------
``start_socket()`` opens a Unix-domain socket (``AF_UNIX``) on POSIX and on
Windows 10 build 17063+ (Python ≥ 3.9).  On older Windows it falls back to a
Windows named pipe (path format ``\\\\.\\pipe\\intentumdiff-live-<random>``), which
the OS restricts to the creating user via its default ACL — no
application-level token required.
The ``start_stdin()`` method provides a simpler pipe-based interface (one
request → one response, no debouncing) for environments where sockets are
unavailable.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import json
import logging
import os
import secrets
import socket
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from intentumdiff.differ import SemanticDiffer
    from rich.console import Console as RichConsole

from intentumdiff.core.models import ChangeStreamEvent, EditDelta
from intentumdiff.sources.live_buffer_source import LiveBufferSource

logger = logging.getLogger(__name__)

# Maximum bytes buffered per client before the connection is dropped (transport-enforced).
_MAX_LINE_BYTES = 1_048_576  # 1 MiB

# Maximum number of concurrent client connections (transport-enforced).
_MAX_CLIENTS = 4

# NOTE: the content-size and delta-count caps (max_content_bytes / max_deltas) now live in the
# Rust core (`live_server.rs`, the source of truth) and are advertised via `_limits()`; request
# validation enforces them there (#100, A2.5).

# Editor protocol version emitted by ready/hello/diff responses.
_PROTOCOL_VERSION = 2

# Commit-level review must remain responsive in editors.  If a user starts the
# LiveServer with unlimited active-file parser fuel, review reloads a capped
# registry so one hard parser case cannot stall the whole Semantic Changes tree.
_REVIEW_FUEL_WHEN_LIVE_UNLIMITED = 100_000_000


def _wasm_dir() -> str:
    """The bundled wasm directory (holds the parsers + `parser_manifest.json`) the native diff
    handler reads to resolve a parser for a file. The native live-server binary will bundle its own
    copy; here it is the package's `wasm/` dir."""
    import intentumdiff

    return str(Path(intentumdiff.__file__).parent / "wasm")


@dataclass(frozen=True)
class _ParsedRequest:
    op: str
    seq: int
    path: str
    content: str
    ref: str
    edit_deltas: list[EditDelta] | None
    stream: bool | None = None


def _make_socket_dir() -> str:
    """Return (and create) a private directory for the server's socket file."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
    if runtime_dir and os.path.isdir(runtime_dir):
        base = runtime_dir
    else:
        base = tempfile.gettempdir()

    sock_dir = os.path.join(base, f"intentumdiff-{os.getpid()}")
    # 0o700 = OWNER-ONLY rwx — the restrictive choice for the socket dir (the semgrep
    # rule pattern-matches any 0o7xx literal; here it IS the least-permissive mode).
    os.makedirs(sock_dir, mode=0o700, exist_ok=True)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
    # Enforce permissions even if the directory already existed.
    try:
        os.chmod(sock_dir, 0o700)  # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
    except OSError:
        pass
    return sock_dir


def _make_socket_path(pid: int, token: str) -> str:
    """Return a private socket path incorporating a random token."""
    sock_dir = _make_socket_dir()
    return os.path.join(sock_dir, f"intentumdiff-live-{token[:16]}.sock")


def _has_unix_socket() -> bool:
    """Return True when running on POSIX and ``AF_UNIX`` is available.

    Explicitly excludes Windows: modern Windows builds expose ``AF_UNIX`` in
    the ``socket`` module, but the preferred IPC transport on Windows is a
    named pipe (which allows an explicit security descriptor restricting access
    to the creating user).
    """
    return sys.platform != "win32" and hasattr(socket, "AF_UNIX")


# ---------------------------------------------------------------------------
# Windows named-pipe support (only active on win32 without AF_UNIX)
# ---------------------------------------------------------------------------

if sys.platform == "win32":  # pragma: no cover
    import ctypes
    import ctypes.wintypes as _wt

    _PIPE_ACCESS_DUPLEX = 0x00000003
    _PIPE_TYPE_BYTE = 0x00000000
    _PIPE_WAIT = 0x00000000
    _PIPE_UNLIMITED_INSTANCES = 255
    _GENERIC_RW = 0xC0000000
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x80
    _ERROR_PIPE_CONNECTED = 535
    _INVALID_HANDLE = ctypes.c_void_p(-1).value
    # PeekNamedPipe is used to check data availability without blocking,
    # avoiding the deadlock that arises from concurrent ReadFile/WriteFile on
    # a synchronous (non-overlapped) named-pipe handle.
    _PeekNamedPipe = ctypes.windll.kernel32.PeekNamedPipe
    _PeekNamedPipe.restype = _wt.BOOL
    _PeekNamedPipe.argtypes = [
        _wt.HANDLE,           # hNamedPipe
        ctypes.c_char_p,      # lpBuffer (NULL — we only want the byte count)
        _wt.DWORD,            # nBufferSize
        ctypes.POINTER(_wt.DWORD),  # lpBytesRead
        ctypes.POINTER(_wt.DWORD),  # lpTotalBytesAvail
        ctypes.POINTER(_wt.DWORD),  # lpBytesLeftThisMessage
    ]

    # --- Security descriptor helpers (DACL for named pipe) ----------------
    _TOKEN_QUERY = 0x0008
    _TOKEN_USER = 1

    class _SECURITY_ATTRIBUTES(ctypes.Structure):  # type: ignore[misc]
        _fields_ = [
            ("nLength", _wt.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", _wt.BOOL),
        ]

    def _get_current_user_sid() -> str:
        """Return the string SID for the current user (e.g. ``S-1-5-21-...``)."""
        _k32 = ctypes.windll.kernel32
        _adv = ctypes.windll.advapi32
        # GetCurrentProcess returns a pseudo-handle; setting restype to
        # c_void_p prevents sign-extension truncation on 64-bit Windows.
        _k32.GetCurrentProcess.restype = ctypes.c_void_p
        proc = ctypes.c_void_p(_k32.GetCurrentProcess())
        token = _wt.HANDLE(0)
        if not _adv.OpenProcessToken(proc, _TOKEN_QUERY, ctypes.byref(token)):
            raise OSError(
                f"OpenProcessToken failed (error {ctypes.GetLastError()})"
            )
        try:
            # First call: query required buffer size.
            needed = _wt.DWORD(0)
            _adv.GetTokenInformation(
                token, _TOKEN_USER, None, 0, ctypes.byref(needed)
            )
            buf = ctypes.create_string_buffer(needed.value)
            if not _adv.GetTokenInformation(
                token, _TOKEN_USER, buf, needed, ctypes.byref(needed)
            ):
                raise OSError(
                    f"GetTokenInformation failed (error {ctypes.GetLastError()})"
                )
            # TOKEN_USER.User.Sid is a SID pointer at offset 0 of the struct.
            sid_addr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
            # ConvertSidToStringSidW allocates the output string via
            # LocalAlloc; we must call LocalFree to release it.
            str_buf = ctypes.c_void_p()
            if not _adv.ConvertSidToStringSidW(
                ctypes.c_void_p(sid_addr), ctypes.byref(str_buf)
            ):
                raise OSError(
                    "ConvertSidToStringSidW failed "
                    f"(error {ctypes.GetLastError()})"
                )
            try:
                return ctypes.c_wchar_p(str_buf.value).value  # type: ignore[return-value]
            finally:
                _k32.LocalFree(str_buf)
        finally:
            _k32.CloseHandle(token)

    # Build the pipe SECURITY_ATTRIBUTES once at import time using SDDL
    # D:P(A;;GA;;;{user_sid})(A;;GA;;;SY) — a Protected DACL granting
    # Generic All to the current user and SYSTEM only.  The P flag blocks
    # DACL inheritance.  Keeping the security descriptor alive at module
    # scope eliminates per-call overhead in the accept loop, avoiding the
    # timing race between ConnectNamedPipe and the stop() dummy connection.
    _pipe_sa: "Optional[_SECURITY_ATTRIBUTES]" = None
    _pipe_sd: "Optional[ctypes.c_void_p]" = None  # kept alive; freed at exit
    try:
        _uid = _get_current_user_sid()
        _sddl = f"D:P(A;;GA;;;{_uid})(A;;GA;;;SY)"
        _sd = ctypes.c_void_p()
        if ctypes.windll.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            _sddl, 1, ctypes.byref(_sd), None
        ):
            _pipe_sa = _SECURITY_ATTRIBUTES()
            _pipe_sa.nLength = ctypes.sizeof(_SECURITY_ATTRIBUTES)
            _pipe_sa.lpSecurityDescriptor = _sd
            _pipe_sa.bInheritHandle = False
            _pipe_sd = _sd
        else:
            logger.warning(
                "LiveServer: ConvertStringSecurityDescriptorToSecurityDescriptorW "
                "failed (error %d); named pipes will use the default DACL",
                ctypes.GetLastError(),
            )
    except OSError as _dacl_exc:
        logger.warning(
            "LiveServer: could not build pipe security descriptor (%s); "
            "named pipes will use the default DACL",
            _dacl_exc,
        )

    def _create_named_pipe_handle(name: str) -> "ctypes.c_void_p":
        """Create a new named-pipe server instance and return its handle.

        Uses the module-level ``_pipe_sa`` ``SECURITY_ATTRIBUTES`` struct
        (built once at import time) with SDDL
        ``D:P(A;;GA;;;{user_sid})(A;;GA;;;SY)`` so the pipe ACL grants
        access only to the creating user and SYSTEM, regardless of the
        process token's default DACL.  Falls back silently to the default
        DACL when the module-level struct could not be built.
        """
        handle = ctypes.windll.kernel32.CreateNamedPipeW(
            name,
            _PIPE_ACCESS_DUPLEX,
            _PIPE_TYPE_BYTE | _PIPE_WAIT,
            _PIPE_UNLIMITED_INSTANCES,
            65536,
            65536,
            0,
            ctypes.byref(_pipe_sa) if _pipe_sa is not None else None,  # type: ignore[name-defined]
        )
        if handle == _INVALID_HANDLE:
            raise OSError(
                f"CreateNamedPipeW failed (error {ctypes.GetLastError()})"
            )
        return handle

    def _open_pipe_client(name: str) -> None:
        """Open a short-lived client connection to unblock ConnectNamedPipe."""
        ctypes.windll.kernel32.WaitNamedPipeW(name, 100)
        h = ctypes.windll.kernel32.CreateFileW(
            name, _GENERIC_RW, 0, None, _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None
        )
        if h != _INVALID_HANDLE:
            ctypes.windll.kernel32.CloseHandle(h)

    class _NamedPipeConn:
        """Socket-compatible wrapper around a Windows named-pipe handle.

        Provides the ``recv`` / ``sendall`` / ``close`` / context-manager
        interface that ``_handle_client`` and ``_write_line_bytes`` expect.

        **Why PeekNamedPipe instead of blocking ReadFile?**
        Windows serializes *all* I/O on a synchronous (non-overlapped) named-
        pipe handle — reads *and* writes.  If ``_handle_client`` blocks on
        ``ReadFile`` while waiting for the next request, the timer thread
        cannot call ``WriteFile`` to send the current response, creating a
        deadlock.  ``PeekNamedPipe`` is non-blocking and does not hold the
        handle's I/O queue, so ``WriteFile`` from the timer thread can proceed
        while this thread is sleeping between peeks.
        """

        _POLL_INTERVAL = 0.001  # 1 ms — imperceptible for human-speed editing

        def __init__(
            self,
            handle: "ctypes.c_void_p",
            stop_event: threading.Event,
        ) -> None:
            self._handle = handle
            self._stop_event = stop_event
            self._closed = False

        def settimeout(self, timeout: float) -> None:  # noqa: ARG002
            """No-op: termination is driven by stop_event, not a socket timeout."""

        def recv(self, size: int) -> bytes:
            """Return up to *size* bytes, polling until data arrives or the
            server stop-event is set."""
            avail = _wt.DWORD(0)
            while not self._stop_event.is_set():
                ok = _PeekNamedPipe(
                    self._handle, None, 0, None, ctypes.byref(avail), None
                )
                if not ok:
                    return b""  # pipe broken
                if avail.value > 0:
                    to_read = min(size, avail.value)
                    buf = ctypes.create_string_buffer(to_read)
                    n_read = _wt.DWORD(0)
                    ok2 = ctypes.windll.kernel32.ReadFile(
                        self._handle, buf, to_read, ctypes.byref(n_read), None
                    )
                    return buf.raw[: n_read.value] if ok2 else b""
                time.sleep(self._POLL_INTERVAL)
            return b""  # stop requested

        def sendall(self, data: bytes) -> None:
            n_written = _wt.DWORD(0)
            ctypes.windll.kernel32.WriteFile(
                self._handle, data, len(data), ctypes.byref(n_written), None
            )

        def close(self) -> None:
            if not self._closed:
                self._closed = True
                ctypes.windll.kernel32.DisconnectNamedPipe(self._handle)
                ctypes.windll.kernel32.CloseHandle(self._handle)

        def __enter__(self) -> "_NamedPipeConn":
            return self

        def __exit__(self, *_: object) -> None:
            self.close()


class _ReviewCancelled(Exception):
    """Raised internally when a streaming review is superseded by a newer request."""

    def __init__(self, seq: int) -> None:
        super().__init__(f"streaming review seq={seq} superseded")
        self.seq = seq


class LiveServer:
    """Persistent socket server for keystroke-level semantic diffing.

    Parameters
    ----------
    differ:
        A configured :class:`~intentumdiff.differ.SemanticDiffer` instance.
    repo_path:
        Repository root to use for all diff requests.  Clients are **not**
        permitted to override this via the request payload — doing so would
        allow path traversal into arbitrary directories.
    ref:
        Git ref to compare live buffers against (default: ``"HEAD"``).
    debounce:
        Seconds to wait after the last request for a given path before
        running the diff.  Default 0.15 (150 ms).
    console:
        Optional :class:`rich.console.Console` for status messages.
    stream_analysis:
        When ``True``, responses are sent as a sequence of
        ``ChangeStreamEvent`` objects followed by a ``done`` sentinel.
        When ``False`` (default), a single ``SemanticDiff`` JSON object is
        sent per request.
    """

    def __init__(
        self,
        differ: "SemanticDiffer",
        *,
        repo_path: "str | Path",
        ref: str = "HEAD",
        debounce: float = 0.15,
        console: "RichConsole | None" = None,
        stream_analysis: bool = False,
    ) -> None:
        self._differ = differ
        self._repo_path = str(repo_path)
        self._ref = ref
        self._debounce = debounce
        self._console = console
        self._stream_analysis = stream_analysis

        # Random token — used in socket path so guessing the socket requires
        # knowledge of the token.
        self._token: str = secrets.token_hex(32)

        self._lock = threading.Lock()
        # Debounce timers: path → threading.Timer
        self._timers: dict[str, threading.Timer] = {}
        self._timer_requests: dict[str, _ParsedRequest] = {}
        # Latest seen seq per path (for late-response discarding)
        self._latest_seq: dict[str, int] = {}
        self._latest_review_seq: int = 0
        # Active client connection count
        self._client_count: int = 0

        self._stop_event = threading.Event()
        self._server_thread: threading.Thread | None = None
        self._server_socket: socket.socket | None = None
        # Set only when the Windows named-pipe fallback is active.
        self._pipe_name: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ready_message(
        self,
        *,
        transport: str = "stdio",
        address: str | None = None,
    ) -> dict[str, Any]:
        """Return the protocol-v2 startup/capability payload."""

        payload: dict[str, Any] = {
            "op": "ready",
            "ok": True,
            "protocol_version": _PROTOCOL_VERSION,
            "repo_path": self._repo_path,
            "ref": self._ref,
            "transport": transport,
            "limits": self._limits(),
            "capabilities": self._capabilities(),
        }
        if address is not None:
            payload["address"] = address
        return payload

    def start_socket(self, socket_path: str | None = None) -> str:
        """Start the background socket listener.

        Returns the socket address string: a Unix-socket path on POSIX /
        modern Windows, or a ``\\\\.\\pipe\\...`` name on older Windows.
        """
        if _has_unix_socket():
            path = socket_path or _make_socket_path(os.getpid(), self._token)
            # Remove stale socket file if it exists.
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(path)
            # Restrict access to the owning user.
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            addr: str = path
        else:
            # Older Windows (no AF_UNIX): use a named pipe.  The OS default
            # ACL restricts access to the creating user — no token needed.
            pipe_name = r"\\.\pipe\intentumdiff-live-" + secrets.token_hex(8)
            self._pipe_name = pipe_name
            addr = pipe_name

            self._server_thread = threading.Thread(
                target=self._named_pipe_accept_loop,
                name="LiveServer-accept",
                daemon=True,
            )
            self._server_thread.start()

            if self._console is not None:
                self._console.print(f"[dim]LiveServer listening on {addr}[/dim]")

            return addr

        sock.listen(4)  # small backlog — only local editor processes connect
        sock.settimeout(1.0)  # Allow periodic stop-event checks
        self._server_socket = sock

        self._server_thread = threading.Thread(
            target=self._accept_loop,
            name="LiveServer-accept",
            daemon=True,
        )
        self._server_thread.start()

        if self._console is not None:
            self._console.print(f"[dim]LiveServer listening on {addr}[/dim]")

        return addr

    def start_stdin(self) -> None:
        """Read requests from stdin and write responses to stdout (blocking).

        Each line of stdin is treated as a JSON request object.  Responses are
        written as a single JSON line (or multiple lines when streaming).
        No debouncing is applied — each request is processed immediately.
        """
        _write_line(sys.stdout, self.ready_message(transport="stdio"))
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                _write_line(
                    sys.stdout,
                    self._error_response(
                        0,
                        "invalid_json",
                        f"JSON parse error: {exc}",
                    ),
                )
                continue

            def _write(obj: dict[str, Any]) -> None:
                _write_line(sys.stdout, obj)

            self._process_request(request, _write)

    def stop(self) -> None:
        """Signal the server to stop and wait for the background thread."""
        self._stop_event.set()
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass
        if self._pipe_name is not None and self._server_thread is not None:
            # Unblock the blocking ConnectNamedPipe in the accept loop by opening
            # dummy client connections UNTIL the thread exits. A fixed retry budget
            # raced under load (#73 flake): when the accept thread was starved
            # between CloseHandle and its next CreateNamedPipeW, every dummy
            # connect failed instantly (no pipe instance existed yet) and the
            # thread later blocked forever. Nudging against thread liveness is
            # deterministic — the first connect after the thread blocks wins.
            deadline = time.monotonic() + 10.0
            while self._server_thread.is_alive() and time.monotonic() < deadline:
                with suppress(Exception):
                    _open_pipe_client(self._pipe_name)  # type: ignore[name-defined]
                self._server_thread.join(timeout=0.05)
        with self._lock:
            for timer in list(self._timers.values()):
                timer.cancel()
            self._timers.clear()
            self._timer_requests.clear()
        if self._server_thread is not None:
            self._server_thread.join(timeout=3.0)

    # ------------------------------------------------------------------
    # Internal: accept loops (socket and named pipe)
    # ------------------------------------------------------------------

    def _named_pipe_accept_loop(self) -> None:  # pragma: no cover
        """Background thread: accept connections on the Windows named pipe."""
        while not self._stop_event.is_set():
            try:
                handle = _create_named_pipe_handle(self._pipe_name)  # type: ignore[arg-type,name-defined]
            except OSError as exc:
                logger.error("LiveServer: cannot create named pipe: %s", exc)
                break

            connected = ctypes.windll.kernel32.ConnectNamedPipe(handle, None)  # type: ignore[name-defined]
            err = ctypes.GetLastError()  # type: ignore[name-defined]

            # ConnectNamedPipe returns 0 on error; ERROR_PIPE_CONNECTED (535)
            # means a client connected between CreateNamedPipeW and
            # ConnectNamedPipe — still valid.
            pipe_ok = connected != 0 or err in (0, _ERROR_PIPE_CONNECTED)  # type: ignore[name-defined]

            if not pipe_ok:
                ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[name-defined]
                if self._stop_event.is_set():
                    break
                continue

            if self._stop_event.is_set():
                ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[name-defined]
                break

            with self._lock:
                if self._client_count >= _MAX_CLIENTS:
                    logger.warning(
                        "LiveServer: max concurrent clients (%d) reached; "
                        "rejecting new connection.",
                        _MAX_CLIENTS,
                    )
                    try:
                        with _NamedPipeConn(handle, self._stop_event) as tmp:  # type: ignore[name-defined]
                            _write_line_bytes(
                                tmp,
                                self._error_response(
                                    0,
                                    "too_many_clients",
                                    "too many concurrent clients",
                                ),
                            )
                    except OSError:
                        pass
                    continue
                self._client_count += 1

            client_thread = threading.Thread(
                target=self._handle_client,
                args=(_NamedPipeConn(handle, self._stop_event),),  # type: ignore[name-defined]
                name="LiveServer-client",
                daemon=True,
            )
            client_thread.start()

    def _accept_loop(self) -> None:
        """Background thread: accept connections and spawn per-client threads."""
        if self._server_socket is None:
            logger.debug("LiveServer accept loop started without a server socket")
            return
        while not self._stop_event.is_set():
            try:
                conn, _ = self._server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            with self._lock:
                if self._client_count >= _MAX_CLIENTS:
                    # Too many concurrent clients — reject immediately.
                    logger.warning(
                        "LiveServer: max concurrent clients (%d) reached; "
                        "rejecting new connection.",
                        _MAX_CLIENTS,
                    )
                    try:
                        _write_line_bytes(
                            conn,
                            self._error_response(
                                0,
                                "too_many_clients",
                                "too many concurrent clients",
                            ),
                        )
                        conn.close()
                    except OSError:
                        pass
                    continue
                self._client_count += 1

            client_thread = threading.Thread(
                target=self._handle_client,
                args=(conn,),
                name="LiveServer-client",
                daemon=True,
            )
            client_thread.start()

    def _handle_client(self, conn: socket.socket) -> None:
        """Serve a single connected client until it disconnects."""
        buf = b""
        try:
            with conn:
                conn.settimeout(30.0)  # per-read timeout to release idle connections
                while not self._stop_event.is_set():
                    try:
                        chunk = conn.recv(65536)
                    except socket.timeout:
                        # No activity for 30 s — close to free resources.
                        logger.debug("LiveServer: client timed out (idle)")
                        break
                    if not chunk:
                        break
                    buf += chunk
                    # Guard against runaway clients sending gigantic payloads.
                    if len(buf) > _MAX_LINE_BYTES and b"\n" not in buf:
                        logger.warning(
                            "LiveServer: client sent >%d bytes without newline; dropping.",
                            _MAX_LINE_BYTES,
                        )
                        _write_line_bytes(conn, {"error": "request too large"})
                        break
                    while b"\n" in buf:
                        line_bytes, buf = buf.split(b"\n", 1)
                        if len(line_bytes) > _MAX_LINE_BYTES:
                            _write_line_bytes(conn, {"error": "request line too large"})
                            buf = b""
                            break
                        line = line_bytes.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        try:
                            request = json.loads(line)
                        except json.JSONDecodeError as exc:
                            _write_line_bytes(
                                conn,
                                self._error_response(
                                    0,
                                    "invalid_json",
                                    f"JSON parse error: {exc}",
                                ),
                            )
                            continue

                        def _send(obj: dict[str, Any], _conn: socket.socket = conn) -> None:
                            _write_line_bytes(_conn, obj)

                        self._schedule_request(request, _send)
        except OSError as exc:
            logger.debug("Client connection error: %s", exc)
        finally:
            with self._lock:
                self._client_count = max(0, self._client_count - 1)

    # ------------------------------------------------------------------
    # Internal: debounce + dispatch
    # ------------------------------------------------------------------

    def _schedule_request(
        self,
        request: dict[str, Any],
        send_fn: "Any",  # Callable[[dict], None]
    ) -> None:
        """Debounce incoming requests per path and schedule a diff run."""
        if not isinstance(request, dict):
            send_fn(self._error_response(0, "invalid_request", "request must be an object"))
            return
        if self._handle_control_request(request, send_fn):
            return
        parsed, error = self._parse_diff_request(request)
        if error is not None:
            send_fn(error)
            return
        if parsed is None:
            send_fn(
                self._error_response(
                    0,
                    "invalid_request",
                    "request parser returned no diff request",
                    op="diff",
                )
            )
            return

        with self._lock:
            # Track the latest seq for this path so older responses can be dropped.
            self._latest_seq[parsed.path] = max(
                self._latest_seq.get(parsed.path, 0),
                parsed.seq,
            )

            # Cancel any pending timer for this path.
            existing = self._timers.pop(parsed.path, None)
            if existing is not None:
                existing.cancel()
            self._timer_requests.pop(parsed.path, None)

            timer = threading.Timer(
                self._debounce,
                self._run_diff,
                args=(parsed, send_fn),
            )
            self._timers[parsed.path] = timer
            self._timer_requests[parsed.path] = parsed
            timer.start()

    def _run_diff(
        self,
        request: _ParsedRequest,
        send_fn: "Any",
    ) -> None:
        """Execute the diff for a debounced request and send the response."""
        with self._lock:
            self._timers.pop(request.path, None)
            self._timer_requests.pop(request.path, None)
            latest_seq = self._latest_seq.get(request.path, 0)

        if request.seq < latest_seq:
            logger.debug(
                "Dropping stale seq=%d for %r (latest=%d)",
                request.seq,
                request.path,
                latest_seq,
            )
            return

        source = LiveBufferSource(
            repo_path=self._repo_path,
            file_path=request.path,
            live_content=request.content,
            ref=request.ref,
            edit_deltas=request.edit_deltas,
        )

        self._process_request_with_source(source, request, send_fn)

    def _process_request_legacy(
        self,
        request: dict[str, Any],
        send_fn: "Any",
    ) -> None:
        """Process a request immediately (no debounce — used by start_stdin)."""
        raise NotImplementedError

    def _process_request_with_source_legacy(
        self,
        source: LiveBufferSource,
        seq: int,
        send_fn: "Any",
    ) -> None:
        """Run the differ against *source* and send the response via *send_fn*."""
        raise NotImplementedError

    def _process_request(
        self,
        request: dict[str, Any],
        send_fn: "Any",
    ) -> None:
        """Process a request immediately using the protocol-v2 parser."""
        if not isinstance(request, dict):
            send_fn(self._error_response(0, "invalid_request", "request must be an object"))
            return
        if self._handle_control_request(request, send_fn):
            return
        parsed, error = self._parse_diff_request(request)
        if error is not None:
            send_fn(error)
            return
        if parsed is None:
            send_fn(
                self._error_response(
                    0,
                    "invalid_request",
                    "request parser returned no diff request",
                    op="diff",
                )
            )
            return

        source = LiveBufferSource(
            repo_path=self._repo_path,
            file_path=parsed.path,
            live_content=parsed.content,
            ref=parsed.ref,
            edit_deltas=parsed.edit_deltas,
        )
        self._process_request_with_source(source, parsed, send_fn)

    def _process_request_with_source(
        self,
        source: LiveBufferSource,
        request: _ParsedRequest,
        send_fn: "Any",
    ) -> None:
        """Run the differ against *source* and send a protocol-v2 response."""
        start = time.perf_counter()
        use_stream = self._stream_analysis if request.stream is None else request.stream
        try:
            if use_stream:
                for event in self._differ.diff_stream_progressive(source):
                    if self._is_stale(request):
                        return
                    send_fn({
                        "op": "diff",
                        "seq": request.seq,
                        "ok": True,
                        "event": json.loads(event.model_dump_json()),
                    })
                if self._is_stale(request):
                    return
                send_fn({"op": "diff", "seq": request.seq, "ok": True, "done": True})
            else:
                diff = self._compute_diff(source, request)
                if self._is_stale(request):
                    return
                duration_ms = round((time.perf_counter() - start) * 1000, 3)
                send_fn({
                    "op": "diff",
                    "seq": request.seq,
                    "ok": True,
                    "diff": json.loads(diff.model_dump_json()),
                    "metadata": {
                        "duration_ms": duration_ms,
                        "language": diff.language,
                        "change_count": len(diff.changes),
                        "style_only": diff.is_style_only,
                        "guardrail_violation_count": len(diff.guardrail_violations),
                    },
                })
        except FileNotFoundError as exc:
            send_fn(
                self._error_response(
                    request.seq,
                    "file_not_found",
                    str(exc),
                    op="diff",
                )
            )
        except Exception as exc:
            logger.warning(
                "LiveServer diff error for seq=%d: %s",
                request.seq,
                exc,
                exc_info=True,
            )
            send_fn(self._error_response(request.seq, "diff_error", str(exc), op="diff"))

    def _compute_diff(
        self,
        source: LiveBufferSource,
        request: _ParsedRequest,
    ) -> Any:
        """Return the ``SemanticDiff`` for a non-streaming request.

        The native handler in the Rust core serves Python files end-to-end (certified batch,
        #100 A2.5 Phase A1); it returns the diff JSON, which is rebuilt through the DTO so
        normalisation matches the Python path exactly. Any other language, or a non-certified
        outcome, falls back to the Python differ pipeline unchanged.
        """
        from intentumdiff import rust_core
        from intentumdiff.core.models import SemanticDiff

        # Diagnostics mode requires the full Python pipeline (per try_rust_core_batch_diffs);
        # the native handler only serves the certified fast path, so skip it when diagnostics
        # are on. (For a mocked differ in tests, `_config.diagnostics` is truthy -> skip.)
        if not getattr(self._differ._config, "diagnostics", False):
            native = rust_core.live_handle_diff(
                self._repo_path,
                request.path,
                request.ref,
                request.content,
                self._differ._config.model_dump_json(),
                _wasm_dir(),
            )
            if "diff" in native:
                return SemanticDiff.model_validate(native["diff"])
        return self._differ.diff(source)

    def _compute_review(
        self,
        review_config: Any,
        commit_differ: Any,
        old_ref: str,
        new_ref: str,
    ) -> Any:
        """Return the ``CommitDiff`` for a non-streaming review.

        The native handler serves all-Python certified commits from the core (#100 A2.5 Phase A2),
        returning the CommitDiff JSON (rebuilt through the DTO for parity); any other case (mixed
        language, working-tree review, diagnostics, or a non-certified batch) falls back to the
        Python ``CommitDiffer.diff_commit``.
        """
        from intentumdiff import rust_core
        from intentumdiff.core.models import CommitDiff

        if not getattr(review_config, "diagnostics", False):
            native = rust_core.live_handle_review(
                self._repo_path,
                old_ref,
                new_ref,
                review_config.model_dump_json(),
                _wasm_dir(),
            )
            if "commit_diff" in native:
                return CommitDiff.model_validate(native["commit_diff"])
        return commit_differ.diff_commit(
            repo_path=self._repo_path, old_ref=old_ref, new_ref=new_ref
        )

    def _handle_control_request(
        self,
        request: dict[str, Any],
        send_fn: "Any",
    ) -> bool:
        op = request.get("op", "diff")
        if op is None:
            op = "diff"
        if op == "diff":
            return False
        seq, seq_error = self._request_seq(request)
        if seq_error is not None:
            send_fn(seq_error)
            return True
        if op == "hello":
            send_fn({
                "op": "hello",
                "seq": seq,
                "ok": True,
                "protocol_version": _PROTOCOL_VERSION,
                "repo_path": self._repo_path,
                "ref": self._ref,
                "limits": self._limits(),
                "capabilities": self._capabilities(),
            })
            return True
        if op == "review":
            self._process_review_request(request, seq, send_fn)
            return True
        if op == "cancel":
            send_fn(self._cancel_request(request, seq))
            return True
        send_fn(self._error_response(seq, "unknown_op", f"unknown op: {op!r}", op=str(op)))
        return True

    def _process_review_request(
        self,
        request: dict[str, Any],
        seq: int,
        send_fn: "Any",
    ) -> None:
        """Run a saved-snapshot commit review and send a protocol-v2 response."""
        # Ref/stream validation lives in the core; orchestration below stays Python.
        from intentumdiff import rust_core

        review = rust_core.live_parse_review_request(request, self._ref, seq)
        if "error" in review:
            send_fn(review["error"])
            return
        parsed = review["parsed"]
        old_ref = parsed["old_ref"]
        new_ref = parsed["new_ref"]
        stream = parsed["stream"]

        with self._lock:
            self._latest_review_seq = max(self._latest_review_seq, seq)

        start = time.perf_counter()
        try:
            from intentumdiff.core.commit_differ import CommitDiffer

            review_config = self._differ._config.model_copy()
            review_registry = self._differ._registry
            review_fuel_capped = False
            if review_config.plugin_fuel == -1:
                review_config.plugin_fuel = _REVIEW_FUEL_WHEN_LIVE_UNLIMITED
                review_registry = None
                review_fuel_capped = True

            commit_differ = CommitDiffer(
                config=review_config,
                registry=review_registry,
            )
            if stream:
                commit_diff = self._run_streaming_review(
                    commit_differ=commit_differ,
                    seq=seq,
                    old_ref=old_ref,
                    new_ref=new_ref,
                    send_fn=send_fn,
                )
            else:
                commit_diff = self._compute_review(
                    review_config, commit_differ, old_ref, new_ref
                )
            with self._lock:
                if seq < self._latest_review_seq:
                    return
            duration_ms = round((time.perf_counter() - start) * 1000, 3)
            send_fn({
                "op": "review",
                "seq": seq,
                "ok": True,
                "commit_diff": json.loads(commit_diff.model_dump_json()),
                "metadata": {
                    "duration_ms": duration_ms,
                    "file_count": len(commit_diff.file_diffs),
                    "guardrail_violation_count": len(commit_diff.guardrail_violations),
                    "cross_file_change_count": len(commit_diff.cross_file_changes),
                    "old_ref": old_ref,
                    "new_ref": new_ref,
                    "review_fuel_capped": review_fuel_capped,
                    "review_plugin_fuel": review_config.plugin_fuel,
                    "streamed": stream,
                },
            })
        except _ReviewCancelled:
            return
        except Exception as exc:
            logger.warning(
                "LiveServer review error for seq=%d: %s",
                seq,
                exc,
                exc_info=True,
            )
            send_fn(self._error_response(seq, "review_error", str(exc), op="review"))

    def _run_streaming_review(
        self,
        *,
        commit_differ: Any,
        seq: int,
        old_ref: str,
        new_ref: str,
        send_fn: Any,
    ) -> Any:
        """Emit per-file ``review_file`` events then return the final CommitDiff."""
        from intentumdiff.core.commit_differ import FileDiffError, FileDiffResult

        results: list[FileDiffResult] = []
        errors: list[FileDiffError] = []
        for index, item in enumerate(
            commit_differ.iter_file_diffs(
                repo_path=self._repo_path,
                old_ref=old_ref,
                new_ref=new_ref,
            ),
            start=1,
        ):
            with self._lock:
                if seq < self._latest_review_seq:
                    raise _ReviewCancelled(seq)
            if isinstance(item, FileDiffResult):
                results.append(item)
                send_fn({
                    "op": "review_file",
                    "seq": seq,
                    "ok": True,
                    "file_diff": json.loads(item.file_diff.model_dump_json()),
                    "old_filename": item.file_diff.old_filename,
                    "new_filename": item.file_diff.new_filename,
                    "index": index,
                })
            else:
                errors.append(item)

        return commit_differ.finalize_commit_diff(
            results,
            errors,
            old_ref=old_ref,
            new_ref=new_ref,
            repo_path=self._repo_path,
        )

    def _parse_diff_request(
        self,
        request: dict[str, Any],
    ) -> tuple[_ParsedRequest | None, dict[str, Any] | None]:
        # Field validation (seq / path guard / content cap / ref / stream / deltas type+count)
        # lives in the core; the shell only builds the EditDelta DTOs from the raw deltas.
        from intentumdiff import rust_core

        result = rust_core.live_parse_diff_request(request, self._ref)
        if "error" in result:
            return None, result["error"]
        parsed = result["parsed"]

        raw_deltas: list[dict[str, Any]] = parsed.get("deltas") or []
        edit_deltas: list[EditDelta] | None = None
        if raw_deltas:
            try:
                edit_deltas = [EditDelta.model_validate(delta) for delta in raw_deltas]
            except Exception as exc:
                logger.warning(
                    "Invalid edit deltas in request for %r: %s", parsed["path"], exc
                )

        return (
            _ParsedRequest(
                op="diff",
                seq=int(parsed["seq"]),
                path=parsed["path"],
                content=parsed["content"],
                ref=parsed["ref"],
                edit_deltas=edit_deltas,
                stream=parsed["stream"],
            ),
            None,
        )

    def _request_seq(
        self,
        request: dict[str, Any],
    ) -> tuple[int, dict[str, Any] | None]:
        from intentumdiff import rust_core

        result = rust_core.live_request_seq(request)
        if "error" in result:
            return 0, result["error"]
        return int(result["seq"]), None

    def _normalise_request_path(
        self,
        path: str,
        seq: int,
    ) -> tuple[str, dict[str, Any] | None]:
        from intentumdiff import rust_core

        result = rust_core.live_normalise_request_path(path)
        if result["ok"]:
            return result["path"], None
        return "", self._error_response(
            seq, result["code"], result["message"], op="diff"
        )

    def _cancel_request(self, request: dict[str, Any], seq: int) -> dict[str, Any]:
        path_raw = request.get("path")
        cancelled = 0
        with self._lock:
            if isinstance(path_raw, str) and path_raw:
                path, _ = self._normalise_request_path(path_raw, seq)
                timer = self._timers.pop(path, None)
                pending = self._timer_requests.pop(path, None)
                if timer is not None:
                    timer.cancel()
                    cancelled += 1
                stale_after = seq if seq else ((pending.seq + 1) if pending else 1)
                self._latest_seq[path] = max(self._latest_seq.get(path, 0), stale_after)
            elif seq:
                self._latest_review_seq = max(self._latest_review_seq, seq + 1)
                for path, pending in list(self._timer_requests.items()):
                    if pending.seq <= seq:
                        timer = self._timers.pop(path, None)
                        self._timer_requests.pop(path, None)
                        if timer is not None:
                            timer.cancel()
                            cancelled += 1
                        self._latest_seq[path] = max(self._latest_seq.get(path, 0), seq + 1)
        return {"op": "cancel", "seq": seq, "ok": True, "cancelled": cancelled}

    def _is_stale(self, request: _ParsedRequest) -> bool:
        with self._lock:
            return request.seq < self._latest_seq.get(request.path, 0)

    def _limits(self) -> dict[str, int]:
        from intentumdiff import rust_core

        return rust_core.live_limits()

    def _capabilities(self) -> dict[str, Any]:
        from intentumdiff import rust_core

        return rust_core.live_capabilities()

    def _error_response(
        self,
        seq: int,
        code: str,
        message: str,
        *,
        op: str = "error",
    ) -> dict[str, Any]:
        from intentumdiff import rust_core

        return rust_core.live_error_response(seq, code, message, op)


# ---------------------------------------------------------------------------
# Wire helpers
# ---------------------------------------------------------------------------

def _write_line(stream: "Any", obj: dict[str, Any]) -> None:
    """Write *obj* as a JSON line to a text stream."""
    try:
        stream.write(json.dumps(obj, ensure_ascii=True) + "\n")
        stream.flush()
    except OSError:
        pass


def _write_line_bytes(sock: socket.socket, obj: dict[str, Any]) -> None:
    """Write *obj* as a JSON line to a socket."""
    try:
        data = (json.dumps(obj, ensure_ascii=True) + "\n").encode("utf-8")
        sock.sendall(data)
    except OSError:
        pass
