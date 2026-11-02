import bpy
import math
import mathutils
import random

def safe_get_input(node, name):
    """Helper to safely access node sockets."""
    return node.inputs.get(name) or node.outputs.get(name)

try:
    # 1. Scene Setup
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = 250
    scene.render.engine = 'BLENDER_EEVEE'

    # Ensure Root
    root = bpy.data.objects.get("Campus_Root")
    if not root:
        bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0, 0, 0))
        root = bpy.context.active_object
        root.name = "Campus_Root"

    # 2. Create Building Wings
    def create_wing(name, location):
        bpy.ops.mesh.primitive_cube_add(size=2, location=location)
        obj = bpy.context.active_object
        obj.name = name
        obj.scale = (3, 2, 1.5)
        obj.parent = root
        
        # Add Bevel
        mod = obj.modifiers.new(name="Bevel", type='BEVEL')
        mod.width = 0.1
        return obj

    wing_left = create_wing("Campus_Wing_Left", (16, 0, 1.5))
    wing_right = create_wing("Campus_Wing_Right", (27.8, 0, 1.5))

    # 3. Create Roof
    bpy.ops.mesh.primitive_cone_add(vertices=4, radius1=4, depth=2, location=(21.93, 0, 3.5))
    roof = bpy.context.active_object
    roof.name = "Campus_Roof"
    roof.rotation_euler = (0, 0, math.radians(45))
    roof.scale = (1.2, 0.8, 0.5)
    roof.parent = root

    # 4. Materials
    mat_name = "Campus_Material"
    mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        base_color = safe_get_input(bsdf, "Base Color")
        if base_color:
            base_color.default_value = (0.8, 0.7, 0.6, 1)

    for obj in [wing_left, wing_right, roof]:
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)

    # 5. Lighting and Camera
    # Remove existing if needed to avoid duplicates
    if bpy.data.objects.get("Campus_Sun"):
        bpy.data.objects.remove(bpy.data.objects["Campus_Sun"], do_unlink=True)
    
    bpy.ops.object.light_add(type='SUN', location=(10, -10, 20))
    sun = bpy.context.active_object
    sun.name = "Campus_Sun"
    sun.data.energy = 5.0

    if bpy.data.objects.get("Campus_Cam"):
        bpy.data.objects.remove(bpy.data.objects["Campus_Cam"], do_unlink=True)
        
    bpy.ops.object.camera_add(location=(40, -20, 15))
    cam = bpy.context.active_object
    cam.name = "Campus_Cam"
    # Point camera at the building base
    direction = mathutils.Vector((21.93, 0, 1.0)) - mathutils.Vector((40, -20, 15))
    rot_quat = direction.to_track_quat('-Z', 'Y')
    cam.rotation_euler = rot_quat.to_euler()
    
    scene.camera = cam

    print("Campus environment generated successfully.")

except Exception as e:
    print(f"Error generating campus environment: {e}")
