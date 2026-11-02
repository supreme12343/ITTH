bl_info = {
    "name": "Blender MCP (vendored for Campus Suite)",
    "author": "Siddharth Ahuja (vendored)",
    "version": (1, 2),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "Connect Blender to Campus Suite blender_agent.py over localhost:9876",
    "category": "Interface",
}

from . import addon

def register():
    addon.register()

def unregister():
    addon.unregister()