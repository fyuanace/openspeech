"""Expose an approved subset of a Streamable HTTP MCP server to S2S."""

import asyncio
import hashlib
import json
import re
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from referencing import Registry

TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,60}")
PREFIX = "mcp_"
MAX_TOOLS = 16
MAX_SERVERS = 8
MAX_ENABLED_TOOLS = 64


def configured_servers(config):
    if "mcp_servers" in config:
        return config["mcp_servers"]
    legacy = config.get("mcp", {})
    if legacy.get("url"):
        return {"default": {**legacy, "name": urlsplit(legacy["url"]).netloc or "MCP 服务器",
                            "enabled": True}}
    return {}


def validate_url(url):
    address = urlsplit(url)
    if (not address.hostname or address.username is not None or address.password is not None
            or address.query or address.fragment
            or address.scheme != "https" and not (
                address.scheme == "http" and address.hostname in {"localhost", "127.0.0.1", "::1"}
            )):
        raise ValueError("MCP 地址需为 HTTPS，或使用本机 HTTP。请把凭据单独填入令牌字段。")
    _ = address.port


class McpTools:
    def __init__(self, config, server_id="default"):
        self.url = config.get("url", "")
        self.token = config.get("token", "")
        self.allowed = set(config.get("tools", "").split()) if config.get("enabled", True) else set()
        self.label = config.get("name", "")
        self.namespace = f"{PREFIX}{server_id}_"
        self.registered = {}

    @asynccontextmanager
    async def session(self):
        validate_url(self.url)
        headers = {"Authorization": "Bearer " + self.token} if self.token else {}
        async with httpx.AsyncClient(headers=headers, follow_redirects=False, trust_env=False) as client:
            async with streamable_http_client(self.url, http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

    async def discover(self):
        tools = {}
        async with asyncio.timeout(5), self.session() as session:
            cursor = None
            for _ in range(8):
                page = await session.list_tools(cursor=cursor)
                for tool in page.tools:
                    schema = tool.inputSchema
                    if (TOOL_NAME.fullmatch(tool.name) and schema.get("type") == "object"
                            and len(json.dumps(schema)) <= 8192):
                        tools[tool.name] = tool
                cursor = page.nextCursor
                if not cursor:
                    break
        return tools

    async def definitions(self):
        if not self.url or not self.allowed:
            return []
        try:
            tools = await self.discover()
        except Exception:
            return []
        definitions = []
        for name in sorted(self.allowed.intersection(tools))[:MAX_TOOLS]:
            tool = tools[name]
            model_name = self.namespace + "t_" + name
            if len(model_name) > 64:
                digest = hashlib.sha256(name.encode()).hexdigest()[:12]
                model_name = self.namespace + "h_" + name[:49 - len(self.namespace)] + "_" + digest
            self.registered[model_name] = (name, tool.inputSchema)
            description = tool.description or name
            if self.label:
                description = f"由「{self.label}」提供。{description}"
            definitions.append({"type": "function", "name": model_name,
                                "description": description[:500],
                                "parameters": tool.inputSchema})
        return definitions

    async def call(self, name, arguments):
        try:
            tool_name, schema = self.registered[name]
            if not Draft202012Validator(schema, registry=Registry()).is_valid(arguments):
                return {"ok": False, "error": "工具参数不符合定义。"}
            async with asyncio.timeout(12), self.session() as session:
                result = await session.call_tool(tool_name, arguments)
            if result.isError:
                return {"ok": False, "error": "MCP 工具报告执行失败。"}
            if result.structuredContent is not None:
                data = result.structuredContent
                if len(json.dumps(data, ensure_ascii=False)) <= 6000:
                    return {"ok": True, "data": data}
                return {"ok": False, "error": "工具结果过长。"}
            text = "\n".join(item.text for item in result.content if item.type == "text")
            return {"ok": True, "text": text[:6000]}
        except Exception:
            return {"ok": False, "error": "MCP 工具无法确认执行结果。"}
