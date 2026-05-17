"""Session management tools for COMSOL MCP Server."""

import atexit
import os
import socket
import subprocess
import time
from typing import Optional
from mcp.server import Server
from mcp.server.fastmcp import FastMCP
import mph


class SessionManager:
    """Singleton manager for COMSOL client session."""
    
    _instance: Optional["SessionManager"] = None
    _client: Optional[mph.Client] = None
    _models: dict[str, mph.Model] = {}
    _current_model: Optional[str] = None
    _server_process: Optional[subprocess.Popen] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @property
    def client(self) -> Optional[mph.Client]:
        return self._client
    
    @property
    def is_connected(self) -> bool:
        if self._client is None:
            return False
        if self._client.standalone:
            return True
        return self._client.port is not None
    
    @property
    def current_model(self) -> Optional[str]:
        return self._current_model
    
    @property
    def models(self) -> dict[str, mph.Model]:
        return self._models.copy()
    
    def start(self, cores: Optional[int] = None, version: Optional[str] = None) -> dict:
        """Start a COMSOL client session."""
        if self._client is not None:
            try:
                self._client.clear()
                self._models.clear()
                self._current_model = None
                return {
                    "success": True,
                    "version": self._client.version,
                    "cores": self._client.cores,
                    "standalone": self._client.standalone,
                    "message": "Cleared existing session and ready."
                }
            except Exception as e:
                return {"success": False, "error": f"Failed to clear existing session: {e}"}
        try:
            self._client = mph.Client(cores=cores, version=version)
            return {
                "success": True,
                "version": self._client.version,
                "cores": self._client.cores,
                "standalone": self._client.standalone,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def connect(self, port: int, host: str = "localhost") -> dict:
        """Connect to a remote COMSOL server."""
        if self.is_connected:
            return {
                "success": False,
                "error": "COMSOL session already running. Disconnect first."
            }
        try:
            if self._client is None:
                self._client = mph.Client(port=port, host=host)
            else:
                self._client.connect(port, host)
            return {
                "success": True,
                "version": self._client.version,
                "port": port,
                "host": host,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def start_server_and_connect(
        self,
        port: int = 2036,
        host: str = "127.0.0.1",
        comsol_path: Optional[str] = None,
        timeout: float = 45.0,
    ) -> dict:
        """Start a local COMSOL mphserver process and connect to it."""
        if self.is_connected:
            return {
                "success": True,
                "message": "COMSOL client is already connected.",
                "version": self._client.version,
                "host": self._client.host,
                "port": self._client.port,
            }

        comsol_bin = (
            comsol_path
            or os.environ.get("COMSOL_BIN")
            or "/Applications/COMSOL62/Multiphysics/bin/comsol"
        )

        if not os.path.exists(comsol_bin):
            return {"success": False, "error": f"COMSOL executable not found: {comsol_bin}"}

        def port_open() -> bool:
            try:
                with socket.create_connection((host, port), timeout=1.0):
                    return True
            except OSError:
                return False

        started_process = False
        if not port_open():
            command = [
                comsol_bin,
                "mphserver",
                "-port",
                str(port),
                "-login",
                "auto",
                "-multi",
                "on",
                "-silent",
            ]
            env = os.environ.copy()
            env.setdefault("PATH", f"{os.path.dirname(comsol_bin)}:{env.get('PATH', '')}")
            self._server_process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            started_process = True

            deadline = time.time() + timeout
            while time.time() < deadline:
                if self._server_process.poll() is not None:
                    output = ""
                    if self._server_process.stdout is not None:
                        output = self._server_process.stdout.read() or ""
                    return {
                        "success": False,
                        "error": "COMSOL mphserver exited before accepting connections.",
                        "command": command,
                        "output": output[-2000:],
                    }
                if port_open():
                    break
                time.sleep(0.5)

            if not port_open():
                return {
                    "success": False,
                    "error": f"Timed out waiting for COMSOL mphserver on {host}:{port}.",
                    "command": command,
                }

        try:
            if self._client is None:
                self._client = mph.Client(port=port, host=host)
            else:
                self._client.connect(port, host)
            return {
                "success": True,
                "version": self._client.version,
                "port": port,
                "host": host,
                "server_started": started_process,
                "server_pid": self._server_process.pid if self._server_process is not None else None,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "server_started": started_process}

    def stop_server(self, timeout: float = 10.0) -> dict:
        """Terminate the COMSOL mphserver process started by this MCP server."""
        if self._server_process is None:
            return {"success": True, "server_stopped": False, "message": "No MCP-started COMSOL server process is tracked."}

        process = self._server_process
        if process.poll() is not None:
            self._server_process = None
            return {"success": True, "server_stopped": False, "message": "Tracked COMSOL server process had already exited."}

        process.terminate()
        try:
            process.wait(timeout=timeout)
            self._server_process = None
            return {"success": True, "server_stopped": True, "pid": process.pid}
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)
            self._server_process = None
            return {"success": True, "server_stopped": True, "pid": process.pid, "forced": True}
    
    def disconnect(self, stop_server: bool = True) -> dict:
        """Disconnect and clear the session."""
        server_result = None
        try:
            if self._client is not None:
                self._client.clear()
                if not self._client.standalone and self._client.port is not None:
                    self._client.disconnect()
            self._models.clear()
            self._current_model = None

            if stop_server:
                server_result = self.stop_server()

            return {
                "success": True,
                "message": "Session cleared; client disconnected; MCP-started server stopped if present.",
                "server": server_result,
            }
        except Exception as e:
            self._models.clear()
            self._current_model = None
            if stop_server:
                server_result = self.stop_server()
            return {
                "success": True,
                "message": f"Session cleared (error during client cleanup: {e})",
                "server": server_result,
            }

    def shutdown(self) -> None:
        """Best-effort cleanup for interpreter shutdown."""
        try:
            self.disconnect(stop_server=True)
        except Exception:
            pass
    
    def get_status(self) -> dict:
        """Get current session status."""
        if not self.is_connected:
            return {
                "connected": False,
                "message": "No active COMSOL session.",
                "server_tracked": self._server_process is not None and self._server_process.poll() is None,
            }
        
        model_list = []
        for name in self._client.names():
            model_info = {"name": name}
            if name in self._models:
                model = self._models[name]
                model_info["file"] = model.file() if hasattr(model, 'file') else None
            model_list.append(model_info)
        
        return {
            "connected": True,
            "version": self._client.version,
            "cores": self._client.cores,
            "standalone": self._client.standalone,
            "models": model_list,
            "current_model": self._current_model,
        }
    
    def add_model(self, model: mph.Model) -> str:
        """Add a model to tracking."""
        name = model.name()
        self._models[name] = model
        if self._current_model is None:
            self._current_model = name
        return name
    
    def get_model(self, name: Optional[str] = None) -> Optional[mph.Model]:
        """Get a model by name or current model."""
        if name is None:
            name = self._current_model
        return self._models.get(name)
    
    def set_current_model(self, name: str) -> bool:
        """Set the current active model."""
        if name in self._models:
            self._current_model = name
            return True
        return False
    
    def remove_model(self, name: str) -> bool:
        """Remove a model from tracking and client."""
        if name in self._models and self._client is not None:
            try:
                self._client.remove(self._models[name])
                del self._models[name]
                if self._current_model == name:
                    self._current_model = next(iter(self._models.keys()), None)
                return True
            except Exception:
                pass
        return False


session_manager = SessionManager()
atexit.register(session_manager.shutdown)


def register_session_tools(mcp: FastMCP) -> None:
    """Register session management tools with the MCP server."""
    
    @mcp.tool()
    def comsol_start(cores: Optional[int] = None, version: Optional[str] = None) -> dict:
        """
        Start a local COMSOL client session.
        
        Args:
            cores: Number of processor cores to use (default: all available)
            version: COMSOL version to use, e.g., '6.0' (default: latest installed)
        
        Returns:
            Session info including version and core count, or error message
        """
        return session_manager.start(cores=cores, version=version)
    
    @mcp.tool()
    def comsol_connect(port: int, host: str = "localhost") -> dict:
        """
        Connect to a remote COMSOL server.
        
        Args:
            port: Port number the COMSOL server is listening on
            host: Server hostname or IP address (default: 'localhost')
        
        Returns:
            Connection info or error message
        """
        return session_manager.connect(port=port, host=host)

    @mcp.tool()
    def comsol_start_server(
        port: int = 2036,
        host: str = "127.0.0.1",
        comsol_path: Optional[str] = None,
        timeout: float = 45.0,
    ) -> dict:
        """
        Start a local COMSOL mphserver process and connect to it.

        This uses `comsol mphserver -login auto -multi on -silent`, so COMSOL
        must already have local server login information stored in the user's
        COMSOL preferences.

        Args:
            port: Server port to use
            host: Host/IP to connect to after starting the server
            comsol_path: Full path to the COMSOL launcher (default: COMSOL_BIN env or COMSOL 6.2 path)
            timeout: Seconds to wait for the server port to become available

        Returns:
            Connection status and server process information
        """
        return session_manager.start_server_and_connect(
            port=port,
            host=host,
            comsol_path=comsol_path,
            timeout=timeout,
        )
    
    @mcp.tool()
    def comsol_disconnect() -> dict:
        """
        Disconnect from COMSOL, clear all models from memory, and stop any
        COMSOL server process started by this MCP server.
        
        Returns:
            Success status and message
        """
        return session_manager.disconnect(stop_server=True)

    @mcp.tool()
    def comsol_stop_server(timeout: float = 10.0) -> dict:
        """
        Stop the COMSOL mphserver process started by this MCP server.

        Args:
            timeout: Seconds to wait before force-killing the server process

        Returns:
            Stop status
        """
        return session_manager.stop_server(timeout=timeout)
    
    @mcp.tool()
    def comsol_status() -> dict:
        """
        Get the current COMSOL session status.
        
        Returns:
            Session information including connection status, version, and loaded models
        """
        return session_manager.get_status()
