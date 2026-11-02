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
