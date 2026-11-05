"""Windows tools exposed through the standard MCP Streamable HTTP transport."""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field


class VolumeResult(BaseModel):
    percent: int


def windows_set_volume(percent):
    from comtypes import CoInitialize, CoUninitialize
    from pycaw.pycaw import AudioUtilities

    CoInitialize()
    try:
        endpoint = AudioUtilities.GetSpeakers().EndpointVolume
        endpoint.SetMasterVolumeLevelScalar(percent / 100, None)
        return round(endpoint.GetMasterVolumeLevelScalar() * 100)
    finally:
        endpoint = None
        CoUninitialize()


def create_device_tools(set_master_volume=windows_set_volume):
    mcp = FastMCP("Windows 设备工具", host="127.0.0.1", port=18766,
                  stateless_http=True, json_response=True)

    @mcp.tool()
    def set_volume(percent: Annotated[int, Field(strict=True, ge=0, le=100)]) -> VolumeResult:
        """将这台 Windows 电脑的默认播放设备主音量设为指定百分比。用户未指定百分比时先询问。"""
        return VolumeResult(percent=set_master_volume(percent))

    return mcp
