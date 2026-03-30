"""SIM (Stigmergic-Immune Morphogenetic) multi-agent software development framework."""

__all__ = ["__version__"]

try:
    from importlib.metadata import version

    __version__ = version("sim-agents")
except Exception:
    __version__ = "0.1.0-dev"
