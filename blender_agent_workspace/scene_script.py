# ── 2026-08-05 21:44:24 ────────────────────
import bpy, math, mathutils, random
from mathutils import Vector

def safe_get_input(node, *names):
    if not node or not hasattr(node, "inputs"):
        return None
    for name in names:
        inp = node.inputs.get(name)
        if inp is not None:
            return inp
    return None

def make_material(name, base_color, roughness=0.4, metallic=0.0, emission_color=None, emission_strength=0.0, alpha=1.0):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        b_color = safe_get_input(bsdf, "Base Color", "BaseColor", "Color")
        r_input = safe_get_input(bsdf, "Roughness")
        m_input = safe_get_input(bsdf, "Metallic")
        if b_color: b_color.default_value = (*base_color, 1.0) if len(base_color) == 3 else base_color
        if r_input: r_input.default_value = roughness
        if m_input: m_input.default_value = metallic
        
        if emission_color:
            e_color = safe_get_input(bsdf, "Emission Color", "Emission", "Color")
            e_str   = safe_get_input(bsdf, "Emission Strength", "Strength")
            if e_color: e_color.default_value = (*emission_color, 1.0) if len(emission_color) == 3 else emission_color
            if e_str: e_str.default_value = emission_strength
            
        a_input = safe_get_input(bsdf, "Alpha")
        if a_input: a_input.default_value = alpha
        
    if alpha < 1.0:
        mat.blend_method = 'BLEND'
    return mat

# --- 1. Clear scene ---
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

# --- 2. Frame range ---
scene = bpy.context.scene
scene.frame_start = 1
scene.frame_end = 120
scene.frame_current = 1

# --- 3. Render engine ---
scene.render.engine = 'BLENDER_EEVEE'
for attr in ("use_gtao", "use_bloom", "use_ssr"):
    if hasattr(scene.eevee, attr):
        setattr(scene.eevee, attr, True)

# --- 4. World / Background ---
world = bpy.data.worlds.get("World")
if world:
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        c_input = safe_get_input(bg, "Color")
        s_input = safe_get_input(bg, "Strength")
        if c_input: c_input.default_value = (0.05, 0.05, 0.05, 1)
        if s_input: s_input.default_value = 0.5

# --- 5. Create materials ---
mat_body = make_material("BodyPaint", (0.1, 0.1, 0.1), roughness=0.3, metallic=0.8)
mat_stripes = make_material("Stripes", (0.8, 0.1, 0.1), roughness=0.2, metallic=0.9)
mat_carbon = make_material("CarbonFiber", (0.02, 0.02, 0.02), roughness=0.1, metallic=0.5)
mat_tires = make_material("Rubber", (0.02, 0.02, 0.02), roughness=0.9)
mat_rims = make_material("Rims", (0.8, 0.1, 0.1), roughness=0.3, metallic=1.0)
mat_metal = make_material("Metal", (0.7, 0.7, 0.7), roughness=0.2, metallic=1.0)

# --- 6. Create chassis ---
bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=4.0, location=(0, 0, 0.5))
chassis = bpy.context.active_object
chassis.name = "Chassis"
chassis.scale = (1.5, 0.5, 0.5)
chassis.data.materials.append(mat_body)

# --- 7. Create nose cone ---
bpy.ops.mesh.primitive_cone_add(radius1=0.5, depth=2.0, location=(0, -2.5, 0.5))
nose = bpy.context.active_object
nose.name = "NoseCone"
nose.scale = (0.5, 0.5, 0.5)
nose.data.materials.append(mat_body)

# --- 8. Create front wing ---
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -3.0, 0.5))
front_wing = bpy.context.active_object
front_wing.name = "FrontWing"
front_wing.scale = (2.5, 0.1, 0.05)
front_wing.data.materials.append(mat_carbon)

# --- 9. Create rear wing ---
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 2.5, 0.5))
rear_wing = bpy.context.active_object
rear_wing.name = "RearWing"
rear_wing.scale = (2.0, 0.1, 0.05)
rear_wing.data.materials.append(mat_carbon)

# --- 10. Create wheels ---
wheel_positions = [(-1.5, -2.0, 0.25), (1.5, -2.0, 0.25), (-1.5, 2.0, 0.25), (1.5, 2.0, 0.25)]
for pos in wheel_positions:
    bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=0.3, location=pos)
    wheel = bpy.context.active_object
    wheel.rotation_euler = (math.radians(90), 0, 0)
    wheel.data.materials.append(mat_tires)
    bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=0.1, location=pos)
    rim = bpy.context.active_object
    rim.rotation_euler = (math.radians(90), 0, 0)
    rim.data.materials.append(mat_rims)

# --- 11. Lighting ---
bpy.ops.object.light_add(type='AREA', location=(5, -5, 5))
key_light = bpy.context.active_object
key_light.data.energy = 800
key_light.data.size = 3

bpy.ops.object.light_add(type='POINT', location=(-5, 5, 5))
fill_light = bpy.context.active_object
fill_light.data.energy = 300

bpy.ops.object.light_add(type='SUN', location=(0, 0, 10))
rim_light = bpy.context.active_object
rim_light.data.energy = 2.0

# --- 12. Camera ---
bpy.ops.object.camera_add(location=(7, -7, 3))
camera = bpy.context.active_object
camera.rotation_euler = (math.radians(63), 0, math.radians(46))
camera.data.lens = 35
scene.camera = camera


# ── 2026-10-05 10:57:45 ────────────────────
# File: spatial_analysis.py
import bpy
import mathutils

def get_safe_placement_x(buffer=10.0):
    """
    Calculates a safe X-coordinate for new objects by finding the 
    maximum X extent of all existing mesh objects in the scene.
    """
    max_x = 0.0
    found_mesh = False
    
    for obj in bpy.data.objects:
        if obj.type == 'MESH':
            # Ensure the object is evaluated to get correct world-space bounds
            # even if modifiers are present
            try:
                # Get world-space bounding box corners
                bbox = [obj.matrix_world @ mathutils.Vector(corner) for corner in obj.bound_box]
                for v in bbox:
                    if v.x > max_x:
                        max_x = v.x
                found_mesh = True
            except Exception as e:
                print(f"Could not calculate bounds for {obj.name}: {e}")
                
    return max_x + buffer if found_mesh else buffer

if __name__ == "__main__":
    safe_x = get_safe_placement_x()
    print(f"Calculated safe X placement: {safe_x}")


# File: mesh_creation.py
import bpy
import math
import mathutils
import random

def safe_get_input(node, socket_name):
    """Helper to safely access node sockets."""
    return node.inputs.get(socket_name)

def run_campus_setup():
    try:
        # 1. Initialize Scene Settings
        scene = bpy.context.scene
        scene.frame_start = 1
        scene.frame_end = 250
        
        # Set render engine with fallback
        try:
            scene.render.engine = 'BLENDER_EEVEE'
        except:
            scene.render.engine = 'CYCLES'

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
            mat_name = "Campus_Base_Mat"
            if mat_name not in bpy.data.materials:
                mat = bpy.data.materials.new(name=mat_name)
                mat.use_nodes = True
                nodes = mat.node_tree.nodes
                bsdf = nodes.get("Principled BSDF")
                if bsdf:
                    base_color = safe_get_input(bsdf, "Base Color")
                    if base_color:
                        base_color.default_value = (0.2, 0.5, 0.8, 1.0)
            else:
                mat = bpy.data.materials[mat_name]
            
            if building.data.materials:
                building.data.materials[0] = mat
            else:
                building.data.materials.append(mat)
        
        print("Campus base addition complete.")

    except Exception as e:
        print(f"Error during campus setup: {e}")

run_campus_setup()


# File: setup_scene.py
import bpy
import math
import mathutils
import random

def safe_get_input(node, socket_name):
    """Helper to safely access node sockets."""
    return node.inputs.get(socket_name)

def run_campus_setup():
    try:
        # 1. Initialize Scene Settings
        scene = bpy.context.scene
        scene.frame_start = 1
        scene.frame_end = 250
        
        # Set render engine with safe fallback
        # Blender 5.2 LTS supports 'BLENDER_EEVEE' and 'CYCLES'
        try:
            scene.render.engine = 'BLENDER_EEVEE'
        except Exception:
            scene.render.engine = 'CYCLES'

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
                # Calculate world space bounding box
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
            mat_name = "Campus_Base_Mat"
            if mat_name not in bpy.data.materials:
                mat = bpy.data.materials.new(name=mat_name)
                mat.use_nodes = True
                nodes = mat.node_tree.nodes
                bsdf = nodes.get("Principled BSDF")
                if bsdf:
                    base_color = safe_get_input(bsdf, "Base Color")
                    if base_color:
                        base_color.default_value = (0.2, 0.5, 0.8, 1.0)
            else:
                mat = bpy.data.materials[mat_name]
            
            if building.data.materials:
                building.data.materials[0] = mat
            else:
                building.data.materials.append(mat)
        
        print("Campus base addition complete.")

    except Exception as e:
        print(f"Error during campus setup: {e}")

if __name__ == "__main__":
    run_campus_setup()


# File: material_management.py
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
