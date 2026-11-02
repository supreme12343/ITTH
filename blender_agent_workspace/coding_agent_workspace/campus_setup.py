import bpy
import math
import mathutils
import random

def safe_get_input(node, name):
    """Helper to safely access node sockets."""
    return node.inputs.get(name) or node.outputs.get(name)

def initialize_campus_scene():
    """
    Initializes scene settings, frame range, render engine, 
    and ensures the Campus_Root object exists.
    """
    try:
        # 1. Scene Setup
        scene = bpy.context.scene
        scene.frame_start = 1
        scene.frame_end = 250
        
        # Set render engine to EEVEE (Blender 5.2 LTS compatible)
        scene.render.engine = 'BLENDER_EEVEE'

        # Ensure Root object exists
        root = bpy.data.objects.get("Campus_Root")
        if not root:
            bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0, 0, 0))
            root = bpy.context.active_object
            root.name = "Campus_Root"
            print("Created Campus_Root.")
        else:
            print("Campus_Root already exists.")
            
        return root

    except Exception as e:
        print(f"Error initializing campus scene: {e}")
        return None

# Execute initialization
if __name__ == "__main__":
    initialize_campus_scene()
