class WebullAgentError(Exception):
    """Base exception for safe, user-facing failures."""


class ConfigurationError(WebullAgentError):
    """Configuration is missing or invalid."""


class MCPTransportError(WebullAgentError):
    """The MCP server could not complete a read operation."""


class MCPResponseError(WebullAgentError):
    """The MCP server returned an unexpected response."""
