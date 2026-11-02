import bpy
import math
import mathutils
import random

def safe_get_input(node, socket_name):
    """Helper to safely access node sockets."""
    return node.inputs.get(socket_name)

def setup_campus_material():
    """Creates or retrieves the Campus_Base_Mat and returns it."""
    mat_name = "Campus_Base_Mat"
    if mat_name in bpy.data.materials:
        return bpy.data.materials[mat_name]
    
    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    
    if bsdf:
        base_color = safe_get_input(bsdf, "Base Color")
        if base_color:
            base_color.default_value = (0.2, 0.5, 0.8, 1.0)
            
    return mat

def run_campus_setup():
    try:
        # 1. Initialize Scene Settings
        scene = bpy.context.scene
        scene.frame_start = 1
        scene.frame_end = 250
        
        # Set render engine
        scene.render.engine = 'BLENDER_EEVEE'

        # 2. Inspect and Organize
        if "Campus_Root" not in bpy.data.objects:
            campus_root = bpy.data.objects.new("Campus_Root", None)
            scene.collection.objects.link(campus_root)
        else:
            campus_root = bpy.data.objects["Campus_Root"]

        # 3. Choose placement (find max X extent of existing objects)
        max_x = 0
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                bbox = [obj.matrix_world @ mathutils.Vector(corner) for corner in obj.bound_box]
                for v in bbox:
                    if v.x > max_x:
                        max_x = v.x
        
        placement_x = max_x + 10.0

        # 4. Create base addition
        if "Campus_Building_Base" not in bpy.data.objects:
            bpy.ops.mesh.primitive_cube_add(size=2, location=(placement_x, 0, 1))
            building = bpy.context.active_object
            building.name = "Campus_Building_Base"
            building.scale = (3, 2, 4)
            building.parent = campus_root
            
            # 5. Assign Material
            mat = setup_campus_material()
            
            if building.data.materials:
                building.data.materials[0] = mat
            else:
                building.data.materials.append(mat)
        
        print("Campus base addition complete.")

    except Exception as e:
        print(f"Error during campus setup: {e}")

if __name__ == "__main__":
    run_campus_setup()
