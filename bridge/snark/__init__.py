"""Слой 3: интеграция с i2psnark (RPC + веб-интерфейс)."""

from .integration import SnarkIntegration, VideoTorrentHandle
from .rpc_client import RPCClient, RPCError
from .web_client import I2PSnarkWebClient, WebClientError, TorrentMustBeStoppedError

__all__ = [
    "SnarkIntegration",
    "VideoTorrentHandle",
    "RPCClient",
    "RPCError",
    "I2PSnarkWebClient",
    "WebClientError",
    "TorrentMustBeStoppedError",
]
