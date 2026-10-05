"""Innstillinger fra miljøvariabler (se Konfigurasjon i SPEC.md)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(verdi: str) -> bool:
    return verdi.strip().lower() in ("1", "true", "ja", "yes", "on")


@dataclass
class Config:
    mcp_token: str = ""
    port: int = 8765
    data_dir: str = "/data"
    config_dir: str = "/config"
    win_prefix: str = "Z:\\"
    unc_prefix: str = "\\\\192.168.1.215\\blender"
    ollama_url: str = ""
    ntfy_url: str = ""
    max_job_hours: float = 12
    max_frame_minutes: float = 60
    min_free_gb: float = 50
    min_free_vram_gb: float = 6
    allow_cpu: bool = False
    blender_bin: str = "blender"
    app_dir: str = field(default_factory=lambda: os.path.dirname(os.path.abspath(__file__)))

    @property
    def ko_db(self) -> str:
        return os.path.join(self.config_dir, "ko.db")

    @property
    def render_dir(self) -> str:
        return os.path.join(self.data_dir, "render")

    @classmethod
    def fra_miljo(cls, miljo: dict[str, str] | None = None) -> "Config":
        m = os.environ if miljo is None else miljo
        c = cls()
        c.mcp_token = m.get("MCP_TOKEN", "").strip()
        c.port = int(m.get("PORT", c.port))
        c.data_dir = m.get("DATA_DIR", c.data_dir)
        c.config_dir = m.get("CONFIG_DIR", c.config_dir)
        c.win_prefix = m.get("WIN_PREFIX", c.win_prefix)
        c.unc_prefix = m.get("UNC_PREFIX", c.unc_prefix)
        c.ollama_url = m.get("OLLAMA_URL", "").strip().rstrip("/")
        c.ntfy_url = m.get("NTFY_URL", "").strip()
        c.max_job_hours = float(m.get("MAX_JOB_HOURS", c.max_job_hours))
        c.max_frame_minutes = float(m.get("MAX_FRAME_MINUTES", c.max_frame_minutes))
        c.min_free_gb = float(m.get("MIN_FREE_GB", c.min_free_gb))
        c.min_free_vram_gb = float(m.get("MIN_FREE_VRAM_GB", c.min_free_vram_gb))
        c.allow_cpu = _bool(m.get("ALLOW_CPU", "false"))
        c.blender_bin = m.get("BLENDER_BIN", c.blender_bin)
        return c
