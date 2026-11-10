"""
comfyui-hervinai-none
入口文件。ComfyUI 加载自定义节点包时会执行本文件，
并从模块级读取 NODE_CLASS_MAPPINGS / NODE_DISPLAY_NAME_MAPPINGS。

另外通过 WEB_DIRECTORY 把 web/ 目录注册到前端，
让前端补丁（默认空输入）生效。
"""

import os

from .hervinai_none_nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

# 便于在 ComfyUI 的自定义节点列表 / 日志里识别本包
__version__ = "1.0.0"

# 前端扩展目录：ComfyUI 会把这里的内容挂到 web/ 目录下供前端加载
WEB_DIRECTORY = "./web"
